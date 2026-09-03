"""Unit tests for DiscoverySupervisor daemon, locking, isolation, command consumption, and restart scheduling."""

import datetime
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.bridge import KKBOXCommandManager
from song_discovery.supervisor import (
    DiscoverySupervisor,
    SupervisorLockError,
    is_pid_alive,
)


class TestDiscoverySupervisor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sup.db")
        self.lock_path = os.path.join(self.test_dir, "test.lock")
        self.state_path = os.path.join(self.test_dir, "test_state.json")
        self.refresh_cmd_path = os.path.join(self.test_dir, "test_refresh.json")
        self.log_dir = os.path.join(self.test_dir, "logs")

        self.mock_orchestrator = MagicMock()
        self.mock_orchestrator.run_discovery.return_value = {
            "run_id": "test_run",
            "total_releases": 1,
            "total_candidates": 1,
            "platforms": {
                "qq": {"status": "success", "releases_count": 1},
                "netease": {"status": "success", "releases_count": 1},
                "kkbox": {"status": "success", "releases_count": 1},
            },
        }
        self.command_manager = KKBOXCommandManager()

        self.supervisor = DiscoverySupervisor(
            db_path=self.db_path,
            bridge_port=9988,
            review_port=9989,
            interval_seconds=10.0,
            poll_interval_seconds=1.0,
            run_on_start=False,
            enable_notifications=False,
            state_path=self.state_path,
            lock_path=self.lock_path,
            refresh_command_path=self.refresh_cmd_path,
            log_dir=self.log_dir,
            orchestrator=self.mock_orchestrator,
            command_manager=self.command_manager,
        )

    def tearDown(self):
        self.supervisor.release_lock()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_lock_acquire_and_release(self):
        self.assertTrue(self.supervisor.acquire_lock())
        self.assertTrue(os.path.exists(self.lock_path))

        with open(self.lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["pid"], os.getpid())

        self.supervisor.release_lock()
        self.assertFalse(os.path.exists(self.lock_path))

    def test_duplicate_lock_rejection(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "created_at": "2026-08-28T12:00:00"}, f)

        other_supervisor = DiscoverySupervisor(
            db_path=self.db_path,
            lock_path=self.lock_path,
            state_path=self.state_path,
            log_dir=self.log_dir,
        )

        with patch("song_discovery.supervisor.os.getpid", return_value=os.getpid() + 1000):
            with self.assertRaises(SupervisorLockError):
                other_supervisor.acquire_lock()

    def test_stale_lock_recovery(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "created_at": "2026-08-28T12:00:00"}, f)

        with patch("song_discovery.supervisor.is_pid_alive", return_value=False):
            self.assertTrue(self.supervisor.acquire_lock())
            self.assertTrue(os.path.exists(self.lock_path))

    def test_state_persistence_and_loading(self):
        state = self.supervisor.load_state()
        self.assertEqual(state["last_run_status"], "init")

        state["last_run_status"] = "success"
        state["last_pending_count"] = 5
        self.supervisor.save_state(state)

        loaded = self.supervisor.load_state()
        self.assertEqual(loaded["last_run_status"], "success")
        self.assertEqual(loaded["last_pending_count"], 5)

    def test_platform_failure_isolation_qq_fails_netease_succeeds(self):
        def mock_run(platform, **kwargs):
            if platform == "qq":
                raise RuntimeError("QQ Music Gateway 502")
            return {"status": "success", "releases_count": 10}

        self.mock_orchestrator.run_discovery.side_effect = mock_run

        res = self.supervisor.run_discovery_iteration()
        self.assertEqual(res["status"], "success_with_warnings")
        self.assertEqual(res["platforms"]["qq"]["status"], "failed")
        self.assertEqual(res["platforms"]["netease"]["status"], "success")

        state = self.supervisor.load_state()
        self.assertEqual(state["last_run_status"], "success_with_warnings")

    def test_platform_failure_returned_without_exception_is_not_reported_as_success(self):
        def mock_run(platform, **kwargs):
            status = "failed" if platform == "qq" else "success"
            return {
                "platforms": {
                    platform: {
                        "status": status,
                        "releases_count": 0 if status == "failed" else 7,
                        "error": "QQ upstream rejected request" if status == "failed" else "",
                    }
                }
            }

        self.mock_orchestrator.run_discovery.side_effect = mock_run
        result = self.supervisor.run_discovery_iteration()

        self.assertEqual(result["status"], "success_with_warnings")
        self.assertEqual(result["errors"]["qq"], "QQ upstream rejected request")
        state = self.supervisor.load_state()
        self.assertEqual(state["platforms_summary"]["qq"]["status"], "failed")
        self.assertEqual(state["platforms_summary"]["netease"]["releases_count"], 7)

    def test_discovery_iteration_uses_official_kkbox_api_when_available(self):
        def mock_run(platform, **kwargs):
            return {
                "status": "success",
                "releases_count": 10,
                "platforms": {platform: {"status": "success", "releases_count": 10}},
            }

        self.mock_orchestrator.run_discovery.side_effect = mock_run
        res = self.supervisor.run_discovery_iteration()
        self.assertIn("kkbox", res["platforms"])
        self.assertNotEqual(res["platforms"]["kkbox"].get("status"), "enqueued")
        called_platforms = [call.kwargs.get("platform") for call in self.mock_orchestrator.run_discovery.call_args_list]
        self.assertEqual(called_platforms, ["kkbox", "qq", "netease"])
        self.assertIsNone(self.command_manager.claim_next_command())

    def test_discovery_iteration_falls_back_to_sidecar_without_kkbox_credentials(self):
        def mock_run(platform, **kwargs):
            if platform == "kkbox":
                return {"platforms": {"kkbox": {"status": "skipped"}}}
            return {"status": "success", "releases_count": 10}

        self.mock_orchestrator.run_discovery.side_effect = mock_run
        res = self.supervisor.run_discovery_iteration()
        self.assertEqual(res["platforms"]["kkbox"]["status"], "enqueued")

        claimed = self.command_manager.claim_next_command()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["action"], "collect_kkbox")

    def test_supervisor_consumes_user_refresh_command(self):
        # Schedule user refresh command
        with open(self.refresh_cmd_path, "w", encoding="utf-8") as f:
            json.dump({
                "command_id": "test_refresh_123",
                "action": "refresh_all_platforms",
                "status": "pending",
            }, f)

        self.supervisor.poll_and_consume_refresh_commands()

        # Check command status updated to completed
        with open(self.refresh_cmd_path, "r", encoding="utf-8") as f:
            cmd = json.load(f)
        self.assertEqual(cmd["status"], "completed")
        self.assertIn("result", cmd)

    def test_restart_scheduling_respects_recent_last_run(self):
        recent_iso = datetime.datetime.now().isoformat()
        self.supervisor.save_state({
            "last_run_time": recent_iso,
            "last_run_status": "success",
            "total_runs": 1,
        })

        with patch.object(self.supervisor, "start_bridge"):
            with patch.object(self.supervisor, "start_review_ui"):
                with patch.object(self.supervisor, "run_discovery_iteration") as mock_iter:
                    self.supervisor._running = True
                    self.supervisor._stop_event.set()
                    self.supervisor.run_forever(run_once=False)
                    mock_iter.assert_not_called()

    @patch("song_discovery.supervisor.send_macos_notification")
    def test_poll_pending_and_notify_covers_async_bridge_ingestions(self, mock_notify):
        mock_notify.return_value = True
        self.supervisor.enable_notifications = True
        self.supervisor.debounce_seconds = 10.0

        state = {
            "last_pending_count": 0,
            "pending_changed_at": time.time() - 20.0,
            "last_notified_pending_count": 0,
            "last_notified_time": 0.0,
        }
        self.supervisor.save_state(state)

        self.supervisor.db.upsert_candidate(
            platform="kkbox",
            release_source_id="KK_R1",
            track_source_id="KK_T1",
            release_title="夏夜大碟",
            track_title="夏夜",
            artist_names="刺猬",
            release_type="album",
            release_date="2026-08-20",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=85.0,
            relevance_reasons=["摇滚"],
        )

        self.supervisor.poll_pending_and_notify()
        mock_notify.assert_not_called()

        state = self.supervisor.load_state()
        state["pending_changed_at"] = time.time() - 15.0
        self.supervisor.save_state(state)

        self.supervisor.poll_pending_and_notify()
        mock_notify.assert_called_once()

    @patch("song_discovery.supervisor.KKBOXBridgeServer")
    @patch("song_discovery.supervisor.subprocess.Popen")
    def test_supervisor_smoke_run_once(self, mock_popen, mock_bridge_cls):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        mock_bridge = MagicMock()
        mock_bridge_cls.return_value = mock_bridge

        self.supervisor.run_forever(run_once=True)

        self.assertEqual(self.mock_orchestrator.run_discovery.call_count, 3)  # KKBOX + QQ + NetEase
        mock_proc.terminate.assert_called_once()
        mock_bridge.shutdown.assert_called_once()
        self.assertFalse(os.path.exists(self.lock_path))

    def test_supervisor_default_review_port_is_8502(self):
        sup = DiscoverySupervisor()
        self.assertEqual(sup.review_port, 8502)

    @patch("song_discovery.supervisor.subprocess.Popen")
    def test_supervisor_start_review_ui_launches_uvicorn_on_8502(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 7788
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        sup = DiscoverySupervisor(
            db_path=self.db_path,
            log_dir=self.log_dir,
            review_port=8502,
        )
        proc = sup.start_review_ui()
        self.assertIsNotNone(proc)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("uvicorn", cmd)
        self.assertIn("song_discovery.review_api:app", cmd)
        self.assertIn("8502", cmd)

    @patch("song_discovery.supervisor.send_macos_notification")
    def test_supervisor_notification_url_points_to_8502(self, mock_notify):
        mock_notify.return_value = True
        sup = DiscoverySupervisor(
            db_path=self.db_path,
            log_dir=self.log_dir,
            review_port=8502,
            enable_notifications=True,
            debounce_seconds=0.0,
            state_path=self.state_path,
        )
        state = {
            "last_pending_count": 0,
            "pending_changed_at": time.time() - 100.0,
            "last_notified_pending_count": 0,
            "last_notified_time": 0.0,
        }
        sup.save_state(state)
        sup.db.upsert_candidate(
            platform="netease",
            release_source_id="NE_R1",
            track_source_id="NE_T1",
            release_title="测试专辑",
            track_title="测试歌曲",
            artist_names="测试乐队",
            release_type="single",
            release_date="2026-08-25",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=90.0,
            relevance_reasons=["摇滚"],
        )
        sup.poll_pending_and_notify()
        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertIn("http://127.0.0.1:8502", kwargs.get("message", ""))

    def test_supervisor_natural_week_aligned_scheduling(self):
        sup = DiscoverySupervisor(
            db_path=self.db_path,
            log_dir=self.log_dir,
            align_natural_week=True,
            interval_seconds=5 * 86400.0,
        )
        # 1. Never run before -> is_due is True
        self.assertTrue(sup.is_discovery_due(0.0))

        shanghai = datetime.timezone(datetime.timedelta(hours=8))

        # 2. Run on Monday 10:00 China time.
        mon_dt = datetime.datetime(2026, 8, 24, 10, 0, 0, tzinfo=shanghai)
        mon_ts = mon_dt.timestamp()

        # Next scheduled discovery aligns to Tuesday 18:00 China time.
        next_ts = sup.get_next_scheduled_discovery_time(mon_ts)
        next_dt = datetime.datetime.fromtimestamp(next_ts, tz=shanghai)
        self.assertEqual(next_dt.weekday(), 1)  # Tuesday
        self.assertEqual(next_dt.hour, 18)
        self.assertEqual(next_dt.minute, 0)
        self.assertLessEqual(next_ts - mon_ts, 5 * 86400.0)

        # Before Tuesday 18:00 -> not due
        before_tue_ts = datetime.datetime(2026, 8, 25, 17, 0, 0, tzinfo=shanghai).timestamp()
        self.assertFalse(sup.is_discovery_due(mon_ts, now_ts=before_tue_ts))

        # At/after Tuesday 18:00 -> due
        at_tue_ts = datetime.datetime(2026, 8, 25, 18, 0, 0, tzinfo=shanghai).timestamp()
        self.assertTrue(sup.is_discovery_due(mon_ts, now_ts=at_tue_ts))

        # 3. Run on Tuesday 18:00 China time; next run is Friday 18:00.
        next_fri_ts = sup.get_next_scheduled_discovery_time(at_tue_ts)
        next_fri_dt = datetime.datetime.fromtimestamp(next_fri_ts, tz=shanghai)
        self.assertEqual(next_fri_dt.weekday(), 4)  # Friday
        self.assertEqual(next_fri_dt.hour, 18)
        self.assertEqual(next_fri_dt.minute, 0)
        self.assertLessEqual(next_fri_ts - at_tue_ts, 5 * 86400.0)

        # 4. Hard cap: interval since last run never exceeds 5 days
        past_cap_ts = mon_ts + 5.1 * 86400.0
        self.assertTrue(sup.is_discovery_due(mon_ts, now_ts=past_cap_ts))

    def test_daily_schedule_and_startup_debounce(self):
        sup = DiscoverySupervisor(
            db_path=self.db_path,
            log_dir=self.log_dir,
            interval_seconds=86400.0,
            align_natural_week=False,
            run_on_start=True,
            startup_debounce_seconds=1800.0,
        )
        now = datetime.datetime(2026, 9, 3, 9, 0, 0).timestamp()
        self.assertEqual(sup.get_next_scheduled_discovery_time(now), now + 86400.0)
        self.assertTrue(sup.is_startup_discovery_due({}, now_ts=now))
        recent = {"last_startup_attempt_time": datetime.datetime.fromtimestamp(now - 120).isoformat()}
        self.assertFalse(sup.is_startup_discovery_due(recent, now_ts=now))
        old = {"last_startup_attempt_time": datetime.datetime.fromtimestamp(now - 1801).isoformat()}
        self.assertTrue(sup.is_startup_discovery_due(old, now_ts=now))

    def test_fixed_daily_hour_schedule(self):
        shanghai = datetime.timezone(datetime.timedelta(hours=8))
        sup = DiscoverySupervisor(
            db_path=self.db_path,
            log_dir=self.log_dir,
            interval_seconds=86400.0,
            align_natural_week=False,
            daily_run_hour=1,
            run_on_start=True,
        )

        after_one = datetime.datetime(2026, 9, 3, 12, 45, tzinfo=shanghai).timestamp()
        next_ts = sup.get_next_scheduled_discovery_time(after_one)
        self.assertEqual(
            datetime.datetime.fromtimestamp(next_ts, tz=shanghai),
            datetime.datetime(2026, 9, 4, 1, 0, tzinfo=shanghai),
        )
        self.assertFalse(sup.is_discovery_due(after_one, now_ts=next_ts - 1))
        self.assertTrue(sup.is_discovery_due(after_one, now_ts=next_ts))

        before_one = datetime.datetime(2026, 9, 3, 0, 30, tzinfo=shanghai).timestamp()
        same_day_ts = sup.get_next_scheduled_discovery_time(before_one)
        self.assertEqual(
            datetime.datetime.fromtimestamp(same_day_ts, tz=shanghai),
            datetime.datetime(2026, 9, 3, 1, 0, tzinfo=shanghai),
        )

    def test_run_forever_runs_once_immediately_on_start(self):
        self.supervisor.run_on_start = True
        self.supervisor.startup_debounce_seconds = 1800.0
        self.supervisor.save_state({
            "last_run_time": datetime.datetime.now().isoformat(),
            "last_run_status": "success",
        })
        self.supervisor._stop_event.set()

        with patch.object(self.supervisor, "start_bridge"), \
             patch.object(self.supervisor, "start_review_ui"), \
             patch.object(self.supervisor, "run_discovery_iteration") as run_iteration:
            self.supervisor.run_forever()

        run_iteration.assert_called_once_with()
        state = self.supervisor.load_state()
        self.assertTrue(state.get("last_startup_attempt_time"))


if __name__ == "__main__":
    unittest.main()
