"""Always-on Local Supervisor / Daemon for 3-Platform Song Discovery, KKBOX Bridge, Review UI, and Command Execution."""

import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from song_discovery.bridge import KKBOXBridgeServer, KKBOXCommandManager, global_command_manager
from song_discovery.db import DiscoveryDB
from song_discovery.orchestrator import DiscoveryOrchestrator
from song_discovery.review_helpers import (
    get_platforms_overview,
    get_publication_readiness,
    send_macos_notification,
    should_send_notification,
    update_pending_tracking,
)

logger = logging.getLogger("song_discovery.supervisor")


class SupervisorLockError(Exception):
    """Raised when supervisor lock cannot be acquired because another instance is active."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DiscoverySupervisor:
    """
    Supervisor managing:
    1. KKBOX Ingestion Bridge Server & Sidecar Command Queue (default 127.0.0.1:8765)
    2. FastAPI + React Review UI with auto-restart on unexpected termination (default 127.0.0.1:8502)
    3. Unified 3-Platform Discovery (QQ, NetEase, KKBOX Sidecar command dispatching)
    4. Immediate Refresh Command Consumption (from Web UI or CLI)
    5. macOS Notification Debouncing for discovery + async KKBOX bridge ingestions
    6. Atomic Single-instance Locking (O_CREAT | O_EXCL) with Stale PID Recovery
    """

    def __init__(
        self,
        db_path: str = "output/discovery.db",
        bridge_host: str = "127.0.0.1",
        bridge_port: int = 8765,
        review_host: str = "127.0.0.1",
        review_port: int = 8502,
        interval_seconds: float = 24 * 3600.0,
        poll_interval_seconds: float = 15.0,
        align_natural_week: bool = False,
        schedule_timezone: str = "Asia/Shanghai",
        daily_run_hour: Optional[int] = None,
        run_on_start: bool = True,
        startup_debounce_seconds: float = 30 * 60.0,
        cookie_file: str = "cookie.txt",
        netease_url: Optional[str] = None,
        auto_start_netease: bool = True,
        enable_notifications: bool = True,
        debounce_seconds: float = 60.0,
        state_path: str = "output/supervisor_state.json",
        lock_path: str = "output/supervisor.lock",
        refresh_command_path: str = "output/refresh_command.json",
        log_dir: str = "output/logs",
        orchestrator: Optional[DiscoveryOrchestrator] = None,
        command_manager: Optional[KKBOXCommandManager] = None,
    ):
        self.db_path = db_path
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self.review_host = review_host
        self.review_port = review_port
        self.interval_seconds = interval_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.align_natural_week = align_natural_week
        self.schedule_timezone = ZoneInfo(schedule_timezone)
        if daily_run_hour is not None and not 0 <= daily_run_hour <= 23:
            raise ValueError("daily_run_hour must be between 0 and 23")
        self.daily_run_hour = daily_run_hour
        self.run_on_start = run_on_start
        self.startup_debounce_seconds = max(0.0, startup_debounce_seconds)
        self.max_interval_seconds = min(self.interval_seconds, 5 * 24 * 3600.0)
        self.cookie_file = cookie_file
        self.netease_url = netease_url
        self.auto_start_netease = auto_start_netease
        self.enable_notifications = enable_notifications
        self.debounce_seconds = debounce_seconds
        self.state_path = state_path
        self.lock_path = lock_path
        self.refresh_command_path = refresh_command_path
        self.log_dir = log_dir

        self.db = DiscoveryDB(db_path=self.db_path)
        self.orchestrator = orchestrator or DiscoveryOrchestrator(db=self.db)
        self.command_manager = command_manager or global_command_manager

        self.bridge_server: Optional[KKBOXBridgeServer] = None
        self.bridge_thread: Optional[threading.Thread] = None
        self.review_process: Optional[subprocess.Popen] = None
        self._review_out_f = None
        self._review_err_f = None

        self._running = False
        self._stop_event = threading.Event()
        self._last_ui_restart_time = 0.0
        self._ui_restart_count = 0

    # -------------------------------------------------------------------------
    # Atomic Single-instance Lock (O_CREAT | O_EXCL) with Stale Recovery
    # -------------------------------------------------------------------------
    def acquire_lock(self) -> bool:
        """Acquire atomic single-instance lock file using O_CREAT | O_EXCL."""
        os.makedirs(os.path.dirname(os.path.abspath(self.lock_path)), exist_ok=True)

        for attempt in range(3):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    lock_info = {
                        "pid": os.getpid(),
                        "created_at": datetime.datetime.now().isoformat(),
                        "db_path": self.db_path,
                    }
                    json.dump(lock_info, f, indent=2)
                return True
            except FileExistsError:
                try:
                    with open(self.lock_path, "r", encoding="utf-8") as f:
                        lock_data = json.load(f)
                    locked_pid = lock_data.get("pid")
                    if locked_pid and locked_pid != os.getpid() and is_pid_alive(locked_pid):
                        raise SupervisorLockError(
                            f"Another supervisor daemon is already running (PID: {locked_pid}). "
                            f"Lock file: {self.lock_path}"
                        )
                    else:
                        logger.warning("Cleaning up stale supervisor lock from dead PID %s", locked_pid)
                        try:
                            os.remove(self.lock_path)
                        except OSError:
                            pass
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("Overwriting unreadable/corrupt lock file: %s", exc)
                    try:
                        os.remove(self.lock_path)
                    except OSError:
                        pass

        raise SupervisorLockError(f"Could not acquire supervisor lock at {self.lock_path}")

    def release_lock(self) -> None:
        """Release lock file if owned by current process."""
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                if lock_data.get("pid") == os.getpid():
                    os.remove(self.lock_path)
            except Exception:
                try:
                    os.remove(self.lock_path)
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # State Persistence
    # -------------------------------------------------------------------------
    def load_state(self) -> Dict[str, Any]:
        """Load persistent supervisor state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Could not read supervisor state: %s", exc)
        return {
            "last_run_time": None,
            "last_run_status": "init",
            "platforms_summary": {},
            "kkbox_sidecar": {},
            "last_pending_count": 0,
            "pending_changed_at": 0.0,
            "last_notified_pending_count": 0,
            "last_notified_time": 0.0,
            "total_runs": 0,
        }

    def save_state(self, state: Dict[str, Any]) -> None:
        """Save persistent supervisor state atomically."""
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        # Attach current KKBOX sidecar telemetry
        state["kkbox_sidecar"] = self.command_manager.get_sidecar_status()
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.state_path)

    # -------------------------------------------------------------------------
    # Subservice Lifecycle: Bridge & Review UI
    # -------------------------------------------------------------------------
    def start_bridge(self) -> None:
        """Start the KKBOX ingestion HTTP bridge in a background daemon thread."""
        logger.info("Starting KKBOX bridge on %s:%d...", self.bridge_host, self.bridge_port)
        self.bridge_server = KKBOXBridgeServer(
            host=self.bridge_host,
            port=self.bridge_port,
            db=self.db,
            command_manager=self.command_manager,
        )
        self.bridge_thread = threading.Thread(
            target=self.bridge_server.serve_forever,
            name="KKBOXBridgeThread",
            daemon=True,
        )
        self.bridge_thread.start()
        logger.info("KKBOX bridge listening on http://%s:%d/", self.bridge_host, self.bridge_port)

    def stop_bridge(self) -> None:
        """Stop the KKBOX bridge server."""
        if self.bridge_server:
            logger.info("Shutting down KKBOX bridge...")
            try:
                self.bridge_server.shutdown()
            except Exception as e:
                logger.warning("Error shutting down bridge server: %s", e)
            self.bridge_server = None

    def start_review_ui(self) -> Optional[subprocess.Popen]:
        """Start FastAPI + React review UI headlessly if not already running."""
        if self.review_process and self.review_process.poll() is None:
            return self.review_process

        os.makedirs(self.log_dir, exist_ok=True)
        stdout_log = os.path.join(self.log_dir, "review_api_stdout.log")
        stderr_log = os.path.join(self.log_dir, "review_api_stderr.log")

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "song_discovery.review_api:app",
            "--host",
            self.review_host,
            "--port",
            str(self.review_port),
        ]

        logger.info("Starting Review UI (FastAPI + React) on http://%s:%d...", self.review_host, self.review_port)
        try:
            self._review_out_f = open(stdout_log, "a", encoding="utf-8")
            self._review_err_f = open(stderr_log, "a", encoding="utf-8")
            self.review_process = subprocess.Popen(
                cmd,
                stdout=self._review_out_f,
                stderr=self._review_err_f,
                cwd=project_root,
            )
            return self.review_process
        except Exception as exc:
            logger.warning("Could not launch Review UI: %s", exc)
            return None

    def stop_review_ui(self) -> None:
        """Terminate Review UI child process."""
        if self.review_process:
            logger.info("Stopping Review UI process (PID %s)...", self.review_process.pid)
            try:
                self.review_process.terminate()
                self.review_process.wait(timeout=5)
            except Exception:
                try:
                    self.review_process.kill()
                except Exception:
                    pass
            self.review_process = None

        if self._review_out_f:
            try:
                self._review_out_f.close()
            except Exception:
                pass
            self._review_out_f = None

        if self._review_err_f:
            try:
                self._review_err_f.close()
            except Exception:
                pass
            self._review_err_f = None

    def check_and_recover_review_ui(self) -> None:
        """Monitor review UI subprocess and restart with backoff if terminated."""
        if not self._running:
            return

        if self.review_process and self.review_process.poll() is not None:
            exit_code = self.review_process.poll()
            now = time.time()
            if (now - self._last_ui_restart_time) < 10.0 and self._ui_restart_count >= 5:
                logger.error("[SUPERVISOR] Review UI is flapping (exit code %s). Backing off.", exit_code)
                return

            logger.warning("[SUPERVISOR] Review UI process exited unexpectedly (code %s). Restarting...", exit_code)
            self._last_ui_restart_time = now
            self._ui_restart_count += 1
            self.start_review_ui()

    # -------------------------------------------------------------------------
    # Discovery Iteration & Command Consumption
    # -------------------------------------------------------------------------
    def run_discovery_iteration(self) -> Dict[str, Any]:
        """
        Run scheduled 3-platform discovery:
        1. Uses the KKBOX official API when configured; otherwise enqueues the Sidecar fallback.
        2. Executes QQ in isolation.
        3. Executes NetEase in isolation.
        4. Aggregates status and dispatches debounced macOS notification.
        """
        state = self.load_state()
        now_dt = datetime.datetime.now()
        logger.info("[SUPERVISOR] Starting unified 3-platform discovery run (QQ, NetEase, KKBOX)...")

        run_result = {"status": "success", "platforms": {}, "errors": {}}
        qq_ok = False
        netease_ok = False
        kkbox_ok = False
        kkbox_mode = "official_open_api"

        # 1. Prefer the official KKBOX API. Only fall back to the browser
        # sidecar when the API credentials are not configured.
        kkbox_summary = {}
        try:
            kkbox_summary = self.orchestrator.run_discovery(platform="kkbox", limit=50)
            kkbox_info = kkbox_summary.get("platforms", {}).get("kkbox", {})
            if kkbox_info.get("status") == "skipped":
                kkbox_mode = "sidecar_fallback"
                kkbox_cmd = self.command_manager.enqueue_command(action="collect_kkbox")
                run_result["platforms"]["kkbox"] = {
                    "status": "enqueued",
                    "source_mode": kkbox_mode,
                    "command_id": kkbox_cmd.get("command_id"),
                    "message": "KKBOX API credentials unavailable; enqueued Sidecar fallback",
                }
            elif kkbox_info.get("status") in {"success", "success_with_warnings"}:
                kkbox_ok = True
                run_result["platforms"]["kkbox"] = kkbox_summary
            else:
                error = kkbox_info.get("error") or "KKBOX official API collection failed"
                run_result["platforms"]["kkbox"] = kkbox_summary
                run_result["errors"]["kkbox"] = error
        except Exception as exc:
            logger.error("[SUPERVISOR ERROR] KKBOX discovery run encountered error: %s", exc)
            run_result["platforms"]["kkbox"] = {"status": "failed", "error": str(exc)}
            run_result["errors"]["kkbox"] = str(exc)

        # 2. Run QQ in isolation
        qq_summary = {}
        qq_info = {}
        try:
            qq_summary = self.orchestrator.run_discovery(platform="qq", limit=10)
            run_result["platforms"]["qq"] = qq_summary
            qq_info = qq_summary.get("platforms", {}).get("qq", {}) or qq_summary
            qq_ok = qq_info.get("status") in {"success", "success_with_warnings"}
            if not qq_ok:
                error = qq_info.get("error") or "QQ discovery failed"
                run_result["errors"]["qq"] = error
        except Exception as exc:
            logger.error("[SUPERVISOR ERROR] QQ discovery run encountered error: %s", exc)
            run_result["platforms"]["qq"] = {"status": "failed", "error": str(exc)}
            run_result["errors"]["qq"] = str(exc)

        # 3. Run NetEase in isolation (15 pages = 300 albums)
        netease_summary = {}
        netease_info = {}
        try:
            netease_summary = self.orchestrator.run_discovery(
                platform="netease",
                netease_url=self.netease_url,
                netease_pages=15,
                netease_page_size=20,
            )
            run_result["platforms"]["netease"] = netease_summary
            netease_info = netease_summary.get("platforms", {}).get("netease", {}) or netease_summary
            netease_ok = netease_info.get("status") in {"success", "success_with_warnings"}
            if not netease_ok:
                error = netease_info.get("error") or "NetEase discovery failed"
                run_result["errors"]["netease"] = error
        except Exception as exc:
            logger.error("[SUPERVISOR ERROR] NetEase discovery run encountered error: %s", exc)
            run_result["platforms"]["netease"] = {"status": "failed", "error": str(exc)}
            run_result["errors"]["netease"] = str(exc)

        # Determine overall iteration status
        successful_platforms = sum((qq_ok, netease_ok, kkbox_ok))
        kkbox_fallback_enqueued = run_result["platforms"].get("kkbox", {}).get("status") == "enqueued"
        if successful_platforms == 3:
            run_result["status"] = "success"
        elif successful_platforms > 0 or kkbox_fallback_enqueued:
            run_result["status"] = "success_with_warnings"
        else:
            run_result["status"] = "failed"

        state["last_run_status"] = run_result["status"]
        state["last_run_time"] = now_dt.isoformat()
        state["total_runs"] = state.get("total_runs", 0) + 1
        state["platforms_summary"] = {
            "qq": {
                "status": "success" if qq_ok else "failed",
                "last_run_time": now_dt.isoformat(),
                "releases_count": qq_info.get("releases_count", 0) if qq_ok else 0,
                "error": run_result["errors"].get("qq"),
            },
            "netease": {
                "status": "success" if netease_ok else "failed",
                "last_run_time": now_dt.isoformat(),
                "releases_count": netease_info.get("releases_count", 0) if netease_ok else 0,
                "error": run_result["errors"].get("netease"),
            },
            "kkbox": {
                "status": "success" if kkbox_ok else ("enqueued" if kkbox_fallback_enqueued else "failed"),
                "source_mode": kkbox_mode,
                "last_run_time": now_dt.isoformat(),
                "releases_count": (
                    kkbox_summary.get("platforms", {}).get("kkbox", {}).get("releases_count", 0)
                    if kkbox_ok else 0
                ),
                "error": run_result["errors"].get("kkbox"),
            },
        }

        # Check pending candidates in database and update change tracking
        stats = self.db.get_stats()
        current_pending = stats.get("pending", 0)
        update_pending_tracking(state, current_pending)

        # Notification check
        if self.enable_notifications and should_send_notification(state, current_pending, self.debounce_seconds):
            review_url = f"http://{self.review_host}:{self.review_port}"
            notified = send_macos_notification(
                title="NetEase Weekly Clipper",
                subtitle="新歌待人审",
                message=f"发现 {current_pending} 首新待审核歌曲，请前往 {review_url} 审核",
            )
            if notified:
                state["last_notified_pending_count"] = current_pending
                state["last_notified_time"] = time.time()
                logger.info("[SUPERVISOR] Sent macOS notification for %d pending candidates", current_pending)

        self.save_state(state)
        return run_result

    def poll_and_consume_refresh_commands(self) -> None:
        """
        Check for pending user-requested refresh commands (e.g. from Web UI '立即更新三平台' button).
        Executes discovery on all 3 platforms and updates command status.
        """
        if not os.path.exists(self.refresh_command_path):
            return

        cmd = None
        try:
            with open(self.refresh_command_path, "r", encoding="utf-8") as f:
                cmd = json.load(f)
            if cmd.get("status") == "pending":
                logger.info("[SUPERVISOR] Consuming user refresh command '%s'...", cmd.get("command_id"))
                cmd["status"] = "running"
                cmd["started_at"] = datetime.datetime.now().isoformat()
                self._write_refresh_command(cmd)

                # Run full discovery
                res = self.run_discovery_iteration()

                cmd["status"] = "completed"
                cmd["completed_at"] = datetime.datetime.now().isoformat()
                cmd["result"] = res
                self._write_refresh_command(cmd)
                logger.info("[SUPERVISOR] User refresh command completed successfully.")
        except Exception as exc:
            logger.warning("[SUPERVISOR] Error consuming refresh command: %s", exc)
            if isinstance(cmd, dict) and cmd.get("status") == "running":
                cmd["status"] = "failed"
                cmd["completed_at"] = datetime.datetime.now().isoformat()
                cmd["error"] = str(exc)
                try:
                    self._write_refresh_command(cmd)
                except Exception:
                    logger.exception("Could not persist failed refresh command state")

    def _write_refresh_command(self, command: Dict[str, Any]) -> None:
        """Atomically replace the UI-to-supervisor command record."""
        os.makedirs(os.path.dirname(os.path.abspath(self.refresh_command_path)), exist_ok=True)
        tmp_path = f"{self.refresh_command_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(command, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.refresh_command_path)

    def poll_pending_and_notify(self) -> None:
        """
        Poll pending candidate count from SQLite to capture both scheduled discovery
        and asynchronous KKBOX bridge ingestions. Dispatches debounced notification.
        """
        state = self.load_state()
        stats = self.db.get_stats()
        current_pending = stats.get("pending", 0)
        update_pending_tracking(state, current_pending)

        if self.enable_notifications and should_send_notification(state, current_pending, self.debounce_seconds):
            review_url = f"http://{self.review_host}:{self.review_port}"
            notified = send_macos_notification(
                title="NetEase Weekly Clipper",
                subtitle="新歌待人审",
                message=f"发现 {current_pending} 首新待审核歌曲，请前往 {review_url} 审核",
            )
            if notified:
                state["last_notified_pending_count"] = current_pending
                state["last_notified_time"] = time.time()
                logger.info("[SUPERVISOR POLL] Sent macOS notification for %d pending candidates", current_pending)
                self.save_state(state)
        else:
            self.save_state(state)

    # -------------------------------------------------------------------------
    # Main Daemon Loop
    # -------------------------------------------------------------------------
    def _handle_signal(self, signum, frame):
        logger.info("[SUPERVISOR] Received termination signal (%s). Initiating graceful shutdown...", signum)
        self._running = False
        self._stop_event.set()

    def get_next_scheduled_discovery_time(
        self,
        last_run_ts: float,
        now_ts: Optional[float] = None,
    ) -> float:
        """
        Compute the next scheduled discovery unix timestamp.
        If align_natural_week is True:
          Aligns to Tuesday/Friday 18:00 in the configured local timezone,
          enforcing a minimum 12-hour gap after last run, and strictly capped at max_interval_seconds (<= 5 days).
        If daily_run_hour is configured:
          Run at that hour every day in the configured local timezone.
        Otherwise:
          Direct interval scheduling: last_run_ts + interval_seconds.
        """
        if last_run_ts <= 0.0:
            return now_ts if now_ts is not None else time.time()

        max_due = last_run_ts + self.max_interval_seconds
        if not self.align_natural_week:
            if self.daily_run_hour is not None:
                last_dt = datetime.datetime.fromtimestamp(last_run_ts, tz=self.schedule_timezone)
                next_dt = last_dt.replace(
                    hour=self.daily_run_hour,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if next_dt <= last_dt:
                    next_dt += datetime.timedelta(days=1)
                return next_dt.timestamp()
            return min(last_run_ts + self.interval_seconds, max_due)

        last_dt = datetime.datetime.fromtimestamp(last_run_ts, tz=self.schedule_timezone)
        min_next_dt = last_dt + datetime.timedelta(hours=12)

        checkpoints = [(1, 18, 0), (4, 18, 0)]  # Tuesday and Friday, local time
        candidates = []

        for days_ahead in range(0, 14):
            candidate_day = (min_next_dt + datetime.timedelta(days=days_ahead)).date()
            for weekday, hour, minute in checkpoints:
                if candidate_day.weekday() == weekday:
                    cp_dt = datetime.datetime(
                        candidate_day.year, candidate_day.month, candidate_day.day,
                        hour, minute, 0, tzinfo=self.schedule_timezone
                    )
                    if cp_dt >= min_next_dt:
                        candidates.append(cp_dt.timestamp())

        if candidates:
            earliest_checkpoint = min(candidates)
            return min(earliest_checkpoint, max_due)
        return max_due

    def is_discovery_due(
        self,
        last_run_ts: float,
        now_ts: Optional[float] = None,
    ) -> bool:
        """Check if scheduled discovery should run based on last run timestamp and current time."""
        if last_run_ts <= 0.0:
            return True
        now = now_ts if now_ts is not None else time.time()
        if (now - last_run_ts) >= self.max_interval_seconds:
            return True
        next_due = self.get_next_scheduled_discovery_time(last_run_ts, now)
        return now >= next_due

    def is_startup_discovery_due(
        self,
        state: Dict[str, Any],
        now_ts: Optional[float] = None,
    ) -> bool:
        """Run on process start, but suppress rapid launchd crash/restart loops."""
        if not self.run_on_start:
            return False
        now = now_ts if now_ts is not None else time.time()
        last_attempt_iso = str(state.get("last_startup_attempt_time") or "")
        if not last_attempt_iso:
            return True
        try:
            last_attempt_ts = datetime.datetime.fromisoformat(last_attempt_iso).timestamp()
        except (TypeError, ValueError):
            return True
        return (now - last_attempt_ts) >= self.startup_debounce_seconds

    # -------------------------------------------------------------------------
    # Main Daemon Lifecycle
    # -------------------------------------------------------------------------

    def run_forever(self, run_once: bool = False) -> None:
        """
        Main entry point for discovery daemon.
        If run_once is True, executes a single discovery iteration and cleans up.
        """
        logger.info("[SUPERVISOR] Acquiring single-instance lock (%s)...", self.lock_path)
        self.acquire_lock()

        self._running = True
        # Preserve a stop request that raced with startup. A supervisor object is
        # single-use; callers that need a restart construct a fresh instance.

        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except (ValueError, AttributeError):
            # Non-main thread execution in unit tests
            pass

        try:
            # 1. Start persistent services
            self.start_bridge()
            self.start_review_ui()

            # 2. Check persisted last_run_time for restart scheduling
            state = self.load_state()
            last_run_iso = state.get("last_run_time")
            last_run_status = state.get("last_run_status", "init")
            startup_due = self.is_startup_discovery_due(state)

            is_due = True
            last_run_ts = 0.0
            if last_run_iso and last_run_status in ("success", "success_with_warnings"):
                try:
                    last_run_ts = datetime.datetime.fromisoformat(last_run_iso).timestamp()
                    is_due = self.is_discovery_due(last_run_ts, time.time())
                    if not is_due:
                        next_ts = self.get_next_scheduled_discovery_time(last_run_ts, time.time())
                        due_in_hours = max(0.0, next_ts - time.time()) / 3600.0
                        logger.info(
                            "[SUPERVISOR] Previous run was recent (%s). Next discovery due in %.1f hours.",
                            last_run_iso,
                            due_in_hours,
                        )
                except Exception:
                    is_due = True

            if startup_due:
                state["last_startup_attempt_time"] = datetime.datetime.now().isoformat()
                self.save_state(state)

            if is_due or startup_due or run_once:
                self.run_discovery_iteration()
                last_run_ts = time.time()

            if run_once:
                logger.info("[SUPERVISOR] Completed single pass (--once mode).")
                return

            # 3. Continuous supervisor loop with poll interval (default 15s)
            next_ts = self.get_next_scheduled_discovery_time(last_run_ts, time.time())
            due_in_hours = max(0.0, next_ts - time.time()) / 3600.0
            logger.info(
                "[SUPERVISOR] Daemon is running. Next discovery in %.1f hours (daily hour: %s, natural week aligned: %s). Press Ctrl+C to stop.",
                due_in_hours,
                f"{self.daily_run_hour:02d}:00 {self.schedule_timezone.key}" if self.daily_run_hour is not None else "interval",
                self.align_natural_week,
            )

            last_poll_ts = time.time()

            while self._running and not self._stop_event.is_set():
                time.sleep(1.0)
                if not self._running or self._stop_event.is_set():
                    break

                # Poll and consume user refresh commands from UI/CLI
                self.poll_and_consume_refresh_commands()

                now_ts = time.time()

                # Periodic subservice health check & pending-count notification poll
                if (now_ts - last_poll_ts) >= self.poll_interval_seconds:
                    self.check_and_recover_review_ui()
                    self.poll_pending_and_notify()
                    last_poll_ts = now_ts

                # Periodic scheduled discovery iteration
                if self.is_discovery_due(last_run_ts, now_ts):
                    self.run_discovery_iteration()
                    last_run_ts = time.time()

        finally:
            logger.info("[SUPERVISOR] Cleaning up supervisor resources...")
            self.stop_review_ui()
            self.stop_bridge()
            self.release_lock()
            logger.info("[SUPERVISOR] Daemon stopped cleanly.")
