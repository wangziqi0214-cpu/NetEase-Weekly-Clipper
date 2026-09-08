"""Local Artist Knowledge Library: Background bounded persistent collection using agy.

Collects factual artist background, verified sources, uncertainty assessment,
and identity context locally. Never fabricates sources or treats missing info as rejection.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set

from song_discovery.db import DiscoveryDB

logger = logging.getLogger("song_discovery.artist_knowledge")

# Multi-source web research can legitimately take several minutes when AGy
# opens a mix of search results and public artist pages.  Keep this bounded,
# but do not turn a slow search into a false "no information" result.
DEFAULT_AGY_TIMEOUT_SECONDS = int(os.environ.get("ARTIST_AGY_TIMEOUT_SECONDS", "600"))
DEFAULT_CONCURRENCY = 2
DEFAULT_MAX_PENDING = 2000
DEFAULT_SPARSE_RECHECK_SECONDS = 7 * 24 * 60 * 60


def _ascii_log_value(value: Any) -> str:
    """Return an ASCII-safe representation for LaunchAgent/stdout logging."""
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


def normalize_artist_name(name: str) -> str:
    """Normalize artist name for library keying."""
    if not name:
        return ""
    return " ".join(name.strip().split())


def artist_lookup_key(name: str) -> str:
    """Stable comparison key for case, whitespace, and hyphen variants."""
    return re.sub(r"[\s\-]+", "", normalize_artist_name(name).casefold())


def artist_knowledge_needs_refresh(record: Optional[Dict[str, Any]]) -> bool:
    """Return whether a cached result should be retried instead of finalised.

    Sourceless sparse results are displayed as provisional, not as permanent
    negatives.  They are retried on the normal weekly cadence; failed records
    and interrupted pending records are always eligible for retry.
    """
    if not record:
        return True
    status = str(record.get("status") or "")
    if status in ("failed", "pending"):
        return True
    if status != "sparse":
        return False
    if record.get("sources"):
        return False
    checked = str(record.get("last_checked_at") or record.get("updated_at") or "")
    if not checked:
        return True
    try:
        checked_at = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - checked_at).total_seconds()
        threshold = int(os.environ.get("ARTIST_SPARSE_RECHECK_SECONDS", DEFAULT_SPARSE_RECHECK_SECONDS))
        return age_seconds >= threshold
    except ValueError:
        return True


def generate_artist_name_variants(artist_name: str) -> List[str]:
    """Generate casing, space, punctuation, and transliteration variants for search."""
    if not artist_name:
        return []
    cleaned = " ".join(artist_name.strip().split())
    variants: List[str] = [cleaned]

    # Title Cased variant
    title_cased = cleaned.title()
    if title_cased not in variants:
        variants.append(title_cased)

    # Lowercase variant
    lowered = cleaned.lower()
    if lowered not in variants:
        variants.append(lowered)

    # Space vs hyphen variants
    if "-" in cleaned:
        hyphen_to_space = " ".join(cleaned.replace("-", " ").split())
        if hyphen_to_space not in variants:
            variants.append(hyphen_to_space)
    if " " in cleaned:
        space_to_hyphen = re.sub(r"\s+", "-", cleaned)
        if space_to_hyphen not in variants:
            variants.append(space_to_hyphen)

    # Specific common music compound variants: Byebye vs Bye Bye
    if re.search(r"\bbyebye\b", cleaned, re.IGNORECASE):
        byebye_variant = re.sub(r"\bbyebye\b", "Bye Bye", cleaned, flags=re.IGNORECASE)
        if byebye_variant not in variants:
            variants.append(byebye_variant)
    if re.search(r"\bbye\s+bye\b", cleaned, re.IGNORECASE):
        byebye_join = re.sub(r"\bbye\s+bye\b", "Byebye", cleaned, flags=re.IGNORECASE)
        if byebye_join not in variants:
            variants.append(byebye_join)

    # Parentheses alias extraction: e.g. "李幸倪 (Gin Lee)" -> "李幸倪", "Gin Lee"
    paren_match = re.match(r"^(.+?)\s*[\(\（](.+?)[\)\）]\s*$", cleaned)
    if paren_match:
        main_part = paren_match.group(1).strip()
        alias_part = paren_match.group(2).strip()
        if main_part and main_part not in variants:
            variants.append(main_part)
        if alias_part and alias_part not in variants:
            variants.append(alias_part)

    return variants


def split_artist_names(artist_names_str: str) -> List[str]:
    """Split compound artist string into individual artist names."""
    if not artist_names_str:
        return []
    # Split on common separators: / 、 & feat. ft. vs. , ， ;
    raw_tokens = re.split(
        r"\s*(?:/|、|&|(?:\bfeat\.?\b)|(?:\bft\.?\b)|(?:\bvs\.?\b)|,|，|;)\s*",
        artist_names_str,
        flags=re.IGNORECASE,
    )
    results = []
    seen = set()
    for token in raw_tokens:
        cleaned = " ".join(token.strip().split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            results.append(cleaned)
    return results or [artist_names_str.strip()]


def find_agy_executable() -> Optional[str]:
    """Locate the agy executable in PATH or standard environment locations."""
    which_path = shutil.which("agy")
    if which_path and os.path.isfile(which_path):
        return which_path

    env_path = os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    if env_path and os.path.isfile(env_path):
        return env_path

    fallback_candidates = [
        "/Users/oshiki/.local/bin/agy",
        "/usr/local/bin/agy",
        os.path.expanduser("~/.local/bin/agy"),
    ]
    for candidate in fallback_candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def extract_json_payload(raw_output: str) -> Optional[Dict[str, Any]]:
    """Robustly extract a JSON dictionary from agy command output."""
    if not raw_output or not raw_output.strip():
        return None

    cleaned = raw_output.strip()

    # Case 1: Outer agy JSON wrapper
    try:
        outer = json.loads(cleaned)
        if isinstance(outer, dict):
            # Check for CLI failure status
            if outer.get("status") == "FAILED":
                logger.warning("agy returned FAILED status: %s", outer.get("error"))
                return None
            if outer.get("denied_actions") and not outer.get("response"):
                logger.warning("agy execution had denied actions without response: %s", outer.get("denied_actions"))
                return None
            if "response" in outer:
                inner_text = outer["response"]
                if isinstance(inner_text, dict):
                    return inner_text
                inner_str = str(inner_text).strip()
                if not inner_str:
                    return None
                cleaned = inner_str
    except json.JSONDecodeError:
        pass

    # Case 2: Markdown code fence ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if match:
        fence_content = match.group(1).strip()
        try:
            val = json.loads(fence_content)
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass

    # Case 3: Raw JSON object spanning { ... }
    brace_match = re.search(r"(\{[\s\S]*\})", cleaned)
    if brace_match:
        try:
            val = json.loads(brace_match.group(1).strip())
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass

    return None


def sanitize_sources(sources_raw: Any) -> List[str]:
    """Ensure sources are real, non-fabricated HTTP/HTTPS URLs across diverse music platforms."""
    if not sources_raw:
        return []

    items: List[str] = []
    if isinstance(sources_raw, list):
        for entry in sources_raw:
            if isinstance(entry, str):
                items.append(entry)
            elif isinstance(entry, dict):
                for key in ("url", "link", "source", "href", "uri"):
                    val = entry.get(key)
                    if isinstance(val, str):
                        items.append(val)
                        break
    elif isinstance(sources_raw, str):
        items.append(sources_raw)
    elif isinstance(sources_raw, dict):
        for val in sources_raw.values():
            if isinstance(val, str):
                items.append(val)

    url_pattern = re.compile(r"https?://[^\s<>'\"`{}|\\^]+", re.IGNORECASE)
    cleaned: List[str] = []
    seen: Set[str] = set()

    dummy_domains = (
        "example.com",
        "example.org",
        "fake.com",
        "fakeurl.com",
        "placeholder.com",
        "dummy.com",
        "localhost",
    )

    for item in items:
        found_urls = url_pattern.findall(item)
        for url in found_urls:
            # Strip trailing punctuation often left by markdown or surrounding sentences
            url = re.sub(r"[.,;:)\]\"'>]+$", "", url).strip()
            if not url:
                continue

            lowered = url.lower()
            if not (lowered.startswith("http://") or lowered.startswith("https://")):
                continue

            parsed = urllib.parse.urlparse(url)
            netloc = parsed.netloc.lower()
            if not netloc or "." not in netloc:
                continue

            if any(netloc == d or netloc.endswith("." + d) for d in dummy_domains):
                continue
            if "fake" in netloc:
                continue

            if url not in seen:
                seen.add(url)
                cleaned.append(url)

    return cleaned


def remove_definitively_dead_sources(sources: List[str], timeout: int = 5) -> List[str]:
    """Drop only sources that respond with a definitive 404/410.

    Private/region-gated pages often return 403, 429, or time out, so those are
    retained as valid leads instead of being mistaken for missing information.
    This lightweight check prevents a hallucinated or stale artist ID from
    being presented as a working source without making the collector dependent
    on every site being crawlable.
    """
    verified: List[str] = []
    for url in sources:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NetEaseWeeklyClipper/1.0)"},
                method="HEAD",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if int(getattr(response, "status", 200)) not in (404, 410):
                    verified.append(url)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                verified.append(url)
        except (urllib.error.URLError, TimeoutError, OSError):
            # A network failure is not evidence that the source is fake.
            verified.append(url)
    return verified


def build_search_prompt_step1(artist_name: str) -> str:
    variants = generate_artist_name_variants(artist_name)
    variants_str = ", ".join(f"'{v}'" for v in variants if v != artist_name)
    variant_hint = f"\nAlso consider name variants (case, spacing, hyphens, English/Chinese names): {variants_str}." if variants_str else ""

    return (
        f"You are a factual music knowledge researcher. Research the musical artist or band: '{artist_name}'.{variant_hint}\n"
        "Search the web for factual public information across Google search, streaming/social profiles (Spotify, Instagram, YouTube), and reference/indie sites (Wikipedia, StreetVoice, Bandcamp, Douban, media articles). Try the exact quoted name, then <name> band, <name> music, <name> China/Taiwan, plus site:instagram.com, site:open.spotify.com, site:youtube.com, site:streetvoice.cn, site:bandcamp.com, and site:wikipedia.org searches. Do not stop after one platform has no result. Use search snippets when a page is blocked; do not browse indefinitely—return the best verified links and facts promptly.\n\n"
        "Output ONLY a valid JSON object with the following structure:\n"
        "{\n"
        '  "display_name": "Official or standard stylized name (e.g. Schoolgirl Byebye)",\n'
        '  "factual_summary": "2-4 sentences factual Chinese summary of background, debut year, musical style, milestone works.",\n'
        '  "sources": ["real URLs copied from public search results"],\n'
        '  "uncertainty": "low" | "medium" | "high",\n'
        '  "identity_context": {\n'
        '    "genre": ["..."],\n'
        '    "origin": "...",\n'
        '    "type": "Band" | "Solo" | "Duo" | "Producer" | "Project" | "Unknown",\n'
        '    "members": ["..."],\n'
        '    "active_years": "...",\n'
        '    "notable_works": ["..."]\n'
        "  },\n"
        '  "is_sparse": false\n'
        "}\n\n"
        "CRITICAL RULES:\n"
        "1. Sources MUST be actual URLs you opened or saw in public search results (Google search, Wikipedia, Spotify, Instagram, YouTube, StreetVoice, Bandcamp, Douban, official sites, media articles). NEVER invent domains, IDs, query URLs, or placeholder paths. If no verified URL is found, return: \"sources\": [].\n"
        "2. Cross-check identity using the song, album, label, city, or member names before merging small independent pages. If public info is verified across multiple sources, set uncertainty to 'low' or 'medium'. Only after broad searches return almost no attributable records should you set \"is_sparse\": true and \"uncertainty\": \"high\". Missing info about indie/underground artists is completely normal and never a negative evaluation.\n"
        "3. Output strict JSON only.\n"
    )


def build_search_prompt_step2(artist_name: str, song_title: str, album_title: str) -> str:
    variants = generate_artist_name_variants(artist_name)
    variants_str = ", ".join(f"'{v}'" for v in variants if v != artist_name)
    variant_hint = f" (variants: {variants_str})" if variants_str else ""

    return (
        f"Initial search for artist '{artist_name}'{variant_hint} yielded sparse public records.\n"
        f"Now conduct targeted deep search using the specific release context:\n"
        f"- Artist: '{artist_name}'\n"
        f"- Song: '{song_title}'\n"
        f"- Album: '{album_title}'\n\n"
        "SEARCH INSTRUCTIONS:\n"
        "1. Search for this specific track or album release on Spotify, NetEase, KKBOX, StreetVoice, Bandcamp, Apple Music, YouTube, and indie review articles. Also try quoted combinations of the artist + song/album and site-specific searches.\n"
        "2. Inspect songwriter credits, record label, producer notes, liner notes, and festival programs; use those details to disambiguate the artist.\n"
        "3. Synthesize verified release and artist context into a 2-4 sentence Chinese summary.\n\n"
        "OUTPUT FORMAT (STRICT JSON ONLY):\n"
        "{\n"
        '  "display_name": "...",\n'
        '  "factual_summary": "2-4 sentences factual Chinese summary.",\n'
        '  "sources": ["real URLs copied from public search results"],\n'
        '  "uncertainty": "low" | "medium" | "high",\n'
        '  "identity_context": {\n'
        '    "genre": ["..."],\n'
        '    "origin": "...",\n'
        '    "type": "Band" | "Solo" | "Duo" | "Producer" | "Project" | "Unknown",\n'
        '    "members": ["..."],\n'
        '    "active_years": "...",\n'
        '    "notable_works": ["..."]\n'
        "  }\n"
        "}\n\n"
        "CRITICAL RULES:\n"
        "1. Include only exact URLs opened or seen in search (Spotify, StreetVoice, Bandcamp, media reviews, streaming pages). NEVER invent domains, IDs, query URLs, or placeholder paths.\n"
        "2. If still sparse after the targeted searches, record whatever limited facts are verified and set uncertainty to 'high'.\n"
        "3. Output valid JSON only.\n"
    )


def default_agy_runner(cmd: List[str], timeout: int) -> str:
    """Run agy subprocess with bounded timeout and return stdout."""
    env = dict(os.environ)
    local_bin = os.path.expanduser("~/.local/bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # ``UTF-8`` by itself is an encoding name, not a portable locale.  Under
    # launchd this value made the agy child fall back to an ASCII codec for
    # some CJK argv/prompt values.  C.UTF-8 is available on macOS/Linux and
    # keeps the child locale internally consistent.
    env["LC_ALL"] = "C.UTF-8"
    env["LC_CTYPE"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"

    run_cmd = list(cmd)
    prompt_text = _extract_agy_prompt_arg(run_cmd)
    prompt_file = None
    # Keep Unicode prompts out of the parent process' argv even when Python
    # reports a UTF-8 filesystem encoding.  launchd can start this daemon in a
    # locale that disagrees with Python's cached encoding; in that situation
    # the direct exec path still raises an ASCII codec error before agy gets
    # the request.  The tiny shell wrapper receives only ASCII argv, reads the
    # UTF-8 prompt from disk, and then invokes agy with a consistent locale.
    if prompt_text is not None and (
        _argv_needs_unicode_safe_wrapper(run_cmd) or _contains_non_ascii(prompt_text)
    ):
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="artist_agy_prompt_",
            suffix=".txt",
            delete=False,
        )
        try:
            prompt_file.write(prompt_text)
            prompt_file.flush()
        finally:
            prompt_file.close()
        run_cmd = _build_prompt_file_shell_command(run_cmd, prompt_file.name)

    proc = subprocess.run(
        run_cmd,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    try:
        stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else str(proc.stdout or "")
        stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
    finally:
        if prompt_file is not None:
            try:
                os.unlink(prompt_file.name)
            except OSError:
                pass

    if proc.returncode != 0:
        raise RuntimeError(f"agy exited with code {proc.returncode}: {(stderr.strip() or stdout.strip())}")
    return stdout


def _extract_agy_prompt_arg(cmd: List[str]) -> Optional[str]:
    for flag in ("-p", "--print", "--prompt"):
        if flag in cmd:
            idx = cmd.index(flag)
            if idx + 1 < len(cmd):
                return str(cmd[idx + 1])
    return None


def _argv_needs_unicode_safe_wrapper(cmd: List[str]) -> bool:
    filesystem_encoding = sys.getfilesystemencoding() or "utf-8"
    for arg in cmd:
        try:
            str(arg).encode(filesystem_encoding)
        except UnicodeEncodeError:
            return True
    return False


def _contains_non_ascii(value: str) -> bool:
    """Return whether *value* contains characters outside ASCII."""
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def _build_prompt_file_shell_command(cmd: List[str], prompt_path: str) -> List[str]:
    """Build an ASCII argv wrapper that reads the Unicode prompt from disk.

    In launchd/ASCII locales, Python can fail before spawning agy because
    execve argv encoding uses the parent process filesystem encoding.  Keep
    argv ASCII-only and let a UTF-8 shell process read the prompt file.
    """
    if not cmd:
        return cmd
    executable = cmd[0]
    remaining: List[str] = []
    skip_next = False
    for idx, arg in enumerate(cmd[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if arg in ("-p", "--print", "--prompt") and idx + 1 < len(cmd):
            skip_next = True
            continue
        remaining.append(str(arg))
    shell_script = (
        "set -eu\n"
        "agy_exe=$1\n"
        "prompt_file=$2\n"
        "shift 2\n"
        "prompt=$(cat \"$prompt_file\")\n"
        "exec \"$agy_exe\" -p \"$prompt\" \"$@\"\n"
    )
    return ["/bin/sh", "-c", shell_script, "artist-agy-runner", executable, prompt_path, *remaining]


def run_artist_research(
    artist_name: str,
    song_title: str = "",
    album_title: str = "",
    timeout: int = DEFAULT_AGY_TIMEOUT_SECONDS,
    agy_runner: Optional[Callable[[List[str], int], str]] = None,
) -> Dict[str, Any]:
    """
    Research artist knowledge using agy subprocess:
    1. First search artist name with multi-source coverage and variants.
    2. If result is sparse, search song/album plus artist.
    3. Return validated knowledge dictionary with factual summary, real sources, uncertainty, identity context.
    Search failures raise RuntimeError and are never disguised as 'no public info'.
    """
    runner = agy_runner or default_agy_runner
    agy_bin = find_agy_executable()
    if not agy_bin and not agy_runner:
        raise FileNotFoundError("agy executable not found in PATH or environment")

    executable = agy_bin or "agy"
    search_queries: List[str] = [artist_name]

    # Step 1: Artist name search with multi-source coverage & dangerously-skip-permissions for headless tools
    cmd_step1 = [
        executable,
        "-p",
        build_search_prompt_step1(artist_name),
        "--model",
        "gemini-3.8-flash-high",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--print-timeout",
        "10m",
    ]
    logger.info("Executing agy artist research for '%s' (step 1)", _ascii_log_value(artist_name))
    raw_output1 = runner(cmd_step1, timeout)
    parsed1 = extract_json_payload(raw_output1)
    if not parsed1:
        # Search failure: do NOT disguise as sparse or zero info!
        raise RuntimeError(
            f"Failed to extract structured knowledge for artist '{artist_name}' from agy response. "
            f"Raw output snippet: {raw_output1[:300] if raw_output1 else '<empty>'}"
        )

    summary = str(parsed1.get("factual_summary") or "").strip()
    display_name = str(parsed1.get("display_name") or artist_name).strip()
    sources = sanitize_sources(parsed1.get("sources"))
    if agy_runner is None:
        sources = remove_definitively_dead_sources(sources)
    raw_unc = str(parsed1.get("uncertainty") or "medium").lower()
    uncertainty = raw_unc if raw_unc in ("low", "medium", "high") else "medium"
    identity = parsed1.get("identity_context") if isinstance(parsed1.get("identity_context"), dict) else {}

    # Check whether the result is genuinely sparse
    is_sparse = (
        parsed1.get("is_sparse") is True
        or (uncertainty == "high" and len(sources) == 0 and len(summary) < 30)
        or len(summary) < 15
        or "暂无" in summary
        or "未检索" in summary
    )

    # Step 2: Only if step 1 is genuinely sparse AND song/album context is available
    has_release_context = bool((song_title and song_title.strip()) or (album_title and album_title.strip()))
    if is_sparse and has_release_context:
        step2_query = f"{artist_name} {song_title} {album_title}".strip()
        search_queries.append(step2_query)
        cmd_step2 = [
            executable,
            "-p",
            build_search_prompt_step2(artist_name, song_title, album_title),
            "--model",
            "gemini-3.8-flash-high",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            "10m",
        ]
        logger.info(
            "Artist '%s' is sparse; researching with song/album context (step 2)",
            _ascii_log_value(artist_name),
        )
        try:
            raw_output2 = runner(cmd_step2, timeout)
            parsed2 = extract_json_payload(raw_output2)
            if parsed2:
                summary2 = str(parsed2.get("factual_summary") or "").strip()
                sources2 = sanitize_sources(parsed2.get("sources"))
                if agy_runner is None:
                    sources2 = remove_definitively_dead_sources(sources2)
                raw_unc2 = str(parsed2.get("uncertainty") or uncertainty).lower()
                uncertainty2 = raw_unc2 if raw_unc2 in ("low", "medium", "high") else uncertainty
                identity2 = parsed2.get("identity_context") if isinstance(parsed2.get("identity_context"), dict) else {}

                # Combine or upgrade results
                if len(summary2) >= len(summary) or "暂无" in summary:
                    summary = summary2 or summary
                if parsed2.get("display_name"):
                    display_name = str(parsed2["display_name"]).strip()
                for s in sources2:
                    if s not in sources:
                        sources.append(s)
                uncertainty = uncertainty2
                for k, v in identity2.items():
                    if v and not identity.get(k):
                        identity[k] = v
        except Exception as exc:
            logger.warning(
                "Step 2 search failed for '%s': %s",
                _ascii_log_value(artist_name),
                _ascii_log_value(exc),
            )

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    final_status = "sparse" if (uncertainty == "high" and len(sources) == 0 and len(summary) < 30) else "completed"

    return {
        "artist_name": artist_name,
        "display_name": display_name or artist_name,
        "factual_summary": summary or f"关于音乐人「{artist_name}」的公开网络资料较少，属独立/小众发行。",
        "sources": sources,
        "uncertainty": uncertainty,
        "identity_context": identity,
        "status": final_status,
        "search_queries": search_queries,
        "error": "",
        "created_at": now_iso,
        "updated_at": now_iso,
        "last_checked_at": now_iso,
    }


class ArtistKnowledgeCollector:
    """
    Background bounded persistent artist knowledge collector.
    Enforces deduplication of concurrent jobs, bounded worker pool, caching,
    timeout handling, and non-blocking asynchronous enqueue.
    """

    def __init__(
        self,
        db: DiscoveryDB,
        max_workers: int = DEFAULT_CONCURRENCY,
        timeout: int = DEFAULT_AGY_TIMEOUT_SECONDS,
        max_pending: Optional[int] = None,
        agy_runner: Optional[Callable[[List[str], int], str]] = None,
    ):
        self.db = db
        self.max_workers = max(1, int(max_workers))
        self.timeout = timeout
        self.max_pending = max(self.max_workers, int(max_pending or DEFAULT_MAX_PENDING))
        self.agy_runner = agy_runner
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="ArtistKnowledgeWorker",
        )
        self._active_jobs: Set[str] = set()
        self._queued_jobs: Set[str] = set()
        self._lock = threading.Lock()

    def is_active_or_queued(self, artist_name: str) -> bool:
        """Return whether an artist is actively being researched or waiting in queue."""
        normalized = normalize_artist_name(artist_name)
        if not normalized:
            return False
        active_key = artist_lookup_key(normalized)
        with self._lock:
            if active_key in self._active_jobs or active_key in self._queued_jobs:
                return True
        rec = self.db.get_artist_knowledge(normalized)
        if rec and rec.get("status") in ("pending", "collecting"):
            return True
        return False

    def get_in_flight_count(self) -> int:
        """Return count of tasks currently executing or queued."""
        with self._lock:
            return len(self._active_jobs) + len(self._queued_jobs)

    def enqueue_artist(
        self,
        artist_name: str,
        song_title: str = "",
        album_title: str = "",
        force: bool = False,
    ) -> bool:
        """
        Non-blocking enqueue of an artist for background knowledge collection.
        Returns True if queued, False if already active, queued, or cached.
        """
        normalized = normalize_artist_name(artist_name)
        if not normalized:
            return False
        active_key = artist_lookup_key(normalized)

        with self._lock:
            if active_key in self._active_jobs or active_key in self._queued_jobs:
                logger.debug(
                    "Artist '%s' already being collected or queued; skipping deduped job.",
                    _ascii_log_value(normalized),
                )
                return False

            if (len(self._active_jobs) + len(self._queued_jobs)) >= self.max_pending:
                logger.warning("Artist knowledge queue is full; deferring '%s'.", _ascii_log_value(normalized))
                return False

        claimed = self.db.mark_artist_pending(normalized, force=force)
        if not claimed:
            return False

        with self._lock:
            self._queued_jobs.add(active_key)

        try:
            self._executor.submit(
                self._worker_collect,
                normalized,
                song_title,
                album_title,
                active_key,
            )
            return True
        except Exception:
            with self._lock:
                self._queued_jobs.discard(active_key)
            raise

    def collect_sync(
        self,
        artist_name: str,
        song_title: str = "",
        album_title: str = "",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Synchronously collect artist knowledge (useful for CLI backfill or direct tests)."""
        normalized = normalize_artist_name(artist_name)
        if not normalized:
            raise ValueError("Artist name cannot be empty")

        if not force:
            existing = self.db.get_artist_knowledge(normalized)
            if existing and not artist_knowledge_needs_refresh(existing):
                return existing

        active_key = artist_lookup_key(normalized)
        with self._lock:
            if active_key in self._active_jobs:
                return self.db.get_artist_knowledge(normalized) or {
                    "artist_name": normalized,
                    "display_name": normalized,
                    "status": "pending",
                }
            self._active_jobs.add(active_key)

        try:
            return self._execute_and_persist(normalized, song_title, album_title)
        finally:
            with self._lock:
                self._active_jobs.discard(active_key)

    collect_now = collect_sync

    def _worker_collect(self, artist_name: str, song_title: str, album_title: str, active_key: str) -> None:
        with self._lock:
            self._queued_jobs.discard(active_key)
            self._active_jobs.add(active_key)
        try:
            self._execute_and_persist(artist_name, song_title, album_title)
        except Exception as exc:
            logger.error(
                "Background collection error for '%s': %s",
                _ascii_log_value(artist_name),
                _ascii_log_value(exc),
                exc_info=True,
            )
        finally:
            with self._lock:
                self._active_jobs.discard(active_key)

    def _execute_and_persist(self, artist_name: str, song_title: str, album_title: str) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            record = run_artist_research(
                artist_name=artist_name,
                song_title=song_title,
                album_title=album_title,
                timeout=self.timeout,
                agy_runner=self.agy_runner,
            )
            self.db.upsert_artist_knowledge(record)
            return record
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "Timeout collecting knowledge for '%s': %s",
                _ascii_log_value(artist_name),
                _ascii_log_value(exc),
            )
            failed_record = {
                "artist_name": artist_name,
                "display_name": artist_name,
                "factual_summary": "",
                "sources": [],
                "uncertainty": "high",
                "identity_context": {},
                "status": "failed",
                "search_queries": [artist_name],
                "error": f"Research timed out after {self.timeout}s",
                "created_at": now_iso,
                "updated_at": now_iso,
                "last_checked_at": now_iso,
            }
            try:
                self.db.upsert_artist_knowledge(failed_record)
            except Exception as db_exc:
                logger.debug(
                    "Could not persist timeout record for '%s': %s",
                    _ascii_log_value(artist_name),
                    _ascii_log_value(db_exc),
                )
            return failed_record
        except Exception as exc:
            logger.warning(
                "Failed collecting knowledge for '%s': %s",
                _ascii_log_value(artist_name),
                _ascii_log_value(exc),
            )
            failed_record = {
                "artist_name": artist_name,
                "display_name": artist_name,
                "factual_summary": "",
                "sources": [],
                "uncertainty": "high",
                "identity_context": {},
                "status": "failed",
                "search_queries": [artist_name],
                "error": str(exc),
                "created_at": now_iso,
                "updated_at": now_iso,
                "last_checked_at": now_iso,
            }
            try:
                self.db.upsert_artist_knowledge(failed_record)
            except Exception as db_exc:
                logger.debug(
                    "Could not persist failed record for '%s': %s",
                    _ascii_log_value(artist_name),
                    _ascii_log_value(db_exc),
                )
            return failed_record

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown executor."""
        self._executor.shutdown(wait=wait)


_global_collector: Optional[ArtistKnowledgeCollector] = None
_global_collector_lock = threading.Lock()


def get_artist_collector(db: Optional[DiscoveryDB] = None) -> ArtistKnowledgeCollector:
    """Obtain the global singleton ArtistKnowledgeCollector."""
    global _global_collector
    with _global_collector_lock:
        if db is None and _global_collector is None:
            db = DiscoveryDB()
        if _global_collector is None:
            assert db is not None
            _global_collector = ArtistKnowledgeCollector(db=db)
        elif db is not None:
            # review_api creates a lightweight DiscoveryDB wrapper per
            # request, so object identity is not a stable database identity.
            # Compare the actual path to keep one bounded worker pool alive
            # across requests and avoid leaking an executor on every hover.
            current_path = os.path.abspath(str(getattr(_global_collector.db, "db_path", "")))
            requested_path = os.path.abspath(str(getattr(db, "db_path", "")))
            if current_path != requested_path:
                _global_collector.shutdown(wait=False)
                _global_collector = ArtistKnowledgeCollector(db=db)
        return _global_collector
