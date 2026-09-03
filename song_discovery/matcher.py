"""Conservative normalized title and artist matcher for cross-platform song resolution."""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

try:
    from opencc import OpenCC
    _T2S_CONVERTER = OpenCC("t2s")
except (ImportError, OSError):  # Keep the app usable before optional dependencies are installed.
    _T2S_CONVERTER = None


_VERSION_PATTERNS = {
    "demo": re.compile(r"(?<![a-z])(?:demo|demo版|小样|小樣|试听版|試聽版)(?![a-z])", re.IGNORECASE),
    "live": re.compile(r"(?<![a-z])(?:live|现场版|現場版|演唱会版|演唱會版|万人大合唱|萬人大合唱)(?![a-z])", re.IGNORECASE),
    "remix": re.compile(r"(?<![a-z])(?:remix|混音版)(?![a-z])", re.IGNORECASE),
    "acoustic": re.compile(r"(?<![a-z])(?:acoustic|不插电|不插電|原声版|原聲版)(?![a-z])", re.IGNORECASE),
    "instrumental": re.compile(r"(?<![a-z])(?:instrumental|伴奏|纯音乐|純音樂|off\s*vocal)(?![a-z])", re.IGNORECASE),
    "remaster": re.compile(r"(?<![a-z])(?:remaster(?:ed)?|重制版|重製版)(?![a-z])", re.IGNORECASE),
}
_DESCRIPTOR_WORDS = re.compile(
    r"电影|電影|电视剧|電視劇|网剧|網劇|剧集|劇集|游戏|遊戲|动漫|動漫|动画|動畫|纪录片|紀錄片|"
    r"短剧|短劇|主题曲|主題曲|插曲|推广曲|推廣曲|片头曲|片頭曲|片尾曲|原声带|原聲帶|曲目|ost|配乐|配樂",
    re.IGNORECASE,
)
_BRACKETED = re.compile(r"[\(（\[【\{].*?[\)）\]】\}]", re.DOTALL)
_FEAT_SUFFIX = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", re.IGNORECASE)


def to_simplified(value: str) -> str:
    """Normalize Unicode and convert Traditional Chinese to Simplified when OpenCC is present."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return _T2S_CONVERTER.convert(text) if _T2S_CONVERTER is not None else text


def extract_version_intents(title: str) -> List[str]:
    """Extract recording/version semantics that must not be silently discarded."""
    normalized = unicodedata.normalize("NFKC", str(title or ""))
    return sorted(name for name, pattern in _VERSION_PATTERNS.items() if pattern.search(normalized))


def clean_search_title(title: str) -> str:
    """Return the core song title used only for fallback search and comparison.

    Version intent is extracted separately before bracket and suffix removal.
    """
    value = unicodedata.normalize("NFKC", str(title or "")).strip()
    if not value:
        return ""
    value = _FEAT_SUFFIX.sub("", value)
    value = _BRACKETED.sub(" ", value)
    # Remove dash-separated editorial/version suffixes, but keep ordinary hyphenated titles.
    parts = re.split(r"\s+[\-–—]\s+", value, maxsplit=1)
    if len(parts) == 2 and (_DESCRIPTOR_WORDS.search(parts[1]) or any(p.search(parts[1]) for p in _VERSION_PATTERNS.values())):
        value = parts[0]
    # A non-bracketed OST suffix may follow the core title without a dash.
    descriptor = _DESCRIPTOR_WORDS.search(value)
    if descriptor and descriptor.start() > 0:
        value = value[:descriptor.start()]
    value = re.sub(r"\s+", " ", value).strip(" -–—_·:：")
    return value or unicodedata.normalize("NFKC", str(title or "")).strip()


def generate_search_queries(title: str, artists: str) -> List[str]:
    """Generate ordered, deduplicated NetEase queries from strict to broad."""
    raw_title = unicodedata.normalize("NFKC", str(title or "")).strip()
    raw_artists = unicodedata.normalize("NFKC", str(artists or "")).strip()
    core_title = clean_search_title(raw_title)
    candidates = [
        f"{raw_title} {raw_artists}".strip(),
        f"{to_simplified(raw_title)} {to_simplified(raw_artists)}".strip(),
        f"{core_title} {raw_artists}".strip(),
        f"{to_simplified(core_title)} {to_simplified(raw_artists)}".strip(),
        to_simplified(core_title),
    ]
    seen = set()
    result = []
    for query in candidates:
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            result.append(query)
    return result


@dataclass
class MatchResult:
    """Result of cross-platform track matching against NetEase catalog."""
    status: str  # 'direct', 'matched', 'ambiguous', 'unmatched'
    netease_track_id: Optional[str] = None
    confidence: float = 0.0
    matched_title: Optional[str] = None
    matched_artists: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def normalize_title(title: str) -> str:
    """Clean and normalize song title for comparison."""
    if not title:
        return ""
    s = to_simplified(clean_search_title(title)).lower()
    # Remove version brackets like (Live), (Demo), [Remastered], (feat. ...)
    s = re.sub(r"[\(\[\{（【].*?[\)\]\}）】]", " ", s)
    # Remove punctuation & special characters
    s = re.sub(r"[^\w\u4e00-\u9fa5]", "", s)
    return s.strip()


def normalize_artist(artist_str: str) -> List[str]:
    """Split and normalize artist names."""
    if not artist_str:
        return []
    raw_names = re.split(r"[/,、&|+，]", to_simplified(artist_str))
    cleaned = []
    for name in raw_names:
        c = re.sub(r"[^\w\u4e00-\u9fa5]", "", name.lower()).strip()
        # Also clean common suffix '乐队' for matching with base name
        if c:
            cleaned.append(c)
    return cleaned


class TrackMatcher:
    """
    Conservatively matches external tracks (QQ Music, KKBOX) against NetEase search results.
    Prioritizes high precision to avoid false-positive additions.
    """

    CONFIDENCE_THRESHOLD = 0.80
    AMBIGUITY_DELTA = 0.06

    def score_single_candidate(
        self,
        target_title: str,
        target_artists: str,
        target_duration_ms: Optional[int],
        netease_song: Dict[str, Any],
    ) -> Tuple[float, List[str]]:
        reasons = []
        score = 0.0

        cand_title = str(netease_song.get("name", "")).strip()
        cand_artists = [str(a.get("name", "")).strip() for a in netease_song.get("ar", []) or netease_song.get("artists", [])]
        cand_artist_str = " / ".join(cand_artists)
        cand_duration = netease_song.get("dt") or netease_song.get("duration")
        target_intents = set(extract_version_intents(target_title))
        candidate_intents = set(extract_version_intents(cand_title))

        norm_target_title = normalize_title(target_title)
        norm_cand_title = normalize_title(cand_title)

        if not norm_target_title or not norm_cand_title:
            return 0.0, ["标题为空"]

        # 1. Title Similarity (0.0 to 0.55 weight)
        title_ratio = SequenceMatcher(None, norm_target_title, norm_cand_title).ratio()
        if norm_target_title == norm_cand_title:
            score += 0.55
            reasons.append("标题完全匹配 (+55%)")
        elif norm_target_title in norm_cand_title or norm_cand_title in norm_target_title:
            # Subtitle or prefix match
            min_len = min(len(norm_target_title), len(norm_cand_title))
            if min_len >= 4:
                score += 0.50
                reasons.append(f"标题包含/子标题匹配 (+50%)")
            else:
                score += 0.40
                reasons.append(f"短标题子串匹配 (+40%)")
        elif title_ratio >= 0.85:
            title_pts = 0.50 * title_ratio
            score += title_pts
            reasons.append(f"标题高相似度 {title_ratio:.2f} (+{title_pts*100:.0f}%)")
        else:
            reasons.append(f"标题相似度较低: {title_ratio:.2f}")
            return 0.0, reasons

        # 2. Artist Similarity (0.0 to 0.35 weight)
        target_art_list = normalize_artist(target_artists)
        cand_art_list = normalize_artist(cand_artist_str)

        matched_artists = []
        for t_art in target_art_list:
            t_base = t_art.replace("乐队", "").replace("band", "").strip()
            for c_art in cand_art_list:
                c_base = c_art.replace("乐队", "").replace("band", "").strip()
                if t_art == c_art or t_base == c_base or (t_base and t_base in c_base) or (c_base and c_base in t_base):
                    matched_artists.append(f"{t_art}~{c_art}")
                    break

        if matched_artists:
            art_score = min(0.35, 0.30 + (0.05 if len(matched_artists) == len(target_art_list) else 0))
            score += art_score
            reasons.append(f"歌手匹配 ({', '.join(matched_artists)}) (+{art_score*100:.0f}%)")
        else:
            reasons.append("歌手未匹配")
            return 0.0, reasons

        # 3. Duration Consistency Check (0.0 to 0.10 weight or penalty)
        if target_duration_ms and cand_duration and cand_duration > 0:
            diff_sec = abs(target_duration_ms - cand_duration) / 1000.0
            if diff_sec <= 8.0:
                score += 0.10
                reasons.append(f"时长一致 (差 {diff_sec:.1f}s) (+10%)")
            elif diff_sec <= 20.0:
                score += 0.05
                reasons.append(f"时长较接近 (差 {diff_sec:.1f}s) (+5%)")
            elif diff_sec > 60.0:
                score -= 0.20
                reasons.append(f"时长偏差过大 (差 {diff_sec:.1f}s) (-20%)")

        if target_intents != candidate_intents:
            missing = sorted(target_intents - candidate_intents)
            extra = sorted(candidate_intents - target_intents)
            reasons.append(f"版本标签不一致 (缺少: {missing or '-'}; 额外: {extra or '-'})")
            score = min(score, self.CONFIDENCE_THRESHOLD - 0.01)

        return max(0.0, min(1.0, score)), reasons

    def match(
        self,
        target_title: str,
        target_artists: str,
        target_duration_ms: Optional[int],
        search_candidates: List[Dict[str, Any]],
    ) -> MatchResult:
        """
        Evaluate a list of NetEase /cloudsearch results against target track.
        """
        if not search_candidates:
            return MatchResult(
                status="unmatched",
                confidence=0.0,
                reasons=["网易云搜索结果为空"],
                details={"candidates_count": 0},
            )

        scored_candidates = []
        for cand in search_candidates:
            cid = str(cand.get("id", ""))
            score, reasons = self.score_single_candidate(
                target_title=target_title,
                target_artists=target_artists,
                target_duration_ms=target_duration_ms,
                netease_song=cand,
            )
            cand_title = cand.get("name", "")
            cand_artists = " / ".join([a.get("name", "") for a in cand.get("ar", []) or cand.get("artists", [])])
            scored_candidates.append({
                "id": cid,
                "title": cand_title,
                "artists": cand_artists,
                "score": score,
                "reasons": reasons,
                "raw": cand,
            })

        # Sort by score descending
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top = scored_candidates[0]

        # Check if top score meets conservative threshold
        if top["score"] < self.CONFIDENCE_THRESHOLD:
            if top["score"] >= self.CONFIDENCE_THRESHOLD - 0.01 and any("版本标签不一致" in reason for reason in top["reasons"]):
                return MatchResult(
                    status="ambiguous",
                    confidence=top["score"],
                    matched_title=top["title"],
                    matched_artists=top["artists"],
                    reasons=["标题与艺人匹配，但版本标签不同，需人工确认"] + top["reasons"],
                    details={"candidates": scored_candidates[:3]},
                )
            return MatchResult(
                status="unmatched",
                confidence=top["score"],
                matched_title=top["title"],
                matched_artists=top["artists"],
                reasons=[f"最高匹配置信度不足 ({top['score']:.2f} < {self.CONFIDENCE_THRESHOLD})"] + top["reasons"],
                details={"candidates": scored_candidates},
            )

        # Check for ambiguity with runner-up
        if len(scored_candidates) > 1:
            runner_up = scored_candidates[1]
            if runner_up["score"] >= self.CONFIDENCE_THRESHOLD and abs(top["score"] - runner_up["score"]) < self.AMBIGUITY_DELTA:
                return MatchResult(
                    status="ambiguous",
                    confidence=top["score"],
                    matched_title=top["title"],
                    matched_artists=top["artists"],
                    reasons=[
                        f"存在多个高置信度候选项 (Top1: {top['score']:.2f} vs Top2: {runner_up['score']:.2f})，为避免加错曲目已标记为存疑"
                    ],
                    details={"candidates": scored_candidates[:3]},
                )

        return MatchResult(
            status="matched",
            netease_track_id=top["id"],
            confidence=top["score"],
            matched_title=top["title"],
            matched_artists=top["artists"],
            reasons=[f"置信度: {top['score']:.2f}"] + top["reasons"],
            details={"top_candidate": top},
        )
