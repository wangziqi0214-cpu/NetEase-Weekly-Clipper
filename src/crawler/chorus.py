from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

try:
    from pychorus.helpers import create_chroma, find_chorus
except ImportError:
    create_chroma = None
    find_chorus = None


DEFAULT_CLIP_LENGTH_SEC = 15.0


def clamp_chorus_start_ms(
    start_ms: int,
    clip_length_ms: int,
    duration_ms: int | None,
) -> int:
    start_ms = max(0, int(start_ms))
    if duration_ms and duration_ms > clip_length_ms:
        return min(start_ms, duration_ms - clip_length_ms)
    return start_ms


def build_result(
    status: str,
    clip_length_sec: float,
    start_ms: int | None = None,
    end_ms: int | None = None,
    note: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "chorus_candidate_status": status,
        "chorus_candidate_source": "pychorus",
        "chorus_clip_duration_sec": clip_length_sec,
        "chorus_candidate_start_ms": start_ms,
        "chorus_candidate_end_ms": end_ms,
        "chorus_candidate_note": note,
        "chorus_candidate_error": error,
        "chorus_analysis_updated_at": int(time.time()),
    }


def analyze_track(track: dict[str, Any], clip_length_sec: float = DEFAULT_CLIP_LENGTH_SEC) -> dict[str, Any]:
    local_path = track.get("local_path") or ""
    duration_ms = int(track.get("duration_ms") or 0)
    clip_length_ms = int(clip_length_sec * 1000)

    if create_chroma is None or find_chorus is None:
        return build_result(
            status="unavailable",
            clip_length_sec=clip_length_sec,
            note="pychorus is not installed",
            error="missing_dependency",
        )

    if not local_path or not os.path.exists(local_path):
        return build_result(
            status="missing_audio",
            clip_length_sec=clip_length_sec,
            note="local audio file not found",
            error=local_path or "missing_path",
        )

    if os.path.getsize(local_path) < 1024:
        return build_result(
            status="missing_audio",
            clip_length_sec=clip_length_sec,
            note="local audio file too small",
            error=local_path,
        )

    try:
        chroma, _song_wav_data, sr, song_length_sec = create_chroma(local_path)
        chorus_start_sec = find_chorus(chroma, sr, song_length_sec, clip_length_sec)
        if chorus_start_sec is None:
            return build_result(
                status="no_match",
                clip_length_sec=clip_length_sec,
                note="pychorus could not detect a strong repeated chorus window",
            )

        start_ms = clamp_chorus_start_ms(
            int(round(float(chorus_start_sec) * 1000)),
            clip_length_ms,
            duration_ms,
        )
        end_ms = start_ms + clip_length_ms
        if duration_ms:
            end_ms = min(end_ms, duration_ms)
        return build_result(
            status="ok",
            clip_length_sec=clip_length_sec,
            start_ms=start_ms,
            end_ms=end_ms,
            note="candidate window detected by pychorus",
        )
    except Exception as exc:
        return build_result(
            status="error",
            clip_length_sec=clip_length_sec,
            note="pychorus analysis failed",
            error=str(exc),
        )


def track_matches(track: dict[str, Any], track_index: int, target_index: int | None, target_id: int | None) -> bool:
    if target_index is not None and track_index != target_index:
        return False
    if target_id is not None and int(track.get("id") or 0) != int(target_id):
        return False
    return True


def apply_candidates(
    input_path: str,
    output_path: str,
    clip_length_sec: float = DEFAULT_CLIP_LENGTH_SEC,
    track_index: int | None = None,
    track_id: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tracks = data.get("tracks") or []
    processed = 0
    skipped = 0

    for index, track in enumerate(tracks):
        if not track_matches(track, index, track_index, track_id):
            continue

        existing_status = track.get("chorus_candidate_status")
        if existing_status == "ok" and not overwrite:
            skipped += 1
            continue

        result = analyze_track(track, clip_length_sec=clip_length_sec)
        track.update(result)
        if track.get("highlight_start_ms") is not None and result.get("chorus_candidate_start_ms") is not None:
            track["chorus_vs_highlight_delta_ms"] = int(result["chorus_candidate_start_ms"]) - int(track["highlight_start_ms"])
        processed += 1

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "tracks_total": len(tracks),
        "processed": processed,
        "skipped": skipped,
        "output_path": output_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pychorus candidate windows for playlist tracks")
    parser.add_argument("input_path", nargs="?", default="researched_playlist.json")
    parser.add_argument("output_path", nargs="?", default="researched_playlist.json")
    parser.add_argument("--clip-length", dest="clip_length_sec", type=float, default=DEFAULT_CLIP_LENGTH_SEC)
    parser.add_argument("--track-index", dest="track_index", type=int, default=None)
    parser.add_argument("--track-id", dest="track_id", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    summary = apply_candidates(
        input_path=args.input_path,
        output_path=args.output_path,
        clip_length_sec=args.clip_length_sec,
        track_index=args.track_index,
        track_id=args.track_id,
        overwrite=args.overwrite,
    )
    print(
        "[*] pychorus candidates updated: "
        f"processed={summary['processed']} skipped={summary['skipped']} "
        f"tracks_total={summary['tracks_total']} output={summary['output_path']}"
    )


if __name__ == "__main__":
    main()
