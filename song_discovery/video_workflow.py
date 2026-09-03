"""Detached handoff from playlist publication to the existing video pipeline."""

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

from song_discovery.db import DiscoveryDB


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def start_video_workflow(
    db: DiscoveryDB,
    publication_id: str,
    playlist_id: str,
    playlist_url: str,
    ordered_track_ids: List[str],
) -> Dict[str, Any]:
    job_id = f"video_{uuid.uuid4().hex[:12]}"
    job_dir = PROJECT_ROOT / "output" / "video_jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "workflow.log"
    job = {
        "job_id": job_id,
        "publication_id": publication_id,
        "playlist_id": str(playlist_id),
        "playlist_url": playlist_url,
        "ordered_track_ids": [str(value) for value in ordered_track_ids],
        "status": "queued",
        "log_path": str(log_path),
    }
    db.create_video_workflow_job(job)
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "song_discovery.video_workflow_runner",
                "--db", str(Path(db.db_path).resolve()), "--job", job_id,
                "--url", playlist_url, "--job-dir", str(job_dir),
            ],
            cwd=str(PROJECT_ROOT), stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    db.update_video_workflow_job(job_id, "running", pid=process.pid)
    return {**job, "status": "running", "pid": process.pid}


def resume_video_workflow(db: DiscoveryDB, job_id: str) -> Dict[str, Any]:
    """Resume an existing failed/cancelled job from its validated research output or raw data."""
    job = db.get_video_workflow_job(job_id)
    if not job:
        raise ValueError(f"Video workflow job not found: {job_id}")
    original_status = str(job.get("status") or "")
    if original_status not in {"failed", "cancelled"}:
        raise ValueError(f"Video workflow job is not resumable from status: {job.get('status')}")

    log_path_from_db = Path(str(job.get("log_path") or "")).resolve()
    job_dir = log_path_from_db.parent
    if log_path_from_db.name != "workflow.log" or job_dir.name != job_id or job_dir.parent.name != "video_jobs":
        raise ValueError(f"Invalid video workflow job directory: {job_id}")
    researched_path = job_dir / "researched_playlist.json"
    raw_path = job_dir / "raw_playlist.json"

    if not db.claim_video_workflow_resume(job_id):
        raise ValueError(f"Video workflow job is already being resumed or is no longer resumable: {job_id}")

    cmd = [
        sys.executable, "-m", "song_discovery.video_workflow_runner",
        "--db", str(Path(db.db_path).resolve()),
        "--job", job_id,
        "--job-dir", str(job_dir),
    ]
    if researched_path.exists():
        cmd.append("--resume-from-researched")
    elif raw_path.exists():
        cmd.append("--resume-from-raw")
    elif job.get("playlist_url"):
        cmd.extend(["--url", str(job["playlist_url"])])
    else:
        db.update_video_workflow_job(job_id, original_status, error=str(job.get("error") or ""))
        raise ValueError(f"Cannot resume job {job_id}: missing existing data and playlist URL")

    log_path = job_dir / "workflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT), stdout=log_file, stderr=subprocess.STDOUT,
                start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
    except Exception as exc:
        db.update_video_workflow_job(job_id, "failed", error=f"Resume launch failed: {exc}")
        raise
    db.update_video_workflow_job(job_id, "running", pid=process.pid, error="")
    return {**db.get_video_workflow_job(job_id), "pid": process.pid}
