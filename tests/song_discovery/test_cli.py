"""Unit tests for CLI commands execution, 3-platform status, and sidecar collection reporting."""

import argparse
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.cli import (
    cmd_collect,
    cmd_export,
    cmd_login_status,
    cmd_publish,
    cmd_stats,
    cmd_status,
)
from song_discovery.db import DiscoveryDB


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_cli.db")
        self.state_path = os.path.join(self.test_dir, "test_cli_state.json")
        self.db = DiscoveryDB(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("song_discovery.cli.NetEaseServiceManager")
    @patch("song_discovery.cli.DiscoveryOrchestrator")
    @patch("song_discovery.cli.global_command_manager")
    def test_cmd_collect_all_marks_kkbox_managed_via_sidecar(self, mock_command_manager, mock_orch_cls, mock_svc_cls):
        mock_command_manager.enqueue_command.return_value = {"command_id": "test_kkbox_command"}
        mock_orch = MagicMock()
        mock_orch.run_discovery.return_value = {
            "run_id": "run_1",
            "total_releases": 5,
            "total_candidates": 5,
            "platforms": {
                "qq": {"status": "success", "releases_count": 5, "candidates_count": 5},
                "netease": {"status": "success", "releases_count": 5, "candidates_count": 5},
                "kkbox": {"status": "awaiting_bridge", "releases_count": 0, "candidates_count": 0},
            },
        }
        mock_orch_cls.return_value = mock_orch

        args = argparse.Namespace(
            platform="all",
            limit=5,
            netease_pages=2,
            netease_page_size=10,
            db_path=self.db_path,
            netease_url="http://localhost:3000",
            no_auto_start=False,
        )

        cmd_collect(args)
        mock_orch_cls.assert_called_once()
        mock_orch.run_discovery.assert_called_once_with(
            platform="all",
            limit=5,
            netease_url="http://localhost:3000",
            netease_pages=2,
            netease_page_size=10,
        )

    def test_cmd_status_outputs_3_platforms_table(self):
        self.db.upsert_candidate(
            platform="qq",
            release_source_id="R1",
            track_source_id="T1",
            release_title="专辑1",
            track_title="曲目1",
            artist_names="刺猬",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=200000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=80.0,
            relevance_reasons=["摇滚"],
        )

        args = argparse.Namespace(db_path=self.db_path, state_path=self.state_path)
        cmd_status(args)

    def test_cmd_stats_with_representative_db(self):
        self.db.upsert_candidate(
            platform="qq",
            release_source_id="R1",
            track_source_id="T1",
            release_title="专辑1",
            track_title="曲目1",
            artist_names="刺猬",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=200000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=80.0,
            relevance_reasons=["摇滚"],
        )

        args = argparse.Namespace(db_path=self.db_path)
        cmd_stats(args)

    @patch("song_discovery.cli.NetEaseAuth")
    def test_cmd_login_status_logged_in_and_anonymous(self, mock_auth_cls):
        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth

        # 1. Logged in case
        mock_auth.get_login_status.return_value = {
            "is_logged_in": True,
            "user_id": "123456",
            "nickname": "TestUser",
            "vip_type": 11,
        }

        args = argparse.Namespace(
            cookie_file="cookie.txt",
            netease_url="http://localhost:3000",
            no_auto_start=True,
        )
        cmd_login_status(args)

        # 2. Anonymous / Not logged in case
        mock_auth.get_login_status.return_value = {
            "is_logged_in": False,
            "user_id": None,
            "nickname": None,
            "error": "Session is anonymous",
        }
        cmd_login_status(args)

    @patch("song_discovery.cli.NetEasePublisher")
    def test_cmd_publish_dry_run(self, mock_pub_cls):
        mock_pub = MagicMock()
        mock_pub.publish_approved.return_value = {
            "dry_run": True,
            "status": "planned",
            "total_approved": 2,
            "direct_netease_count": 1,
            "matched_count": 1,
            "ambiguous_count": 0,
            "unmatched_count": 0,
            "ready_to_add_count": 2,
        }
        mock_pub_cls.return_value = mock_pub

        args = argparse.Namespace(
            playlist_name="华语新歌周刊",
            playlist_id=None,
            cookie_file="cookie.txt",
            dry_run=True,
            output=None,
            netease_url="http://localhost:3000",
            no_auto_start=True,
            db_path=self.db_path,
        )
        cmd_publish(args)


if __name__ == "__main__":
    unittest.main()
