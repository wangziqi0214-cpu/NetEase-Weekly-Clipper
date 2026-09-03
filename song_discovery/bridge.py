"""HTTP Bridge for KKBOX Sidecar Session Adapter & Idempotent Command Ingestion."""

import datetime
import json
import logging
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from song_discovery.db import DiscoveryDB
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track
from song_discovery.scorer import RelevanceScorer
from song_discovery.review_helpers import get_active_preference_model, initial_review_status_for_candidate
from song_discovery.selection import EmptyReleaseError, get_selection_rule_label, select_track_from_release

logger = logging.getLogger("song_discovery.bridge")

# Max allowed payload body size (10 MB)
MAX_BODY_SIZE = 10 * 1024 * 1024
ALLOWED_SIDECAR_STATUSES = {
    "idle", "crawling", "running", "completed", "success_with_warnings",
    "no_data", "auth_required", "geoblocked", "paused_bridge_offline",
    "error", "failed", "offline",
}


def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure no credential keys or authentication cookies are retained in memory.
    Strips any Authorization, Cookie, Token, or ClientSecret keys recursively.
    """
    sensitive_keys = {
        "cookie", "cookies", "authorization", "auth", "token",
        "client_secret", "secret", "session", "password", "access_token",
    }

    def _sanitize(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _sanitize(v)
                for k, v in obj.items()
                if k.lower() not in sensitive_keys
            }
        elif isinstance(obj, list):
            return [_sanitize(item) for item in obj]
        return obj

    return _sanitize(payload)


def validate_release_schema(item: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate that an individual release object satisfies the Release model requirement.
    """
    if not isinstance(item, dict):
        return False, "Release item must be an object"

    required_fields = ["source_id", "title"]
    for field in required_fields:
        if not item.get(field):
            return False, f"Missing required release field: '{field}'"

    generic_titles = {"kkbox", "kkbox web player", "let's music - kkbox"}
    if str(item.get("title") or "").strip().lower() in generic_titles:
        return False, "Release title is a generic KKBOX placeholder"

    placeholder_artists = {"", "kkbox artist", "unknown", "unknown artist"}
    release_artists = {
        str(artist.get("name") or "").strip().lower()
        for artist in item.get("artists", []) if isinstance(artist, dict)
    }
    has_release_artist = any(name not in placeholder_artists for name in release_artists)

    tracks = item.get("tracks")
    if not isinstance(tracks, list) or len(tracks) == 0:
        return False, "Release must contain a non-empty 'tracks' list"

    for idx, t in enumerate(tracks):
        if not isinstance(t, dict):
            return False, f"Track at index {idx} must be an object"
        if not t.get("title"):
            return False, f"Track at index {idx} missing 'title'"
        track_artists = {
            str(artist.get("name") or "").strip().lower()
            for artist in t.get("artists", []) if isinstance(artist, dict)
        }
        if not has_release_artist and not any(name not in placeholder_artists for name in track_artists):
            return False, f"Track at index {idx} missing a real artist"

    return True, ""


def ingest_kkbox_payload(
    payload: Dict[str, Any],
    db: DiscoveryDB,
    scorer: Optional[RelevanceScorer] = None,
) -> Tuple[int, Dict[str, Any]]:
    """
    Process and ingest a batch of KKBOX releases into SQLite DB.
    Applies sanitization, schema validation, selection rules, and relevance scoring.
    Returns (status_code, response_dict).
    """
    if not isinstance(payload, dict):
        return 400, {"error": "Payload must be a JSON object"}

    sanitized = sanitize_payload(payload)
    raw_releases = sanitized.get("releases", [])
    if not isinstance(raw_releases, list):
        return 400, {"error": "'releases' field must be a list"}

    scorer_instance = scorer or RelevanceScorer()
    preference_model = get_active_preference_model(db)
    ingested_count = 0
    candidates_count = 0
    validation_errors = []

    for item in raw_releases:
        is_valid, err_msg = validate_release_schema(item)
        if not is_valid:
            validation_errors.append(f"Album {item.get('source_id', 'unknown')}: {err_msg}")
            continue

        # Parse into Release and Track models
        artists = [Artist(name=a.get("name", "Unknown")) for a in item.get("artists", [])]
        if not artists:
            artists = [Artist(name="KKBOX Artist")]

        release = Release(
            platform=Platform.KKBOX.value,
            source_id=str(item["source_id"]),
            title=str(item["title"]),
            artists=artists,
            album_title=str(item.get("album_title") or item["title"]),
            release_type=str(item.get("release_type") or ReleaseType.ALBUM.value),
            release_date=str(item.get("release_date") or ""),
            track_count=len(item.get("tracks", [])),
            source_url=str(item.get("source_url") or ""),
            raw_metadata=item,
        )

        tracks = []
        for idx, t_data in enumerate(item.get("tracks", []), start=1):
            track_artists = [Artist(name=a.get("name", "")) for a in t_data.get("artists", [])] or artists
            track = Track(
                platform=Platform.KKBOX.value,
                source_id=str(t_data.get("source_id") or f"{release.source_id}_{idx}"),
                title=str(t_data["title"]),
                artists=track_artists,
                album_title=release.album_title,
                release_type=release.release_type,
                release_date=release.release_date,
                duration_ms=t_data.get("duration_ms"),
                track_number=t_data.get("track_number", idx),
                source_url=str(t_data.get("source_url") or ""),
                raw_metadata=t_data,
            )
            tracks.append(track)

        release.tracks = tracks
        release.track_count = len(tracks)

        # Persist release
        db.upsert_release(release)
        ingested_count += 1

        # Apply selection rule
        try:
            target_track = select_track_from_release(release)
            rule_desc = get_selection_rule_label(release)
        except EmptyReleaseError:
            continue

        # Apply scorer
        scoring_res = scorer_instance.score_candidate(target_track, release=release)
        if not scoring_res.is_candidate:
            continue

        # Upsert candidate
        initial_status = initial_review_status_for_candidate({
            "relevance_score": scoring_res.score,
            "relevance_reasons": scoring_res.reasons,
            "artist_names": target_track.artist_names_str,
            "track_title": target_track.title,
            "release_title": release.title,
            "platform": release.platform,
            "release_type": release.release_type,
        }, model=preference_model)
        cid, _ = db.upsert_candidate(
            platform=release.platform,
            release_source_id=release.source_id,
            track_source_id=target_track.source_id,
            release_title=release.title,
            track_title=target_track.title,
            artist_names=target_track.artist_names_str,
            release_type=release.release_type,
            release_date=release.release_date,
            duration_ms=target_track.duration_ms,
            track_number=target_track.track_number,
            release_url=release.source_url,
            track_url=target_track.source_url,
            selection_rule=rule_desc,
            relevance_score=scoring_res.score,
            relevance_reasons=scoring_res.reasons,
            raw_metadata=target_track.raw_metadata,
            initial_review_status=initial_status,
        )
        candidates_count += 1

    return 200, {
        "status": "success",
        "ingested_count": ingested_count,
        "candidates_count": candidates_count,
        "validation_errors": validation_errors,
    }


class KKBOXCommandManager:
    """Thread-safe command queue & sidecar health manager for KKBOX Session Adapter."""

    ALLOWED_ACTIONS = {"collect_kkbox"}

    def __init__(self, state_path: Optional[str] = None):
        self._lock = threading.Lock()
        self.state_path = state_path
        self._commands: Dict[str, Dict[str, Any]] = {}
        self._sidecar_status: Dict[str, Any] = {
            "online": False,
            "status": "idle",
            "last_seen_at": 0.0,
            "last_seen_iso": None,
            "sidecar_version": None,
            "current_job_id": None,
            "cumulative_ingested": 0,
            "error": None,
        }
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            commands = data.get("commands", {})
            sidecar = data.get("sidecar_status", {})
            if isinstance(commands, dict):
                self._commands = commands
            if isinstance(sidecar, dict):
                self._sidecar_status.update(sidecar)
        except Exception as exc:
            logger.warning("Could not load KKBOX sidecar state: %s", exc)

    def _save_state_locked(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                {"commands": self._commands, "sidecar_status": self._sidecar_status},
                f,
                indent=2,
                ensure_ascii=False,
            )
        os.replace(tmp_path, self.state_path)

    def enqueue_command(self, action: str = "collect_kkbox", command_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Enqueue an idempotent collection command.
        If an active pending, claimed, or running command already exists, returns that command.
        """
        if action not in self.ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported KKBOX sidecar action: {action}")
        with self._lock:
            now = time.time()
            for cid, cmd in self._commands.items():
                if cmd.get("action") == action and cmd.get("status") in ("pending", "claimed", "running"):
                    claimed_at = cmd.get("claimed_at") or 0.0
                    # If not expired (< 600s), reuse active command
                    if cmd.get("status") == "pending" or (now - claimed_at < 600.0):
                        return dict(cmd)

            cid = command_id or f"kkbox_cmd_{int(now)}_{uuid.uuid4().hex[:6]}"
            cmd_record = {
                "command_id": cid,
                "action": action,
                "status": "pending",
                "created_at": datetime.datetime.now().isoformat(),
                "created_timestamp": now,
                "claimed_at": None,
                "updated_at": now,
                "completed_at": None,
                "progress": {},
                "error": None,
            }
            self._commands[cid] = cmd_record
            self._save_state_locked()
            return dict(cmd_record)

    def claim_next_command(self, claim_timeout_seconds: float = 600.0) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the oldest pending command or recover an expired claimed/running command.
        """
        with self._lock:
            now = time.time()
            # 1. Recover expired claimed/running commands
            for cid, cmd in self._commands.items():
                if cmd.get("status") in ("claimed", "running"):
                    claimed_at = cmd.get("claimed_at") or 0.0
                    if (now - claimed_at) > claim_timeout_seconds:
                        cmd["status"] = "pending"
                        cmd["error"] = "Previous claim expired; re-queued for execution."
                        cmd["claimed_at"] = None

            # 2. Find oldest pending command
            pending_cmds = [c for c in self._commands.values() if c.get("status") == "pending"]
            if not pending_cmds:
                return None

            pending_cmds.sort(key=lambda x: x.get("created_timestamp", 0))
            claimed_cmd = pending_cmds[0]
            claimed_cmd["status"] = "claimed"
            claimed_cmd["claimed_at"] = now
            claimed_cmd["updated_at"] = now
            self._save_state_locked()
            return dict(claimed_cmd)

    def report_command(
        self,
        command_id: str,
        status: str,
        progress: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Atomically update status of a claimed/running command."""
        with self._lock:
            if command_id not in self._commands:
                return False, f"Command '{command_id}' not found"

            valid_statuses = ("claimed", "running", "completed", "failed", "paused")
            if status not in valid_statuses:
                return False, f"Invalid status '{status}'. Must be one of {valid_statuses}"

            cmd = self._commands[command_id]
            # A paused browser job must remain claimable after connectivity is
            # restored; otherwise one transient bridge outage strands it forever.
            stored_status = "pending" if status == "paused" else status
            cmd["status"] = stored_status
            cmd["last_reported_status"] = status
            cmd["updated_at"] = time.time()
            if stored_status in ("claimed", "running"):
                # Progress reports renew the claim lease during long crawls.
                cmd["claimed_at"] = cmd["updated_at"]
            if progress:
                cmd["progress"] = sanitize_payload(progress)
            if error is not None:
                cmd["error"] = error
            if stored_status in ("completed", "failed"):
                cmd["completed_at"] = datetime.datetime.now().isoformat()
            self._save_state_locked()
            return True, "Updated successfully"

    def record_heartbeat(self, sidecar_info: Dict[str, Any]) -> None:
        """Record heartbeat and telemetry from KKBOX Chrome Sidecar."""
        if not isinstance(sidecar_info, dict):
            raise ValueError("Heartbeat payload must be an object")
        status = sidecar_info.get("status", "idle")
        if status not in ALLOWED_SIDECAR_STATUSES:
            raise ValueError(f"Invalid sidecar status: {status}")
        cumulative = sidecar_info.get("cumulative_ingested", 0)
        if not isinstance(cumulative, int) or cumulative < 0:
            raise ValueError("cumulative_ingested must be a non-negative integer")
        clean_info = sanitize_payload(sidecar_info)
        with self._lock:
            now = time.time()
            self._sidecar_status["online"] = True
            self._sidecar_status["last_seen_at"] = now
            self._sidecar_status["last_seen_iso"] = datetime.datetime.now().isoformat()
            self._sidecar_status["status"] = clean_info.get("status", "idle")
            self._sidecar_status["sidecar_version"] = clean_info.get("sidecar_version")
            self._sidecar_status["current_job_id"] = clean_info.get("current_job_id")
            self._sidecar_status["cumulative_ingested"] = clean_info.get("cumulative_ingested", 0)
            if "error" in clean_info:
                self._sidecar_status["error"] = clean_info["error"]
            self._save_state_locked()

    def get_sidecar_status(self, offline_threshold_seconds: float = 45.0) -> Dict[str, Any]:
        """Get aggregated status of KKBOX sidecar adapter."""
        with self._lock:
            now = time.time()
            last_seen = self._sidecar_status.get("last_seen_at", 0.0)
            is_online = (now - last_seen) <= offline_threshold_seconds if last_seen > 0 else False
            status_copy = dict(self._sidecar_status)
            status_copy["online"] = is_online
            if not is_online and last_seen > 0:
                status_copy["status"] = "offline"
            return status_copy

    def get_command_status(self, command_id: str) -> Optional[Dict[str, Any]]:
        """Get copy of command record by ID."""
        with self._lock:
            cmd = self._commands.get(command_id)
            return dict(cmd) if cmd else None


# Shared singleton instance for handler
global_command_manager = KKBOXCommandManager(state_path="output/kkbox_sidecar_state.json")


class KKBOXIngestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for KKBOX sidecar ingestion, command claim, and heartbeat."""

    server_db: DiscoveryDB = None
    scorer: RelevanceScorer = RelevanceScorer()
    command_manager: KKBOXCommandManager = global_command_manager

    def _set_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
            self.send_header("Access-Control-Max-Age", "86400")

    def _send_json_response(self, status_code: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/health":
            self._send_json_response(200, {
                "status": "ok",
                "service": "kkbox-ingestion-bridge",
                "platform": "kkbox",
            })
        elif parsed_url.path == "/status/summary":
            sidecar_status = self.command_manager.get_sidecar_status()
            self._send_json_response(200, {
                "status": "ok",
                "sidecar": sidecar_status,
            })
        else:
            self._send_json_response(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0

        raw_json = {}
        if content_length > 0:
            if content_length > MAX_BODY_SIZE:
                self._send_json_response(413, {"error": f"Payload exceeds maximum allowed size of {MAX_BODY_SIZE} bytes"})
                return
            raw_body = self.rfile.read(content_length)
            try:
                raw_json = json.loads(raw_body.decode("utf-8"))
            except Exception as exc:
                self._send_json_response(400, {"error": f"Invalid JSON payload: {exc}"})
                return

        # 1. Ingestion Endpoint
        if parsed_url.path == "/ingest/kkbox":
            db = self.server_db or DiscoveryDB()
            status_code, resp_data = ingest_kkbox_payload(raw_json, db=db, scorer=self.scorer)
            self._send_json_response(status_code, resp_data)
            return

        # 2. Command Claim Endpoint (Atomically claims next pending command)
        elif parsed_url.path == "/commands/claim":
            claimed_cmd = self.command_manager.claim_next_command()
            self._send_json_response(200, {
                "status": "ok",
                "command": claimed_cmd,
            })
            return

        # 3. Command Report Endpoint (Reports progress / completion)
        elif parsed_url.path == "/commands/report":
            if not isinstance(raw_json, dict):
                self._send_json_response(400, {"error": "Report payload must be an object"})
                return
            cmd_id = raw_json.get("command_id")
            report_status = raw_json.get("status")
            if not cmd_id or not report_status:
                self._send_json_response(400, {"error": "Missing required fields 'command_id' or 'status'"})
                return
            if raw_json.get("progress") is not None and not isinstance(raw_json.get("progress"), dict):
                self._send_json_response(400, {"error": "'progress' must be an object or null"})
                return

            ok, msg = self.command_manager.report_command(
                command_id=cmd_id,
                status=report_status,
                progress=raw_json.get("progress"),
                error=raw_json.get("error"),
            )
            if ok:
                self._send_json_response(200, {"status": "ok", "message": msg})
            else:
                self._send_json_response(400, {"error": msg})
            return

        # 4. Heartbeat Endpoint
        elif parsed_url.path == "/heartbeat/kkbox":
            if not isinstance(raw_json, dict):
                self._send_json_response(400, {"error": "Heartbeat payload must be an object"})
                return
            try:
                self.command_manager.record_heartbeat(raw_json)
            except ValueError as exc:
                self._send_json_response(400, {"error": str(exc)})
                return
            self._send_json_response(200, {"status": "ok"})
            return

        else:
            self._send_json_response(404, {"error": "Endpoint not found"})

    def log_message(self, format: str, *args: Any) -> None:
        pass


class KKBOXBridgeServer:
    """Local HTTP Server wrapping KKBOX ingestion and Sidecar Command Manager."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        db: Optional[DiscoveryDB] = None,
        command_manager: Optional[KKBOXCommandManager] = None,
    ):
        self.host = host
        self.port = port
        self.db = db or DiscoveryDB()
        self.command_manager = command_manager or global_command_manager

        # Bind dependencies to a per-server handler subclass.  Mutating the
        # base handler class would make concurrent test or local server
        # instances overwrite one another's database and command manager.
        handler_cls = type(
            "BoundKKBOXIngestHandler",
            (KKBOXIngestHandler,),
            {"server_db": self.db, "command_manager": self.command_manager},
        )
        self.server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self.server.daemon_threads = True

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
