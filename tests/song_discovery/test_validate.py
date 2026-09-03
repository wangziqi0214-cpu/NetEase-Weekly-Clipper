"""Unit tests for validation CLI execution."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from song_discovery.exceptions import ServiceUnavailableError
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track
from song_discovery.validate import ValidationRunner


class TestValidateRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.artist = Artist(name="测试歌手", id="1")
        self.track1 = Track(
            platform="qq",
            source_id="T1",
            title="曲目1",
            artists=[self.artist],
            album_title="专辑1",
            release_type="album",
            release_date="2026-08-20",
            duration_ms=180000,
            track_number=1,
        )
        self.track2 = Track(
            platform="qq",
            source_id="T2",
            title="曲目2",
            artists=[self.artist],
            album_title="专辑1",
            release_type="album",
            release_date="2026-08-20",
            duration_ms=200000,
            track_number=2,
        )
        self.release = Release(
            platform="qq",
            source_id="A1",
            title="专辑1",
            artists=[self.artist],
            album_title="专辑1",
            release_type="album",
            release_date="2026-08-20",
            track_count=2,
            tracks=[self.track1, self.track2],
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("song_discovery.validate.QQMusicCollector.collect_new_releases")
    def test_run_qq_validation(self, mock_qq):
        mock_qq.return_value = [self.release]

        runner = ValidationRunner(output_dir=self.test_dir, limit=1)
        res = runner.run_qq()

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["releases_count"], 1)
        self.assertIsNotNone(res["items"][0]["selected_track"])
        self.assertEqual(res["items"][0]["selected_track"]["title"], "曲目2")

        # Verify output files
        qq_json = os.path.join(self.test_dir, "qq_releases.json")
        self.assertTrue(os.path.exists(qq_json))

    @patch("song_discovery.validate.QQMusicCollector.collect_new_releases")
    def test_execute_all_creates_summary_files(self, mock_qq):
        mock_qq.return_value = [self.release]

        runner = ValidationRunner(output_dir=self.test_dir, limit=1)
        summary = runner.execute(platform="qq")

        self.assertIn("qq", summary["platforms"])
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "summary.json")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "summary.md")))

    @patch("song_discovery.validate.NetEaseCollector.collect_new_releases")
    def test_netease_unreachable_marked_as_failed_with_base_url(self, mock_netease):
        mock_netease.side_effect = ServiceUnavailableError(
            "Service unavailable", endpoint="http://localhost:9999"
        )
        runner = ValidationRunner(output_dir=self.test_dir, limit=1, netease_url="http://localhost:9999")
        res = runner.run_netease()
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["base_url"], "http://localhost:9999")
        self.assertIn("http://localhost:9999", res.get("reason", ""))

    def test_kkbox_without_credentials_marked_as_skipped(self):
        with patch.dict(os.environ, {}, clear=True):
            runner = ValidationRunner(output_dir=self.test_dir, limit=1)
            res = runner.run_kkbox()
            self.assertEqual(res["status"], "skipped")
            self.assertEqual(res["source_mode"], "awaiting_extension_bridge")
            self.assertIn("Missing", res.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
