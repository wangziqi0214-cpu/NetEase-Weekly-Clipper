from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import google.generativeai as genai
except ImportError:
    genai = None

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from src.researcher.common import (
    build_intro_group_key,
    build_radio_script_compact,
    build_radio_script_fallback,
    build_radio_script_ultra_compact,
    compose_release_label,
    epoch_ms_to_year,
    extract_label_from_description,
    fetch_album_detail,
    format_artist_for_radio,
    format_release_title_for_radio,
    init_netease_session,
    is_recent_release,
    normalize_radio_copy_text,
    normalize_release_type,
    search_release_context,
)
from src.researcher.radio_tts import AUDIO_CACHE_DIR, generate_radio_tts, resolve_voice_reference


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _get_api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _configure_genai() -> bool:
    if genai is None:
        return False
    api_key = _get_api_key()
    if not api_key:
        return False
    genai.configure(api_key=api_key)
    return True


def enrich_track_facts(track: dict[str, Any]) -> dict[str, Any]:
    album_id = track.get("album_id")
    album_info = fetch_album_detail(album_id) if album_id else {}

    raw_type = track.get("release_raw_type")
    raw_subtype = track.get("release_raw_subtype")
    release_size = track.get("release_size")
    release_publish_time = track.get("release_publish_time")
    release_title = track.get("release_title") or track.get("album") or track.get("name")
    release_company = track.get("release_company")
    album_description = track.get("album_description") or ""

    if album_info:
        raw_type = raw_type if raw_type is not None else album_info.get("type")
        raw_subtype = raw_subtype if raw_subtype is not None else album_info.get("subType")
        release_size = release_size or album_info.get("size")
        release_publish_time = release_publish_time or album_info.get("publishTime")
        release_company = release_company or album_info.get("company")
        release_title = release_title or album_info.get("name")
        album_description = album_description or album_info.get("description") or ""

    normalized_release_type = normalize_release_type(
        raw_type,
        raw_subtype,
        release_size,
        track.get("name", ""),
        release_title,
    )
    release_year = epoch_ms_to_year(release_publish_time)
    label_from_description = extract_label_from_description(album_description)
    release_label_text = release_company or label_from_description or compose_release_label(
        {
            "normalized_release_type": normalized_release_type,
            "release_year": release_year,
        }
    )

    search_context: list[str] = []
    if not release_publish_time or not release_company:
        search_context = search_release_context(
            {
                "name": track.get("name"),
                "artists": track.get("artists", []),
                "album": release_title,
            }
        )

    track.update(
        {
            "release_title": release_title,
            "release_raw_type": raw_type,
            "release_raw_subtype": raw_subtype,
            "release_size": release_size,
            "release_publish_time": release_publish_time,
            "release_year": release_year,
            "release_company": release_company,
            "release_label_text": release_label_text,
            "album_description": album_description,
            "normalized_release_type": normalized_release_type,
            "album_type": normalized_release_type,
            "is_new_song": is_recent_release(release_publish_time),
            "research_search_context": search_context,
        }
    )
    track["intro_group_key"] = build_intro_group_key(track)
    return track


def prepare_playlist(input_path: str, output_path: str) -> str:
    init_netease_session()
    payload = load_json(input_path)
    payload["tracks"] = [enrich_track_facts(track) for track in payload.get("tracks", [])]
    save_json(output_path, payload)
    print(f"[*] Research prepare complete -> {output_path}")
    return output_path


def build_radio_prompt(track: dict[str, Any]) -> str:
    artist = track.get("artists", ["未知乐队"])[0]
    radio_artist = format_artist_for_radio(artist)
    release_title = track.get("release_title") or track.get("album") or track.get("name")
    release_type = track.get("normalized_release_type") or track.get("album_type") or "单曲"
    radio_release_title = format_release_title_for_radio(track)
    release_year = track.get("release_year")
    label_text = track.get("release_label_text") or ""
    search_context = "\n".join(track.get("research_search_context") or [])
    album_description = track.get("album_description") or ""

    return f"""
你是一个中文独立音乐电台主持人。请写一条 18 到 34 个汉字的短串词，要求在正常中文播报里大约 3 到 4 秒说完。

歌曲信息：
- 乐队/艺人：{artist}
- 标准播报艺人名：{radio_artist}
- 歌曲：{track.get("name")}
- 发行物：{release_title}
- 类型：{release_type}
- 标准播报发行物：{radio_release_title}
- 年份：{release_year}
- 厂牌/标签：{label_text}

参考信息：
{album_description}

搜索摘录：
{search_context}

要求：
1. 只写一条自然播报句子。
2. 优先交代这是哪支乐队的哪首歌，收录于哪张专辑/EP/现场，或是一首新单曲。
3. 提到艺人时，一律使用“{radio_artist}”这种说法，不要只念艺人名。
4. 提到发行物标题时，必须带上类型前缀，例如“专辑《作品名》”或“EP《作品名》”。
5. 不要使用夸张形容词，不要写成长乐评。
6. 不要超过 34 个汉字，尽量落在 24 到 30 个汉字。
7. 只输出最终句子。
""".strip()


def generate_radio_copy(track: dict[str, Any]) -> str:
    if _configure_genai():
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(build_radio_prompt(track))
            text = (response.text or "").strip()
            if text:
                normalized = normalize_radio_copy_text(track, text.replace("\n", "").strip("“”\""))
                if len(normalized) <= 36:
                    return normalized
        except Exception as exc:
            print(f"[!] Gemini radio copy failed for {track.get('name')}: {exc}", flush=True)

    # Use compact script directly to avoid lengthy speech before TTS
    compact_text = build_radio_script_compact(track)
    if compact_text and len(compact_text) <= 36:
        return normalize_radio_copy_text(track, compact_text)
    return normalize_radio_copy_text(track, build_radio_script_fallback(track))


def generate_radio_assets(
    input_path: str,
    output_path: str,
    voice_ref_path: str | None = None,
    voice_ref_text: str | None = None,
    track_index: int | None = None,
    track_id: int | None = None,
    force_tts: bool = False,
) -> str:
    base_clip_duration_sec = 20.0
    base_radio_window_sec = 5.0
    base_pure_song_duration_sec = 15.0
    max_radio_window_sec = 9.0
    radio_guard_sec = 0.35

    payload = load_json(input_path)
    tracks = payload.get("tracks", [])
    selected_tracks = tracks
    resolved_voice_ref_path = None
    resolved_voice_ref_text = voice_ref_text

    if voice_ref_path:
        resolved_voice_ref_path, resolved_voice_ref_text = resolve_voice_reference(voice_ref_path, voice_ref_text)

    if track_id is not None:
        selected_tracks = [track for track in tracks if str(track.get("id")) == str(track_id)]
    elif track_index is not None:
        selected_tracks = [tracks[track_index]] if 0 <= track_index < len(tracks) else []

    if not selected_tracks:
        raise ValueError("No tracks selected for radio generation.")

    total_selected = len(selected_tracks)
    for order, track in enumerate(selected_tracks, start=1):
        print(f"[*] TTS track {order}/{total_selected}: {track.get('name', 'unknown')}", flush=True)
        radio_text = generate_radio_copy(track)
        track["radio_intro_text"] = radio_text
        track["clip_duration_sec"] = base_clip_duration_sec
        track["song_fadein_sec"] = base_radio_window_sec
        track["radio_window_sec"] = base_radio_window_sec
        track["pure_song_duration_sec"] = base_pure_song_duration_sec
        track["radio_voice_guard_sec"] = radio_guard_sec

        if resolved_voice_ref_path:
            audio_path = os.path.join(AUDIO_CACHE_DIR, f"{track['id']}.wav")
            previous_text = track.get("tts_source_text")
            previous_ref_path = track.get("tts_voice_ref_path")
            previous_ref_text = track.get("tts_voice_ref_text")
            current_ref_text = resolved_voice_ref_text or ""
            needs_regen = (
                force_tts
                or not os.path.exists(audio_path)
                or previous_text != radio_text
                or previous_ref_path != resolved_voice_ref_path
                or (previous_ref_text or "") != current_ref_text
            )

            if needs_regen:
                print(f"    -> generating radio TTS: {radio_text}", flush=True)
                try:
                    generated_path, duration_sec = generate_radio_tts(
                        text=radio_text,
                        output_path=audio_path,
                        ref_audio=resolved_voice_ref_path,
                        ref_text=resolved_voice_ref_text,
                    )
                except Exception as exc:
                    print(f"[!] TTS generation failed for track '{track.get('name')}': {exc}", flush=True)
                    raise
            else:
                generated_path = audio_path
                duration_sec = float(track.get("tts_duration_ms", 0) or 0) / 1000.0

            track["tts_audio_path"] = generated_path
            track["tts_duration_ms"] = int(duration_sec * 1000) if duration_sec else track.get("tts_duration_ms", 0)
            track["tts_source_text"] = radio_text
            track["tts_voice_ref_path"] = resolved_voice_ref_path
            track["tts_voice_ref_text"] = resolved_voice_ref_text or ""
            print(f"    -> final radio TTS duration: {track['tts_duration_ms']} ms", flush=True)

            effective_voice_duration = float(duration_sec or 0.0)
            dynamic_radio_window_sec = base_radio_window_sec
            if effective_voice_duration > 0:
                dynamic_radio_window_sec = min(
                    max_radio_window_sec,
                    max(base_radio_window_sec, effective_voice_duration + radio_guard_sec),
                )
            track["song_fadein_sec"] = round(dynamic_radio_window_sec, 2)
            track["radio_window_sec"] = round(dynamic_radio_window_sec, 2)
            track["clip_duration_sec"] = round(dynamic_radio_window_sec + base_pure_song_duration_sec, 2)
            track["pure_song_duration_sec"] = base_pure_song_duration_sec
        else:
            track.setdefault("tts_audio_path", "")
            track.setdefault("tts_duration_ms", 0)

    save_json(output_path, payload)
    print(f"[*] Radio research complete -> {output_path}", flush=True)
    return output_path


def research_playlist(input_path: str = "project.json", output_path: str = "project.json") -> None:
    prepared_path = prepare_playlist(input_path, output_path)
    generate_radio_assets(prepared_path, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research pipeline for NetEase Weekly Clipper")
    subparsers = parser.add_subparsers(dest="action")

    prepare_parser = subparsers.add_parser("prepare", help="Prepare structured release facts")
    prepare_parser.add_argument("input_path", nargs="?", default="raw_playlist.json")
    prepare_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")

    radio_parser = subparsers.add_parser("radio", help="Generate radio copy and optional TTS")
    radio_parser.add_argument("input_path", nargs="?", default="researched_playlist.json")
    radio_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")
    radio_parser.add_argument("--voice-ref", dest="voice_ref_path", default=None)
    radio_parser.add_argument("--voice-ref-text", dest="voice_ref_text", default=None)
    radio_parser.add_argument("--track-index", dest="track_index", type=int, default=None)
    radio_parser.add_argument("--track-id", dest="track_id", type=int, default=None)
    radio_parser.add_argument("--force-tts", action="store_true")

    legacy_parser = subparsers.add_parser("legacy", help="Prepare data and generate text-only radio copy")
    legacy_parser.add_argument("input_path", nargs="?", default="raw_playlist.json")
    legacy_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    action = args.action or "legacy"

    if action == "prepare":
        prepare_playlist(args.input_path, args.output_path)
    elif action == "radio":
        generate_radio_assets(
            args.input_path,
            args.output_path,
            voice_ref_path=args.voice_ref_path,
            voice_ref_text=args.voice_ref_text,
            track_index=args.track_index,
            track_id=args.track_id,
            force_tts=args.force_tts,
        )
    else:
        research_playlist(args.input_path, args.output_path)
