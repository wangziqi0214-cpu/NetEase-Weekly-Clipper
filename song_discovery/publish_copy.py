"""Deterministic publish copy generator for video workflow releases.

Generates structured Chinese Markdown copy from researched_playlist.json
without external AI calls or hallucination.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def parse_week_from_playlist_name(playlist_name: str) -> Optional[Tuple[int, int]]:
    """Extract (year, week_number) from playlist_name if matching 'YYYY年第NN周'."""
    if not playlist_name:
        return None
    match = re.search(r"(\d{4})\s*年\s*第\s*(\d{1,2})\s*周", playlist_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def derive_week_info(
    playlist_name: Optional[str] = None,
    tracks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Derive ISO week, date range, and month week-of-month.

    Priority:
    1. Parse 'YYYY年第NN周' from playlist_name.
    2. Infer from release_publish_time or release_date in tracks.
    3. Fallback to current date ISO week.
    """
    parsed = parse_week_from_playlist_name(playlist_name or "")
    if parsed:
        year, week = parsed
    else:
        year, week = None, None
        if tracks:
            for track in tracks:
                pub_time = track.get("release_publish_time")
                if pub_time:
                    try:
                        # Can be millisecond or second timestamp
                        ts = float(pub_time)
                        if ts > 1e11:
                            ts /= 1000.0
                        dt = datetime.fromtimestamp(ts)
                        iso_cal = dt.date().isocalendar()
                        year, week = iso_cal.year, iso_cal.week
                        break
                    except (ValueError, OSError, OverflowError):
                        pass
                rel_date = track.get("release_date") or track.get("source_release_date") or track.get("target_release_date")
                if rel_date and isinstance(rel_date, str) and len(rel_date) >= 10:
                    try:
                        dt = date.fromisoformat(rel_date[:10])
                        iso_cal = dt.isocalendar()
                        year, week = iso_cal.year, iso_cal.week
                        break
                    except ValueError:
                        pass

        if year is None or week is None:
            today = date.today()
            iso_cal = today.isocalendar()
            year, week = iso_cal.year, iso_cal.week

    monday = date.fromisocalendar(year, week, 1)
    sunday = date.fromisocalendar(year, week, 7)
    month = monday.month

    # Calculate week-of-the-month: number of distinct ISO calendar weeks
    # touching this month up to and including the current week's Monday.
    distinct_weeks = set()
    for day in range(1, monday.day + 1):
        d = date(monday.year, month, day)
        distinct_weeks.add(d.isocalendar().week)
    week_of_month = len(distinct_weeks)

    date_range_str = f"{monday.strftime('%m.%d')}-{sunday.strftime('%m.%d')}"
    summary_str = f"{year}年第{week}周（{date_range_str}，{month}月第{week_of_month}周）"

    return {
        "year": year,
        "week": week,
        "month": month,
        "week_of_month": week_of_month,
        "monday": monday,
        "sunday": sunday,
        "date_range_str": date_range_str,
        "summary_str": summary_str,
    }


def get_track_artist_text(track: Dict[str, Any]) -> str:
    """Format track artist names cleanly."""
    artists = track.get("artists")
    if isinstance(artists, list):
        filtered = [str(a).strip() for a in artists if str(a).strip()]
        return " / ".join(filtered) if filtered else ""
    return str(artists or "").strip()


def get_track_release_type(track: Dict[str, Any]) -> str:
    """Extract normalized release type ('专辑', 'EP', '单曲', etc.)."""
    return (
        track.get("normalized_release_type")
        or track.get("album_type")
        or track.get("release_raw_type")
        or "单曲"
    )


def is_album_or_ep(release_type: str) -> bool:
    """Check if release type is an album or EP."""
    norm = release_type.strip().lower()
    return norm in {"专辑", "ep", "album", "mini album", "lp", "全长专辑"}


def get_track_release_title(track: Dict[str, Any]) -> str:
    """Get release title following the business rules:

    - Album / EP: prioritize release_title / album.
    - Single (单曲): MUST use song name.
    - Other / Fallback: release_title / album / name.
    """
    rel_type = get_track_release_type(track)
    if is_album_or_ep(rel_type):
        return (
            track.get("release_title")
            or track.get("album")
            or track.get("name")
            or ""
        ).strip()
    norm = rel_type.strip().lower()
    if norm in {"单曲", "single"}:
        return str(track.get("name") or "").strip()
    return (
        track.get("release_title")
        or track.get("album")
        or track.get("name")
        or ""
    ).strip()


def get_headline_artists(tracks: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    """Extract the first N unique artist names in original track order."""
    unique_artists: List[str] = []
    seen = set()
    for track in tracks:
        artists = track.get("artists")
        artist_list = artists if isinstance(artists, list) else [artists]
        for art in artist_list:
            if art is None:
                continue
            name = str(art).strip()
            if name and name not in seen:
                seen.add(name)
                unique_artists.append(name)
                if len(unique_artists) >= limit:
                    return unique_artists
    return unique_artists


def build_structural_judgment(
    total: int,
    album_count: int,
    ep_count: int,
    single_count: int,
) -> str:
    """Generate objective structural judgment based purely on release counts."""
    long_play_count = album_count + ep_count
    if total == 0:
        return "本周暂无收录新歌发行。"
    if single_count == total:
        return "本周全部为单曲发行，呈现高频单曲更新节奏。"
    if long_play_count == total:
        return "本周全部为长碟作品，呈现完整的专辑企划释放。"
    if album_count > single_count:
        return "本周全长专辑集中释放，发行重心向完整作品倾斜。"
    if album_count == single_count and album_count > 0:
        return "本周专辑与单曲数量并重，既有即时单曲更新，也有完整作品释出。"
    if long_play_count > 0 and single_count > 0:
        return "本周发行以单曲为主，同时伴有长碟作品的释出。"
    return "本周发行形态多样，长短规格互为补充。"


def generate_publish_copy(
    data_or_path: Union[Dict[str, Any], str, Path],
    artist_limit: int = 3,
) -> str:
    """Generate pure deterministic Chinese Markdown publish copy from researched_playlist data."""
    if isinstance(data_or_path, (str, Path)):
        with open(data_or_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = data_or_path

    playlist_name = str(data.get("playlist_name") or "")
    tracks: List[Dict[str, Any]] = data.get("tracks") or []
    total = len(tracks)

    # 1. First line headline
    headline_artists = get_headline_artists(tracks, limit=artist_limit)
    if headline_artists:
        headline = f"# {'、'.join(headline_artists)}等乐队新歌电台来了！"
    else:
        headline = "# 乐队新歌电台来了！"

    # 2. Week derivation and structural assessment
    week_info = derive_week_info(playlist_name=playlist_name, tracks=tracks)
    counts: Dict[str, int] = {}
    for track in tracks:
        typ = get_track_release_type(track)
        counts[typ] = counts.get(typ, 0) + 1

    album_count = counts.get("专辑", 0)
    ep_count = counts.get("EP", 0)
    single_count = counts.get("单曲", 0)

    judgment = build_structural_judgment(
        total=total,
        album_count=album_count,
        ep_count=ep_count,
        single_count=single_count,
    )

    p2 = (
        f"本期为{week_info['summary_str']}，共收录 {total} 组发行，"
        f"包含 {album_count} 张专辑、{ep_count} 张EP、{single_count} 首单曲。"
        f"{judgment}"
    )

    # 3. Release details list (preserving original order)
    detail_lines = [
        "🔴本周发行详情：",
    ]
    for track in tracks:
        artist_text = get_track_artist_text(track)
        rel_type = get_track_release_type(track)
        rel_title = get_track_release_title(track)
        detail_lines.append(f"·{artist_text}发行[{rel_type}] 《{rel_title}》")

    sections = [
        headline,
        "",
        p2,
        "",
        "\n".join(detail_lines),
    ]

    return "\n".join(sections) + "\n"


def write_publish_copy_artifacts(
    job_dir: Union[str, Path],
    copy_text: str,
    overwrite_editable: bool = False,
) -> Tuple[Path, Path]:
    """Write publish_copy.auto.md (immutable auto draft) and publish_copy.md (user editable draft).

    Rules:
    - publish_copy.auto.md is always written/updated with copy_text.
    - publish_copy.md:
      - If it doesn't exist, write copy_text.
      - If it exists and matches previous auto text, update with new copy_text.
      - If it has been modified by the user (differs from old auto), DO NOT overwrite,
        unless overwrite_editable is explicitly True.
    """
    job_path = Path(job_dir).resolve()
    job_path.mkdir(parents=True, exist_ok=True)

    auto_path = job_path / "publish_copy.auto.md"
    editable_path = job_path / "publish_copy.md"

    old_auto_text: Optional[str] = None
    if auto_path.is_file():
        try:
            old_auto_text = auto_path.read_text(encoding="utf-8")
        except OSError:
            old_auto_text = None

    # Always write publish_copy.auto.md atomically
    _atomic_write_text(auto_path, copy_text)

    # Determine whether to update publish_copy.md
    should_update_editable = False
    if overwrite_editable or not editable_path.is_file():
        should_update_editable = True
    elif old_auto_text is not None:
        try:
            current_editable = editable_path.read_text(encoding="utf-8")
            if current_editable == old_auto_text:
                should_update_editable = True
        except OSError:
            should_update_editable = True

    if should_update_editable:
        _atomic_write_text(editable_path, copy_text)

    return auto_path, editable_path


def _atomic_write_text(target_path: Path, content: str) -> None:
    """Atomically write text file using UTF-8."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_file = tempfile.mkstemp(dir=str(target_path.parent), prefix=".tmp_pub_", text=True)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_file, str(target_path))
    finally:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
