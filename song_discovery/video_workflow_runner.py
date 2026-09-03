"""Background process that runs the video workflow with ordered_track_ids as the authoritative source."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from song_discovery.db import DiscoveryDB
from src.crawler.crawler import crawl_playlist_from_track_ids


def run_video_workflow(
    db_path: str,
    job_id: str,
    job_dir_str: str,
    url: Optional[str] = None,
    resume_from_researched: bool = False,
    resume_from_raw: bool = False,
) -> int:
    db = DiscoveryDB(db_path)
    job_dir = Path(job_dir_str)
    job_dir.mkdir(parents=True, exist_ok=True)

    job = db.get_video_workflow_job(job_id)
    if not job:
        error_msg = f"Job '{job_id}' not found in database"
        print(f"[!] {error_msg}", file=sys.stderr)
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return 1

    ordered_track_ids = job.get("ordered_track_ids") or []
    if not ordered_track_ids:
        error_msg = f"Job '{job_id}' has no ordered_track_ids. Cannot proceed without authoritative track list."
        print(f"[!] {error_msg}", file=sys.stderr)
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return 1

    raw_path = job_dir / "raw_playlist.json"
    researched_path = job_dir / "researched_playlist.json"
    playlist_id = job.get("playlist_id")
    playlist_url = url or job.get("playlist_url")

    print(f"[*] Starting video workflow job '{job_id}' with {len(ordered_track_ids)} authoritative tracks...")
    print(f"[*] Track IDs: {ordered_track_ids}")

    if not resume_from_researched and not resume_from_raw:
        # 1. Generate raw_playlist.json strictly from ordered_track_ids
        try:
            crawl_playlist_from_track_ids(
                track_ids=ordered_track_ids,
                playlist_id=playlist_id,
                url=playlist_url,
                output_path=str(raw_path),
            )
        except Exception as exc:
            error_msg = f"Failed to generate raw_playlist.json from ordered_track_ids: {exc}"
            print(f"[!] {error_msg}", file=sys.stderr)
            db.update_video_workflow_job(job_id, "failed", error=error_msg)
            return 1

    # 2. Strict validation of the generated raw_playlist.json
    validation_path = researched_path if resume_from_researched else raw_path
    if not validation_path.exists():
        error_msg = f"{'researched_playlist.json' if resume_from_researched else 'raw_playlist.json'} was not found at {validation_path}"
        print(f"[!] {error_msg}", file=sys.stderr)
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return 1

    try:
        with validation_path.open("r", encoding="utf-8") as f:
            raw_data = json.load(f)
        tracks = raw_data.get("tracks") or []
        if len(tracks) != len(ordered_track_ids):
            raise ValueError(
                f"Track count mismatch in {validation_path.name}: expected {len(ordered_track_ids)}, got {len(tracks)}"
            )
        actual_ids = [str(t.get("id")) for t in tracks]
        expected_ids = [str(tid) for tid in ordered_track_ids]
        if actual_ids != expected_ids:
            raise ValueError(
                f"Track order mismatch in {validation_path.name}: expected {expected_ids}, got {actual_ids}"
            )
    except Exception as exc:
        error_msg = f"Validation failed for generated raw_playlist.json: {exc}"
        print(f"[!] {error_msg}", file=sys.stderr)
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return 1

    # 3. Run downstream pipeline (research, TTS, render) from raw_playlist.json
    if resume_from_researched:
        command = [
            sys.executable, "run.py", "resume_researched", "--project", str(researched_path),
            "--artifact-dir", str(job_dir),
        ]
    else:
        command = [
            sys.executable, "run.py", "from_raw",
            "--raw-path", str(raw_path),
            "--project-output", str(researched_path),
            "--artifact-dir", str(job_dir),
        ]
    try:
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            required_artifacts = [
                job_dir / "intro.mp4",
                job_dir / "song_info.md",
                job_dir / "release_report.md",
                job_dir / "publish_copy.md",
                job_dir / "final_video.mp4",
            ]
            missing = [str(path) for path in required_artifacts if not path.is_file() or path.stat().st_size == 0]
            if missing:
                error_msg = f"pipeline returned success but required artifacts are missing: {missing}"
                db.update_video_workflow_job(job_id, "failed", error=error_msg)
                return 1
            db.update_video_workflow_job(job_id, "completed")
            return 0
        error_msg = f"pipeline exit code {result.returncode}"
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return result.returncode
    except Exception as exc:
        error_msg = str(exc)
        db.update_video_workflow_job(job_id, "failed", error=error_msg)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--url", required=False, default=None)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--resume-from-researched", action="store_true")
    parser.add_argument("--resume-from-raw", action="store_true")
    args = parser.parse_args()
    return run_video_workflow(
        db_path=args.db,
        job_id=args.job,
        job_dir_str=args.job_dir,
        url=args.url,
        resume_from_researched=args.resume_from_researched,
        resume_from_raw=args.resume_from_raw,
    )


if __name__ == "__main__":
    raise SystemExit(main())
