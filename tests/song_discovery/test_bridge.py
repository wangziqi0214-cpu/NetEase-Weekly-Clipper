"""Unit tests for KKBOX HTTP Bridge, Command Manager, and Ingestion."""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.bridge import (
    KKBOXBridgeServer,
    KKBOXCommandManager,
    ingest_kkbox_payload,
    sanitize_payload,
    validate_release_schema,
)
from song_discovery.db import DiscoveryDB


class TestKKBOXBridge(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bridge.db")
        self.db = DiscoveryDB(db_path=self.db_path)
        self.command_manager = KKBOXCommandManager()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sanitize_payload_redacts_credentials(self):
        raw = {
            "token": "secret_token_123",
            "authorization": "Bearer xxx",
            "cookie": "uid=123",
            "releases": [
                {
                    "title": "Album 1",
                    "cookies": "bad",
                    "tracks": [{"title": "Track 1", "auth": "no"}],
                }
            ],
        }
        clean = sanitize_payload(raw)
        self.assertNotIn("token", clean)
        self.assertNotIn("authorization", clean)
        self.assertNotIn("cookie", clean)
        self.assertEqual(clean["releases"][0]["title"], "Album 1")
        self.assertNotIn("cookies", clean["releases"][0])
        self.assertNotIn("auth", clean["releases"][0]["tracks"][0])

    def test_validate_release_schema(self):
        valid_item = {
            "source_id": "alb_1",
            "title": "测试专辑",
            "artists": [{"name": "测试乐队"}],
            "tracks": [{"title": "测试单曲", "source_id": "trk_1"}],
        }
        ok, err = validate_release_schema(valid_item)
        self.assertTrue(ok)
        self.assertEqual(err, "")

        invalid_item = {"source_id": "alb_2"}
        ok, err = validate_release_schema(invalid_item)
        self.assertFalse(ok)
        self.assertIn("Missing required release field", err)

    def test_command_manager_idempotency_and_enqueue(self):
        cmd1 = self.command_manager.enqueue_command(action="collect_kkbox")
        self.assertEqual(cmd1["status"], "pending")
        self.assertEqual(cmd1["action"], "collect_kkbox")

        # Second enqueue returns identical active pending command
        cmd2 = self.command_manager.enqueue_command(action="collect_kkbox")
        self.assertEqual(cmd1["command_id"], cmd2["command_id"])

    def test_atomic_claim_and_queue_order(self):
        cmd1 = self.command_manager.enqueue_command(command_id="cmd_first")
        time.sleep(0.01)
        # Create second pending command directly
        self.command_manager._commands["cmd_second"] = {
            "command_id": "cmd_second",
            "action": "collect_kkbox",
            "status": "pending",
            "created_timestamp": time.time() + 1.0,
        }

        # First claim picks oldest
        claimed = self.command_manager.claim_next_command()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["command_id"], "cmd_first")
        self.assertEqual(claimed["status"], "claimed")

        # Second claim picks second
        claimed2 = self.command_manager.claim_next_command()
        self.assertIsNotNone(claimed2)
        self.assertEqual(claimed2["command_id"], "cmd_second")

        # Third claim returns None
        self.assertIsNone(self.command_manager.claim_next_command())

    def test_expired_claim_recovery(self):
        cmd = self.command_manager.enqueue_command(command_id="cmd_timeout")
        # Simulate command claimed 1000s ago
        self.command_manager._commands["cmd_timeout"]["status"] = "claimed"
        self.command_manager._commands["cmd_timeout"]["claimed_at"] = time.time() - 1000.0

        # claim_next_command with timeout 600s should recover and claim it
        recovered = self.command_manager.claim_next_command(claim_timeout_seconds=600.0)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["command_id"], "cmd_timeout")
        self.assertEqual(recovered["status"], "claimed")

    def test_command_reporting_and_status_validation(self):
        cmd = self.command_manager.enqueue_command(command_id="cmd_rep")
        self.command_manager.claim_next_command()

        # Valid report
        ok, msg = self.command_manager.report_command(
            command_id="cmd_rep",
            status="running",
            progress={"ingested": 10},
        )
        self.assertTrue(ok)
        status = self.command_manager.get_command_status("cmd_rep")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["progress"]["ingested"], 10)

        # Invalid status rejected
        ok, msg = self.command_manager.report_command(command_id="cmd_rep", status="invalid_status")
        self.assertFalse(ok)
        self.assertIn("Invalid status", msg)

    def test_heartbeat_and_sidecar_status(self):
        self.command_manager.record_heartbeat({
            "status": "crawling",
            "sidecar_version": "1.3.0",
            "cumulative_ingested": 25,
        })

        sidecar = self.command_manager.get_sidecar_status(offline_threshold_seconds=10.0)
        self.assertTrue(sidecar["online"])
        self.assertEqual(sidecar["status"], "crawling")
        self.assertEqual(sidecar["cumulative_ingested"], 25)

    def test_command_and_heartbeat_state_survive_manager_restart(self):
        state_path = os.path.join(self.test_dir, "kkbox_sidecar_state.json")
        manager = KKBOXCommandManager(state_path=state_path)
        command = manager.enqueue_command(command_id="persistent_cmd")
        manager.record_heartbeat({"status": "idle", "sidecar_version": "1.3.0"})

        restored = KKBOXCommandManager(state_path=state_path)
        self.assertEqual(restored.get_command_status("persistent_cmd")["status"], "pending")
        self.assertEqual(restored.get_sidecar_status()["sidecar_version"], "1.3.0")
        self.assertEqual(restored.claim_next_command()["command_id"], command["command_id"])

    def test_running_report_renews_claim_lease(self):
        command = self.command_manager.enqueue_command(command_id="lease_cmd")
        claimed = self.command_manager.claim_next_command()
        old_claimed_at = claimed["claimed_at"]
        time.sleep(0.01)
        ok, _ = self.command_manager.report_command(command["command_id"], "running", {"stage": "albums"})
        self.assertTrue(ok)
        self.assertGreater(
            self.command_manager.get_command_status(command["command_id"])["claimed_at"],
            old_claimed_at,
        )

    def test_paused_report_requeues_command(self):
        command = self.command_manager.enqueue_command(command_id="paused_cmd")
        self.command_manager.claim_next_command()
        ok, _ = self.command_manager.report_command(command["command_id"], "paused", error="bridge offline")
        self.assertTrue(ok)
        self.assertEqual(self.command_manager.get_command_status(command["command_id"])["status"], "pending")
        self.assertEqual(self.command_manager.claim_next_command()["command_id"], command["command_id"])

    def test_rejects_unsupported_action(self):
        with self.assertRaises(ValueError):
            self.command_manager.enqueue_command(action="open_arbitrary_url")

    def test_ingest_kkbox_payload_and_persistence(self):
        payload = {
            "releases": [
                {
                    "source_id": "kk_alb_1",
                    "title": "新青年",
                    "artists": [{"name": "刺猬"}],
                    "release_type": "album",
                    "release_date": "2026-08-01",
                    "source_url": "https://play.kkbox.com/album/kk_alb_1",
                    "tracks": [
                        {"title": "Intro", "duration_ms": 60000, "track_number": 1, "source_id": "t1"},
                        {"title": "光芒", "duration_ms": 240000, "track_number": 2, "source_id": "t2"},
                    ],
                }
            ]
        }

        code, resp = ingest_kkbox_payload(payload, db=self.db)
        self.assertEqual(code, 200)
        self.assertEqual(resp["ingested_count"], 1)
        self.assertEqual(resp["candidates_count"], 1)

        candidates = self.db.get_candidates(platform="kkbox")
        self.assertEqual(len(candidates), 1)
        # Multi-track album should select physical 2nd track ("光芒")
        self.assertEqual(candidates[0]["track_title"], "光芒")

    def test_rejects_placeholder_only_kkbox_metadata(self):
        payload = {
            "releases": [{
                "source_id": "bad_album",
                "title": "KKBOX Web Player",
                "artists": [{"name": "KKBOX Artist"}],
                "tracks": [{"source_id": "bad_track", "title": "只有歌名"}],
            }]
        }
        code, response = ingest_kkbox_payload(payload, db=self.db)
        self.assertEqual(code, 200)
        self.assertEqual(response["ingested_count"], 0)
        self.assertTrue(response["validation_errors"])


if __name__ == "__main__":
    unittest.main()
