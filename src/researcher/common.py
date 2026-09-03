from __future__ import annotations

import datetime as dt
import re
import os
import re
from http.cookiejar import MozillaCookieJar
from typing import Any

import pyncm
from pyncm import apis

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


COOKIE_PATH = "cookie.txt"
TYPE_ORDER = {"单曲": 0, "专辑": 1, "EP": 2, "现场": 3}
ALBUM_DETAIL_CACHE: dict[int, dict[str, Any]] = {}


def init_netease_session(cookie_path: str = COOKIE_PATH) -> None:
    if not os.path.exists(cookie_path):
        return
    cookie_jar = MozillaCookieJar(cookie_path)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    pyncm.GetCurrentSession().cookies = cookie_jar


def fetch_album_detail(album_id: int | None) -> dict[str, Any]:
    if not album_id:
        return {}
    if album_id in ALBUM_DETAIL_CACHE:
        return ALBUM_DETAIL_CACHE[album_id]
    try:
        info = apis.album.GetAlbumInfo(album_id)
        album = info.get("album") or {}
        ALBUM_DETAIL_CACHE[album_id] = album
        return album
    except Exception:
        return {}


def normalize_release_type(
    raw_type: str | None,
    raw_subtype: str | None,
    release_size: int | None,
    song_name: str,
    release_title: str,
) -> str:
    joined = f"{song_name} {release_title}".lower()
    raw_type_normalized = (raw_type or "").strip().lower()
    raw_subtype_normalized = (raw_subtype or "").strip().lower()
    size = release_size or 0

    if "现场" in raw_subtype_normalized or "live" in joined or "现场" in joined:
        return "现场"
    if raw_type_normalized == "ep":
        return "EP"
    if raw_type_normalized == "single":
        return "单曲"
    if raw_type_normalized == "专辑":
        if size >= 7:
            return "专辑"
        if 3 <= size <= 6:
            return "EP"
        return "单曲"
    if size >= 7:
        return "专辑"
    if 3 <= size <= 6:
        return "EP"
    return "单曲"


def build_intro_group_key(track: dict[str, Any]) -> str:
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    release_title = track.get("release_title") or track.get("album") or track.get("name") or "Unknown"
    release_id = track.get("album_id") or track.get("id") or "na"
    if release_type == "单曲":
        return "单曲::all"
    return f"{release_type}::{release_id}::{release_title}"


def chunked(items: list[Any], chunk_size: int) -> list[list[Any]]:
    return [items[idx: idx + chunk_size] for idx in range(0, len(items), chunk_size)]


def build_intro_pages(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    tracks_by_type: dict[str, list[dict[str, Any]]] = {
        "专辑": [],
        "单曲": [],
        "现场": [],
        "EP": [],
    }

    for track in tracks:
        release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
        if release_type not in tracks_by_type:
            release_type = "单曲"
        track["intro_group_key"] = build_intro_group_key(track)
        tracks_by_type[release_type].append(track)

    for release_type in ("单曲", "专辑", "EP", "现场"):
        typed_tracks = tracks_by_type.get(release_type, [])
        total_pages = len(chunked(typed_tracks, 8))
        for page_index, page_tracks in enumerate(chunked(typed_tracks, 8), start=1):
            pages.append(
                {
                    "page_type": release_type,
                    "page_title": release_type,
                    "page_subtitle": "",
                    "tracks": page_tracks,
                }
            )

    return pages


def epoch_ms_to_datestr(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    try:
        return dt.datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return None


def epoch_ms_to_year(epoch_ms: int | None) -> int | None:
    date_str = epoch_ms_to_datestr(epoch_ms)
    if not date_str:
        return None
    return int(date_str[:4])


def is_recent_release(epoch_ms: int | None, days: int = 60) -> bool:
    if not epoch_ms:
        return False
    release_date = dt.datetime.fromtimestamp(epoch_ms / 1000)
    now = dt.datetime.now()
    return (now - release_date).days <= days


def extract_label_from_description(description: str) -> str | None:
    if not description:
        return None
    matches = re.findall(r"(?:发行|出品)[:：]\s*([^\n\r]+)", description)
    for item in matches:
        cleaned = item.strip()
        if cleaned:
            return cleaned
    return None


def search_release_context(track: dict[str, Any], max_results: int = 4) -> list[str]:
    if DDGS is None:
        return []
    queries = [
        f"{track.get('artists', [''])[0]} {track.get('name')} {track.get('album')} 发行 专辑 EP 单曲",
        f"{track.get('artists', [''])[0]} {track.get('album')} 发行时间 site:music.163.com OR site:douban.com",
    ]
    snippets: list[str] = []
    try:
        with DDGS() as ddgs:
            for query in queries:
                for result in ddgs.text(query, max_results=max_results):
                    body = (result.get("body") or "").strip()
                    if body and body not in snippets:
                        snippets.append(body)
    except Exception:
        return []
    return snippets[:max_results]


def compose_release_label(track: dict[str, Any]) -> str:
    release_type = track.get("normalized_release_type") or "单曲"
    release_year = track.get("release_year")
    if release_type == "现场":
        return f"{release_year} 现场录音" if release_year else "现场录音"
    if release_year:
        return f"{release_year} {release_type}"
    return release_type


def format_artist_for_radio(artist: str) -> str:
    text = (artist or "未知乐队").strip()
    if text.startswith("乐队"):
        return text
    if text.endswith("乐队") or text.endswith("乐团"):
        base = text[:-2].strip()
        if base:
            return f"乐队{base}"
    return f"乐队{text}"


def format_release_type_prefix_for_radio(release_type: str) -> str:
    normalized = (release_type or "单曲").strip()
    if normalized == "专辑":
        return "专辑"
    if normalized == "EP":
        return "EP"
    if normalized == "现场":
        return "现场"
    return "单曲"


def format_release_title_for_radio(track: dict[str, Any]) -> str:
    release_title = track.get("release_title") or track.get("album") or track.get("name") or "未知作品"
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    return f"{format_release_type_prefix_for_radio(release_type)}《{release_title}》"


def normalize_radio_copy_text(track: dict[str, Any], text: str) -> str:
    normalized = (text or "").strip().replace("\n", "")
    artist = track.get("artists", ["未知乐队"])[0]
    radio_artist = format_artist_for_radio(artist)

    if artist:
        normalized = re.sub(rf"(?<!乐队){re.escape(artist)}", radio_artist, normalized, count=1)

    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    release_title = track.get("release_title") or track.get("album") or track.get("name") or ""
    song_name = track.get("name") or ""
    if release_type in {"专辑", "EP", "现场"} and release_title and release_title != song_name:
        quoted_title = f"《{release_title}》"
        prefixed_title = format_release_title_for_radio(track)
        if quoted_title in normalized and prefixed_title not in normalized:
            normalized = normalized.replace(quoted_title, prefixed_title, 1)

    return normalized


def build_radio_script_fallback(track: dict[str, Any]) -> str:
    artist = format_artist_for_radio(track.get("artists", ["未知乐队"])[0])
    song_name = track.get("name", "未知歌曲")
    release_title = track.get("release_title") or track.get("album") or song_name
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    release_year = track.get("release_year")
    is_new_song = track.get("is_new_song", False)
    formatted_release_title = format_release_title_for_radio(track)

    if release_type == "现场":
        if release_year:
            return f"接下来这首，是{artist}在{release_year}年的现场录音，《{song_name}》。"
        return f"接下来这首，是{artist}的现场录音版本，《{song_name}》。"
    if release_type == "EP":
        if release_year:
            return f"接下来这首来自{artist}{release_year}年的{formatted_release_title}，歌曲是《{song_name}》。"
        return f"接下来这首来自{artist}的{formatted_release_title}，歌曲是《{song_name}》。"
    if release_type == "专辑":
        if release_year:
            return f"接下来这首收录在{artist}{release_year}年的{formatted_release_title}里，歌曲是《{song_name}》。"
        return f"接下来这首收录在{artist}的{formatted_release_title}里，歌曲是《{song_name}》。"
    if is_new_song:
        return f"接下来听到的是{artist}带来的新歌《{song_name}》。"
    if release_year:
        return f"接下来这首，是{artist}在{release_year}年发行的单曲《{song_name}》。"
    return f"接下来听到的是{artist}的单曲《{song_name}》。"


def shorten_song_name_for_radio(song_name: str) -> str:
    compact = song_name.strip()
    compact = re.sub(r"[（(]\s*\d{4}\s*Live\s*[）)]", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"[（(]\s*Live\s*[）)]", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact or song_name


def build_radio_script_compact(track: dict[str, Any]) -> str:
    artist = format_artist_for_radio(track.get("artists", ["未知乐队"])[0])
    song_name = shorten_song_name_for_radio(track.get("name", "未知歌曲"))
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    is_new_song = track.get("is_new_song", False)
    formatted_release_title = format_release_title_for_radio(track)

    if release_type == "现场":
        return f"接下来是{artist}《{song_name}》现场版。"
    if is_new_song and release_type == "单曲":
        return f"接下来是{artist}的新歌《{song_name}》。"
    if release_type in {"EP", "专辑"}:
        release_title = track.get("release_title") or track.get("album") or song_name
        if release_title != song_name:
            return f"接下来是{artist}《{song_name}》，来自{formatted_release_title}。"
    return f"接下来是{artist}《{song_name}》。"


def build_radio_script_ultra_compact(track: dict[str, Any]) -> str:
    artist = format_artist_for_radio(track.get("artists", ["未知乐队"])[0])
    song_name = shorten_song_name_for_radio(track.get("name", "未知歌曲"))
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"

    if release_type == "现场":
        return f"{artist}《{song_name}》现场版。"
    return f"{artist}《{song_name}》。"
