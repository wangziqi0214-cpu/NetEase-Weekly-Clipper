"""Unit tests for DiscoveryOrchestrator."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.db import DiscoveryDB
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track
from song_discovery.orchestrator import DiscoveryOrchestrator


class TestDiscoveryOrchestrator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_orch.db")
        self.db = DiscoveryDB(db_path=self.db_path)
        self.orchestrator = DiscoveryOrchestrator(db=self.db)

        self.artist = Artist(name="刺猬乐队", id="A1")
        self.track1 = Track(
            platform="qq",
            source_id="T1",
            title="白日梦蓝",
            artists=[self.artist],
            album_title="白日梦蓝",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=210000,
            track_number=1,
        )
        self.track2 = Track(
            platform="qq",
            source_id="T2",
            title="火车驶向云外，梦安魂于九霄",
            artists=[self.artist],
            album_title="白日梦蓝",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=280000,
            track_number=2,
        )
        self.multi_release = Release(
            platform="qq",
            source_id="R_ALBUM",
            title="白日梦蓝",
            artists=[self.artist],
            album_title="白日梦蓝",
            release_type="album",
            release_date="2026-08-01",
            track_count=2,
            tracks=[self.track1, self.track2],
        )

        self.single_track = Track(
            platform="qq",
            source_id="T_S1",
            title="孤独的朋克单曲",
            artists=[self.artist],
            album_title="孤独的朋克单曲",
            release_type="single",
            release_date="2026-08-01",
            duration_ms=190000,
            track_number=1,
        )
        self.single_release = Release(
            platform="qq",
            source_id="R_SINGLE",
            title="孤独的朋克单曲",
            artists=[self.artist],
            album_title="孤独的朋克单曲",
            release_type="single",
            release_date="2026-08-01",
            track_count=1,
            tracks=[self.single_track],
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("song_discovery.orchestrator.QQMusicCollector.collect_new_releases")
    def test_run_discovery_applies_selection_and_scoring(self, mock_qq):
        mock_qq.return_value = [self.multi_release, self.single_release]

        summary = self.orchestrator.run_discovery(platform="qq", limit=2)
        self.assertEqual(summary["total_releases"], 2)
        self.assertEqual(summary["total_candidates"], 2)

        candidates = self.db.get_candidates(status="pending")
        self.assertEqual(len(candidates), 2)

        # Multi-track release should pick track 2
        multi_cand = next(c for c in candidates if c["release_source_id"] == "R_ALBUM")
        self.assertEqual(multi_cand["track_title"], "火车驶向云外，梦安魂于九霄")
        self.assertEqual(multi_cand["track_number"], 2)

        # Single-track release should pick track 1
        single_cand = next(c for c in candidates if c["release_source_id"] == "R_SINGLE")
        self.assertEqual(single_cand["track_title"], "孤独的朋克单曲")
        self.assertEqual(single_cand["track_number"], 1)

    @patch("song_discovery.orchestrator.QQMusicCollector.collect_new_releases")
    def test_new_generic_release_is_machine_filtered_but_retained(self, mock_qq):
        artist = Artist(name="普通歌手")
        track = Track(
            platform="qq", source_id="GENERIC_T", title="普通情歌",
            artists=[artist], album_title="普通单曲", release_type="single",
            release_date="2026-08-01", duration_ms=180000, track_number=1,
        )
        release = Release(
            platform="qq", source_id="GENERIC_R", title="普通单曲",
            artists=[artist], album_title="普通单曲", release_type="single",
            release_date="2026-08-01", track_count=1, tracks=[track],
        )
        mock_qq.return_value = [release]

        summary = self.orchestrator.run_discovery(platform="qq", limit=1)
        self.assertEqual(summary["total_candidates"], 1)
        self.assertEqual(len(self.db.get_candidates(status="pending")), 0)
        retained = self.db.get_candidates(status="machine_filtered")
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["track_title"], "普通情歌")

    @patch("song_discovery.orchestrator.NetEaseCollector.collect_new_releases")
    def test_run_discovery_forwards_netease_pagination_params(self, mock_netease):
        mock_netease.return_value = [self.multi_release]

        summary = self.orchestrator.run_discovery(
            platform="netease",
            limit=5,
            netease_pages=15,
            netease_page_size=20,
        )

        mock_netease.assert_called_once_with(pages=15, page_size=20)
        self.assertEqual(summary["total_releases"], 1)

    def test_run_discovery_kkbox_without_credentials_reports_skipped(self):
        with patch.dict(os.environ, {}, clear=True):
            summary = self.orchestrator.run_discovery(platform="kkbox", limit=5)
            kk_info = summary["platforms"]["kkbox"]
            self.assertEqual(kk_info["status"], "skipped")
            self.assertEqual(kk_info["source_mode"], "awaiting_extension_bridge")
            self.assertIn("Chrome extension", kk_info["reason"])

    @patch("song_discovery.orchestrator.NetEaseCollector.collect_new_releases")
    def test_orchestrator_summary_propagation_with_warnings(self, mock_netease):
        mock_netease.return_value = [self.multi_release]

        with patch.object(
            DiscoveryOrchestrator,
            "_process_platform",
            return_value={
                "platform": "netease",
                "status": "success_with_warnings",
                "releases_count": 1,
                "candidates_count": 1,
                "candidates": [],
                "warning_count": 1,
                "warnings": [{"album_id": "392010339", "album_title": "异常专辑", "error": "HTTP 405"}],
            },
        ):
            summary = self.orchestrator.run_discovery(platform="netease")
            self.assertEqual(summary["platforms"]["netease"]["status"], "success_with_warnings")
            self.assertEqual(summary["platforms"]["netease"]["warning_count"], 1)
            self.assertEqual(len(summary["platforms"]["netease"]["warnings"]), 1)

    def test_export_candidates_json(self):
        self.db.upsert_candidate(
            platform="qq",
            release_source_id="R1",
            track_source_id="T1",
            release_title="专辑1",
            track_title="曲目1",
            artist_names="刺猬乐队",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=85.0,
            relevance_reasons=["乐队特征"],
        )
        # Mark as approved
        self.db.update_review_status(1, "approved")

        export_file = os.path.join(self.test_dir, "export.json")
        res = self.orchestrator.export_candidates(status="approved", output_file=export_file)
        self.assertEqual(res["count"], 1)
        self.assertTrue(os.path.exists(export_file))


if __name__ == "__main__":
    unittest.main()
