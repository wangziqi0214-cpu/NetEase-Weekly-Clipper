"""SQLite persistence layer for song discovery releases, candidates, runs, reviews, and publications."""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from song_discovery.models import REASON_SCOPE_LABELS, REJECTION_REASON_LABELS, Release, Track


class DiscoveryDB:
    """Manages SQLite database for new releases, candidates, human reviews, and playlist publications."""

    def __init__(self, db_path: str = "output/discovery.db"):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 1. Runs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT UNIQUE NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    collected_count INTEGER DEFAULT 0,
                    candidates_count INTEGER DEFAULT 0,
                    notes TEXT
                )
            """)

            # 2. Releases table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist_names TEXT NOT NULL,
                    release_type TEXT,
                    release_date TEXT,
                    track_count INTEGER DEFAULT 0,
                    source_url TEXT,
                    raw_metadata TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(platform, source_id)
                )
            """)

            # 3. Candidates table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    release_source_id TEXT NOT NULL,
                    track_source_id TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    track_title TEXT NOT NULL,
                    artist_names TEXT NOT NULL,
                    release_type TEXT,
                    release_date TEXT,
                    duration_ms INTEGER,
                    track_number INTEGER,
                    release_url TEXT,
                    track_url TEXT,
                    selection_rule TEXT,
                    relevance_score REAL NOT NULL DEFAULT 50.0,
                    relevance_reasons TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    review_notes TEXT,
                    reviewed_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    raw_metadata TEXT,
                    UNIQUE(platform, release_source_id, track_source_id)
                )
            """)

            # 4. Review History table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
                )
            """)

            # 5. Publications table (NetEase weekly playlist publication batch)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT UNIQUE NOT NULL,
                    playlist_id TEXT NOT NULL,
                    playlist_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_tracks INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    notes TEXT
                )
            """)

            # 6. Published Tracks table (Tracks published per playlist)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS published_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT NOT NULL,
                    candidate_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    original_track_id TEXT NOT NULL,
                    netease_track_id TEXT,
                    match_status TEXT NOT NULL,
                    match_confidence REAL DEFAULT 0.0,
                    match_details TEXT,
                    added_to_playlist INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(publication_id, candidate_id),
                    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
                )
            """)

            # 7. Normalized Review Feedbacks table (Active Learning & User Preference Loop)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_feedbacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason_code TEXT,
                    reason_scope TEXT DEFAULT 'track',
                    note TEXT DEFAULT '',
                    feature_snapshot TEXT,
                    model_version TEXT NOT NULL DEFAULT 'v0_cold_start',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
                )
            """)

            # 8. Learner Models Registry table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS learner_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    sample_count INTEGER DEFAULT 0,
                    positive_count INTEGER DEFAULT 0,
                    negative_count INTEGER DEFAULT 0,
                    metrics TEXT,
                    weights_json TEXT,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)

            # 9. Human-confirmed cross-platform catalog matches. Kept separate
            # from collector metadata so a future refresh cannot overwrite it.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS manual_match_overrides (
                    candidate_id INTEGER PRIMARY KEY,
                    netease_track_id TEXT NOT NULL,
                    matched_title TEXT NOT NULL DEFAULT '',
                    matched_artists TEXT NOT NULL DEFAULT '',
                    target_release_date TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
                )
            """)
            override_columns = {row[1] for row in cursor.execute("PRAGMA table_info(manual_match_overrides)").fetchall()}
            if "target_release_date" not in override_columns:
                cursor.execute("ALTER TABLE manual_match_overrides ADD COLUMN target_release_date TEXT NOT NULL DEFAULT ''")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS delivery_orders (
                    playlist_name TEXT PRIMARY KEY,
                    candidate_ids TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS video_workflow_jobs (
                    job_id TEXT PRIMARY KEY,
                    publication_id TEXT,
                    playlist_id TEXT NOT NULL,
                    playlist_url TEXT NOT NULL,
                    ordered_track_ids TEXT NOT NULL,
                    status TEXT NOT NULL,
                    log_path TEXT NOT NULL,
                    pid INTEGER,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Indices
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_releases_platform_source ON releases(platform, source_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(review_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_platform ON candidates(platform)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(relevance_score)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pub_tracks_playlist ON published_tracks(netease_track_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_publications_playlist_name ON publications(playlist_name, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedbacks_candidate ON review_feedbacks(candidate_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedbacks_decision ON review_feedbacks(decision)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_models_status ON learner_models(status)")
            conn.commit()

    def set_manual_match_override(
        self,
        candidate_id: int,
        netease_track_id: str,
        matched_title: str = "",
        matched_artists: str = "",
        target_release_date: str = "",
        notes: str = "",
    ) -> None:
        """Persist a human-confirmed NetEase mapping without altering source metadata."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            exists = conn.execute("SELECT 1 FROM candidates WHERE id = ?", (int(candidate_id),)).fetchone()
            if not exists:
                raise ValueError(f"Candidate {candidate_id} does not exist")
            conn.execute(
                """
                INSERT INTO manual_match_overrides (
                    candidate_id, netease_track_id, matched_title, matched_artists, target_release_date,
                    notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    netease_track_id = excluded.netease_track_id,
                    matched_title = excluded.matched_title,
                    matched_artists = excluded.matched_artists,
                    target_release_date = excluded.target_release_date,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    int(candidate_id), str(netease_track_id), matched_title,
                    matched_artists, target_release_date, notes, now, now,
                ),
            )
            conn.commit()

    def get_manual_match_override(self, candidate_id: int) -> Optional[Dict[str, Any]]:
        """Return the human-confirmed NetEase mapping for one candidate."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM manual_match_overrides WHERE candidate_id = ?",
                (int(candidate_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_manual_match_override(self, candidate_id: int) -> bool:
        """Delete manual match override for a candidate."""
        with self.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM manual_match_overrides WHERE candidate_id = ?",
                (int(candidate_id),),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_all_manual_match_overrides(self) -> List[Dict[str, Any]]:
        """Return all manual match overrides ordered by updated time."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM manual_match_overrides ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_delivery_order(self, playlist_name: str, candidate_ids: List[int]) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = json.dumps([int(value) for value in candidate_ids])
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO delivery_orders (playlist_name, candidate_ids, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(playlist_name) DO UPDATE SET candidate_ids=excluded.candidate_ids, updated_at=excluded.updated_at",
                (playlist_name, payload, now),
            )
            conn.commit()

    def get_delivery_order(self, playlist_name: str) -> List[int]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT candidate_ids FROM delivery_orders WHERE playlist_name=?", (playlist_name,)).fetchone()
        return [int(value) for value in json.loads(row["candidate_ids"])] if row else []

    def create_video_workflow_job(self, job: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO video_workflow_jobs (job_id, publication_id, playlist_id, playlist_url, ordered_track_ids, status, log_path, pid, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job["job_id"], job.get("publication_id"), job["playlist_id"], job["playlist_url"], json.dumps(job.get("ordered_track_ids") or []), job.get("status", "queued"), job["log_path"], job.get("pid"), job.get("error", ""), now, now),
            )
            conn.commit()

    def update_video_workflow_job(self, job_id: str, status: str, *, pid: Optional[int] = None, error: str = "") -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            conn.execute("UPDATE video_workflow_jobs SET status=?, pid=COALESCE(?,pid), error=?, updated_at=? WHERE job_id=?", (status, pid, error, now, job_id))
            conn.commit()

    def claim_video_workflow_resume(self, job_id: str) -> bool:
        """Atomically claim a failed/cancelled job for one resume attempt."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE video_workflow_jobs
                SET status='queued', error='', updated_at=?
                WHERE job_id=? AND status IN ('failed', 'cancelled')
                """,
                (now, job_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def get_latest_video_workflow_job(self) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM video_workflow_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = dict(row)
        result["ordered_track_ids"] = json.loads(result.get("ordered_track_ids") or "[]")
        return result

    def get_video_workflow_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return all video workflow jobs ordered by creation time descending."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM video_workflow_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["ordered_track_ids"] = json.loads(item.get("ordered_track_ids") or "[]")
            results.append(item)
        return results

    def get_video_workflow_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM video_workflow_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["ordered_track_ids"] = json.loads(result.get("ordered_track_ids") or "[]")
        return result

    def start_run(self, platform: str, notes: str = "") -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, started_at, platform, status, notes)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, now, platform, notes),
            )
            conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        collected_count: int = 0,
        candidates_count: int = 0,
        notes: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE runs
                SET completed_at = ?, status = ?, collected_count = ?, candidates_count = ?, notes = ?
                WHERE run_id = ?
                """,
                (now, status, collected_count, candidates_count, notes, run_id),
            )
            conn.commit()

    def upsert_release(self, release: Release) -> int:
        """Idempotently insert or update a release record."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_json = json.dumps(release.raw_metadata, ensure_ascii=False) if release.raw_metadata else "{}"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO releases (
                    platform, source_id, title, artist_names, release_type,
                    release_date, track_count, source_url, raw_metadata,
                    first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, source_id) DO UPDATE SET
                    title = excluded.title,
                    artist_names = excluded.artist_names,
                    release_type = excluded.release_type,
                    release_date = excluded.release_date,
                    track_count = excluded.track_count,
                    source_url = excluded.source_url,
                    raw_metadata = excluded.raw_metadata,
                    last_seen_at = excluded.last_seen_at
                RETURNING id
                """,
                (
                    release.platform,
                    release.source_id,
                    release.title,
                    release.artist_names_str,
                    release.release_type,
                    release.release_date,
                    release.track_count,
                    release.source_url,
                    raw_json,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
            return row["id"] if row else 0

    def upsert_candidate(
        self,
        platform: str,
        release_source_id: str,
        track_source_id: str,
        release_title: str,
        track_title: str,
        artist_names: str,
        release_type: str,
        release_date: str,
        duration_ms: Optional[int],
        track_number: Optional[int],
        release_url: str,
        track_url: str,
        selection_rule: str,
        relevance_score: float,
        relevance_reasons: List[str],
        raw_metadata: Optional[Dict[str, Any]] = None,
        initial_review_status: str = "pending",
    ) -> Tuple[int, bool]:
        """
        Idempotently insert or update candidate.
        Preserves existing review_status, review_notes, reviewed_at if already reviewed.
        Returns: (candidate_id, is_new)
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_initial_statuses = {"pending", "machine_filtered"}
        if initial_review_status not in valid_initial_statuses:
            raise ValueError(f"Invalid initial review status: {initial_review_status}")
        reasons_json = json.dumps(relevance_reasons, ensure_ascii=False)
        raw_json = json.dumps(raw_metadata, ensure_ascii=False) if raw_metadata else "{}"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, review_status FROM candidates
                WHERE platform = ? AND release_source_id = ? AND track_source_id = ?
                """,
                (platform, release_source_id, track_source_id),
            )
            existing = cursor.fetchone()

            if existing:
                cid = existing["id"]
                cursor.execute(
                    """
                    UPDATE candidates
                    SET release_title = ?,
                        track_title = ?,
                        artist_names = ?,
                        release_type = ?,
                        release_date = ?,
                        duration_ms = ?,
                        track_number = ?,
                        release_url = ?,
                        track_url = ?,
                        selection_rule = ?,
                        relevance_score = ?,
                        relevance_reasons = ?,
                        raw_metadata = ?,
                        review_status = CASE
                            WHEN review_status = 'machine_filtered' THEN ?
                            ELSE review_status
                        END,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        release_title,
                        track_title,
                        artist_names,
                        release_type,
                        release_date,
                        duration_ms,
                        track_number,
                        release_url,
                        track_url,
                        selection_rule,
                        relevance_score,
                        reasons_json,
                        raw_json,
                        initial_review_status,
                        now,
                        cid,
                    ),
                )
                conn.commit()
                return cid, False
            else:
                cursor.execute(
                    """
                    INSERT INTO candidates (
                        platform, release_source_id, track_source_id,
                        release_title, track_title, artist_names,
                        release_type, release_date, duration_ms, track_number,
                        release_url, track_url, selection_rule,
                        relevance_score, relevance_reasons, review_status,
                        first_seen_at, last_seen_at, raw_metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        platform,
                        release_source_id,
                        track_source_id,
                        release_title,
                        track_title,
                        artist_names,
                        release_type,
                        release_date,
                        duration_ms,
                        track_number,
                        release_url,
                        track_url,
                        selection_rule,
                        relevance_score,
                        reasons_json,
                        initial_review_status,
                        now,
                        now,
                        raw_json,
                    ),
                )
                cid = cursor.lastrowid
                conn.commit()
                return cid, True

    def update_review_status(
        self,
        candidate_id: int,
        status: str,
        notes: Optional[str] = None,
        reason_code: Optional[str] = None,
        reason_scope: str = "track",
        feature_snapshot: Optional[Dict[str, Any]] = None,
        model_version: str = "v0_cold_start",
    ) -> bool:
        # Backward-compatible callers may not know about structured reasons.
        # Preserve the decision while recording an explicit neutral legacy code.
        if status.lower().strip() == "rejected" and not reason_code:
            reason_code = "other"
        return self.record_feedback(
            candidate_id=candidate_id,
            decision=status,
            reason_code=reason_code,
            reason_scope=reason_scope,
            note=notes,
            feature_snapshot=feature_snapshot,
            model_version=model_version,
        )

    def record_feedback(
        self,
        candidate_id: int,
        decision: str,
        reason_code: Optional[str] = None,
        reason_scope: str = "track",
        note: Optional[str] = None,
        feature_snapshot: Optional[Dict[str, Any]] = None,
        model_version: str = "v0_cold_start",
    ) -> bool:
        """
        Record a normalized feedback decision with reason, scope, feature snapshot, and model version.
        Synchronously updates candidate status and review_history for full backward compatibility.
        """
        valid_statuses = {"pending", "approved", "rejected", "deferred", "machine_filtered"}
        decision_val = decision.lower().strip()
        if decision_val not in valid_statuses:
            raise ValueError(f"Invalid review status: {decision}. Must be one of {valid_statuses}")
        if reason_scope not in REASON_SCOPE_LABELS:
            raise ValueError(f"Invalid rejection scope: {reason_scope}")
        if decision_val == "rejected":
            if not reason_code or reason_code not in REJECTION_REASON_LABELS:
                raise ValueError("Rejected feedback requires a valid structured reason_code")
        else:
            reason_code = None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
            cand_row = cursor.fetchone()
            if not cand_row:
                return False

            prev_status = cand_row["review_status"]

            # Construct feature snapshot if not supplied
            if feature_snapshot is None:
                snapshot_data = {
                    "platform": cand_row["platform"],
                    "track_title": cand_row["track_title"],
                    "artist_names": cand_row["artist_names"],
                    "release_title": cand_row["release_title"],
                    "release_type": cand_row["release_type"],
                    "release_date": cand_row["release_date"],
                    "duration_ms": cand_row["duration_ms"],
                    "track_number": cand_row["track_number"],
                    "selection_rule": cand_row["selection_rule"],
                    "relevance_score": cand_row["relevance_score"],
                    "relevance_reasons": cand_row["relevance_reasons"],
                }
            else:
                snapshot_data = feature_snapshot

            snapshot_json = json.dumps(snapshot_data, ensure_ascii=False) if isinstance(snapshot_data, dict) else str(snapshot_data)

            # 1. Update candidate table
            cursor.execute(
                """
                UPDATE candidates
                SET review_status = ?, review_notes = coalesce(?, review_notes), reviewed_at = ?
                WHERE id = ?
                """,
                (decision_val, note, now, candidate_id),
            )

            # 2. Insert into review_history (backward compatibility)
            cursor.execute(
                """
                INSERT INTO review_history (candidate_id, previous_status, new_status, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (candidate_id, prev_status, decision_val, note or "", now),
            )

            # 3. Insert into review_feedbacks
            cursor.execute(
                """
                INSERT INTO review_feedbacks (
                    candidate_id, decision, reason_code, reason_scope,
                    note, feature_snapshot, model_version, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    decision_val,
                    reason_code,
                    reason_scope or "track",
                    note or "",
                    snapshot_json,
                    model_version or "v0_cold_start",
                    now,
                ),
            )

            conn.commit()
            return True

    def bulk_update_review_status(self, updates: List[Dict[str, Any]]) -> int:
        return self.bulk_record_feedbacks(updates)

    def bulk_sync_review_status(
        self,
        candidate_ids: List[int],
        status: str,
        notes: Optional[str] = None,
    ) -> int:
        """Synchronize duplicate platform rows without duplicating human-learning samples."""
        valid_statuses = {"pending", "approved", "rejected", "deferred", "machine_filtered"}
        status_value = str(status).lower().strip()
        if status_value not in valid_statuses:
            raise ValueError(f"Invalid review status: {status}")
        if not candidate_ids:
            return 0

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated_count = 0
        with self.get_connection() as conn:
            for candidate_id in sorted({int(value) for value in candidate_ids}):
                row = conn.execute(
                    "SELECT review_status FROM candidates WHERE id = ?", (candidate_id,)
                ).fetchone()
                if not row:
                    continue
                previous_status = row["review_status"]
                conn.execute(
                    """
                    UPDATE candidates
                    SET review_status = ?, review_notes = coalesce(?, review_notes), reviewed_at = ?
                    WHERE id = ?
                    """,
                    (status_value, notes or None, now, candidate_id),
                )
                conn.execute(
                    """
                    INSERT INTO review_history
                        (candidate_id, previous_status, new_status, notes, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (candidate_id, previous_status, status_value, notes or "", now),
                )
                updated_count += 1
            conn.commit()
        return updated_count

    def bulk_record_feedbacks(self, updates: List[Dict[str, Any]]) -> int:
        """Bulk record multiple feedback decisions transactionally."""
        updated_count = 0
        for item in updates:
            cid = item.get("candidate_id") or item.get("id")
            st = item.get("status") or item.get("decision")
            nt = item.get("notes") or item.get("note")
            rc = item.get("reason_code")
            if str(st).lower().strip() == "rejected" and not rc:
                rc = "other"
            rs = item.get("reason_scope", "track")
            fs = item.get("feature_snapshot")
            mv = item.get("model_version", "v0_cold_start")
            if cid and st:
                if self.record_feedback(
                    candidate_id=int(cid),
                    decision=str(st),
                    reason_code=rc,
                    reason_scope=rs,
                    note=nt,
                    feature_snapshot=fs,
                    model_version=mv,
                ):
                    updated_count += 1
        return updated_count

    def set_machine_filtered(self, candidate_id: int, note: str = "") -> bool:
        """Apply an automated routing decision without creating human feedback."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT review_status FROM candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if not row or row["review_status"] != "pending":
                return False
            conn.execute(
                "UPDATE candidates SET review_status = 'machine_filtered', review_notes = coalesce(?, review_notes) WHERE id = ?",
                (note or None, candidate_id),
            )
            conn.execute(
                "INSERT INTO review_history (candidate_id, previous_status, new_status, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, "pending", "machine_filtered", note, now),
            )
            conn.commit()
            return True

    def get_feedbacks(
        self,
        decision: Optional[str] = None,
        candidate_id: Optional[int] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """Retrieve feedback records with parsed feature snapshots."""
        query = """
            SELECT rf.*, c.track_title, c.artist_names, c.release_title, c.platform as cand_platform,
                   c.release_type as cand_release_type, c.relevance_score as cand_score,
                   c.relevance_reasons as cand_reasons
            FROM review_feedbacks rf
            LEFT JOIN candidates c ON rf.candidate_id = c.id
            WHERE 1=1
        """
        params: List[Any] = []
        if decision and decision.lower() != "all":
            query += " AND rf.decision = ?"
            params.append(decision.lower())
        if candidate_id is not None:
            query += " AND rf.candidate_id = ?"
            params.append(candidate_id)

        query += " ORDER BY rf.id DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            feedbacks = []
            for r in rows:
                d = dict(r)
                if d.get("feature_snapshot"):
                    try:
                        d["feature_snapshot"] = json.loads(d["feature_snapshot"])
                    except Exception:
                        pass
                # Extract convenience fields for training
                snap = d.get("feature_snapshot") if isinstance(d.get("feature_snapshot"), dict) else {}
                d["artist_names"] = d.get("artist_names") or snap.get("artist_names", "")
                d["track_title"] = d.get("track_title") or snap.get("track_title", "")
                d["release_title"] = d.get("release_title") or snap.get("release_title", "")
                d["release_type"] = d.get("cand_release_type") or snap.get("release_type", "other")
                d["platform"] = d.get("cand_platform") or snap.get("platform", "unknown")
                d["relevance_score"] = d.get("cand_score") or snap.get("relevance_score", 50.0)
                d["relevance_reasons"] = d.get("cand_reasons") or snap.get("relevance_reasons", [])
                feedbacks.append(d)
            return feedbacks

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Aggregate the latest human decision per candidate, not raw history rows."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM review_feedbacks")
            history_total = cursor.fetchone()["total"]

            cursor.execute(
                """
                WITH latest AS (
                    SELECT rf.* FROM review_feedbacks rf
                    WHERE rf.id = (
                        SELECT MAX(rf2.id) FROM review_feedbacks rf2
                        WHERE rf2.candidate_id = rf.candidate_id
                    )
                )
                SELECT decision, COUNT(*) AS cnt
                FROM latest
                GROUP BY decision
                """
            )
            decision_counts = {r["decision"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute(
                """
                WITH latest AS (
                    SELECT rf.* FROM review_feedbacks rf
                    WHERE rf.id = (SELECT MAX(rf2.id) FROM review_feedbacks rf2 WHERE rf2.candidate_id = rf.candidate_id)
                )
                SELECT reason_code, COUNT(*) as cnt FROM latest
                WHERE decision = 'rejected' AND reason_code IS NOT NULL AND reason_code != ''
                GROUP BY reason_code
                """
            )
            reason_counts = {r["reason_code"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute(
                """
                WITH latest AS (
                    SELECT rf.* FROM review_feedbacks rf
                    WHERE rf.id = (SELECT MAX(rf2.id) FROM review_feedbacks rf2 WHERE rf2.candidate_id = rf.candidate_id)
                )
                SELECT reason_scope, COUNT(*) as cnt FROM latest
                WHERE decision = 'rejected' AND reason_scope IS NOT NULL AND reason_scope != ''
                GROUP BY reason_scope
                """
            )
            scope_counts = {r["reason_scope"]: r["cnt"] for r in cursor.fetchall()}

            return {
                "total": decision_counts.get("approved", 0) + decision_counts.get("rejected", 0),
                "history_total": history_total,
                "approved": decision_counts.get("approved", 0),
                "rejected": decision_counts.get("rejected", 0),
                "deferred": decision_counts.get("deferred", 0),
                "pending": decision_counts.get("pending", 0),
                "reasons": reason_counts,
                "scopes": scope_counts,
            }

    def save_model_version(
        self,
        version_id: str,
        weights: Dict[str, Any],
        metrics: Dict[str, Any],
        sample_stats: Optional[Dict[str, int]] = None,
        status: str = "active",
        notes: str = "",
    ) -> int:
        """Register or update a personalized model version."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        weights_json = json.dumps(weights, ensure_ascii=False)
        metrics_json = json.dumps(metrics, ensure_ascii=False)
        stats = sample_stats or {}
        sample_count = stats.get("total", 0)
        pos_count = stats.get("positive", 0)
        neg_count = stats.get("negative", 0)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status == "active":
                # Deactivate current active models
                cursor.execute("UPDATE learner_models SET status = 'archived' WHERE status = 'active'")

            cursor.execute(
                """
                INSERT INTO learner_models (
                    version_id, status, sample_count, positive_count,
                    negative_count, metrics, weights_json, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    status = excluded.status,
                    sample_count = excluded.sample_count,
                    positive_count = excluded.positive_count,
                    negative_count = excluded.negative_count,
                    metrics = excluded.metrics,
                    weights_json = excluded.weights_json,
                    notes = excluded.notes,
                    created_at = excluded.created_at
                RETURNING id
                """,
                (
                    version_id,
                    status,
                    sample_count,
                    pos_count,
                    neg_count,
                    metrics_json,
                    weights_json,
                    notes,
                    now,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
            return row["id"] if row else 0

    def get_active_model_version(self) -> Optional[Dict[str, Any]]:
        """Retrieve the currently active model record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM learner_models
                WHERE status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("weights_json"):
                try:
                    d["weights"] = json.loads(d["weights_json"])
                except Exception:
                    d["weights"] = {}
            if d.get("metrics"):
                try:
                    d["metrics"] = json.loads(d["metrics"])
                except Exception:
                    d["metrics"] = {}
            return d

    def get_all_model_versions(self) -> List[Dict[str, Any]]:
        """List all model version records ordered by creation date descending."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM learner_models ORDER BY created_at DESC, id DESC")
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("weights_json"):
                    try:
                        d["weights"] = json.loads(d["weights_json"])
                    except Exception:
                        d["weights"] = {}
                if d.get("metrics"):
                    try:
                        d["metrics"] = json.loads(d["metrics"])
                    except Exception:
                        d["metrics"] = {}
                results.append(d)
            return results

    def rollback_model_version(self, target_version_id: str) -> bool:
        """Roll back active model to a specified previous version."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM learner_models WHERE version_id = ?", (target_version_id,))
            target = cursor.fetchone()
            if not target:
                return False

            cursor.execute("UPDATE learner_models SET status = 'archived' WHERE status = 'active'")
            cursor.execute("UPDATE learner_models SET status = 'active' WHERE version_id = ?", (target_version_id,))
            conn.commit()
            return True


    def get_candidates(
        self,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        min_score: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = """
            SELECT c.*,
                r.raw_metadata AS release_raw_metadata,
                (SELECT rf.reason_code FROM review_feedbacks rf WHERE rf.candidate_id = c.id ORDER BY rf.id DESC LIMIT 1) AS reason_code,
                (SELECT rf.reason_scope FROM review_feedbacks rf WHERE rf.candidate_id = c.id ORDER BY rf.id DESC LIMIT 1) AS reason_scope,
                (SELECT rf.model_version FROM review_feedbacks rf WHERE rf.candidate_id = c.id ORDER BY rf.id DESC LIMIT 1) AS feedback_model_version
            FROM candidates c
            LEFT JOIN releases r
              ON r.platform = c.platform AND r.source_id = c.release_source_id
            WHERE 1=1
        """
        params: List[Any] = []

        if status and status.lower() != "all":
            query += " AND c.review_status = ?"
            params.append(status.lower())

        if platform and platform.lower() != "all":
            query += " AND c.platform = ?"
            params.append(platform.lower())

        if min_score is not None:
            query += " AND c.relevance_score >= ?"
            params.append(min_score)

        query += " ORDER BY c.relevance_score DESC, c.id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("relevance_reasons"):
                    try:
                        d["relevance_reasons"] = json.loads(d["relevance_reasons"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_candidate_by_id(self, candidate_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.*, r.raw_metadata AS release_raw_metadata
                FROM candidates c
                LEFT JOIN releases r
                  ON r.platform = c.platform AND r.source_id = c.release_source_id
                WHERE c.id = ?
                """,
                (candidate_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("relevance_reasons"):
                try:
                    d["relevance_reasons"] = json.loads(d["relevance_reasons"])
                except Exception:
                    pass
            return d

    def export_candidates(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.get_candidates(status=status, limit=10000)

    def create_publication(
        self,
        playlist_id: str,
        playlist_name: str,
        status: str = "success",
        total_tracks: int = 0,
        notes: str = "",
    ) -> str:
        pub_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO publications (publication_id, playlist_id, playlist_name, status, total_tracks, created_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (pub_id, playlist_id, playlist_name, status, total_tracks, now, notes),
            )
            conn.commit()
        return pub_id

    def record_published_tracks(self, publication_id: str, track_records: List[Dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        added_cnt = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for tr in track_records:
                details_json = json.dumps(tr.get("match_details") or {}, ensure_ascii=False)
                cursor.execute(
                    """
                    INSERT INTO published_tracks (
                        publication_id, candidate_id, platform, original_track_id,
                        netease_track_id, match_status, match_confidence,
                        match_details, added_to_playlist, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(publication_id, candidate_id) DO UPDATE SET
                        netease_track_id = excluded.netease_track_id,
                        match_status = excluded.match_status,
                        match_confidence = excluded.match_confidence,
                        match_details = excluded.match_details,
                        added_to_playlist = excluded.added_to_playlist
                    """,
                    (
                        publication_id,
                        tr["candidate_id"],
                        tr["platform"],
                        tr["original_track_id"],
                        tr.get("netease_track_id"),
                        tr["match_status"],
                        tr.get("match_confidence", 0.0),
                        details_json,
                        1 if tr.get("added_to_playlist") else 0,
                        now,
                    ),
                )
                added_cnt += 1
            conn.commit()
        return added_cnt

    def get_already_published_track_ids(self, playlist_id: str) -> Set[str]:
        """Get set of netease_track_ids already recorded as added to this playlist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT pt.netease_track_id
                FROM published_tracks pt
                JOIN publications p ON pt.publication_id = p.publication_id
                WHERE p.playlist_id = ? AND pt.added_to_playlist = 1 AND pt.netease_track_id IS NOT NULL
                """,
                (playlist_id,),
            )
            return {row["netease_track_id"] for row in cursor.fetchall() if row["netease_track_id"]}

    def get_latest_publication_by_name(self, playlist_name: str) -> Optional[Dict[str, Any]]:
        """Return the latest successful publication for a playlist name."""
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM publications
                WHERE playlist_name = ? AND status = 'success'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (playlist_name,),
            ).fetchone()
            return dict(row) if row else None

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM releases")
            total_releases = cursor.fetchone()["total"]

            cursor.execute("SELECT COUNT(*) as total FROM candidates")
            total_candidates = cursor.fetchone()["total"]

            cursor.execute(
                """
                SELECT review_status, COUNT(*) as cnt
                FROM candidates
                GROUP BY review_status
                """
            )
            status_counts = {r["review_status"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute(
                """
                SELECT platform, COUNT(*) as cnt
                FROM candidates
                GROUP BY platform
                """
            )
            platform_counts = {r["platform"]: r["cnt"] for r in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) as total FROM publications")
            total_publications = cursor.fetchone()["total"]

            return {
                "total_releases": total_releases,
                "total_candidates": total_candidates,
                "total_publications": total_publications,
                "pending": status_counts.get("pending", 0),
                "approved": status_counts.get("approved", 0),
                "rejected": status_counts.get("rejected", 0),
                "deferred": status_counts.get("deferred", 0),
                "machine_filtered": status_counts.get("machine_filtered", 0),
                "platforms": platform_counts,
            }
