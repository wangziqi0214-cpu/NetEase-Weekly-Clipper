"""Unit tests for video workflow runner and authoritative track ID pipeline."""

import json
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from song_discovery.db import DiscoveryDB
from song_discovery.video_workflow_runner import run_video_workflow
from src.crawler.crawler import crawl_playlist_from_track_ids


class TestVideoWorkflowRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_discovery.db")
        self.db = DiscoveryDB(db_path=self.db_path)
        self.job_dir = Path(self.test_dir) / "output" / "video_jobs" / "video_test_123"
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_successful_video_workflow_from_ordered_track_ids(self, mock_crawl, mock_run):
        ordered_ids = [f"337000000{i}" for i in range(12)]
        job_id = "video_test_123"
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_001",
            "playlist_id": "PL_12345",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_12345",
            "ordered_track_ids": ordered_ids,
            "status": "queued",
            "log_path": str(self.job_dir / "workflow.log"),
        })

        # Mock crawler to write valid raw_playlist.json with matching ordered tracks
        def mock_crawl_impl(track_ids, playlist_id=None, url=None, output_path="raw_playlist.json", **kwargs):
            data = {
                "version": "2.1",
                "playlist_id": playlist_id or "PL_12345",
                "playlist_name": "华语新歌周刊",
                "tracks": [{"id": tid, "name": f"Song {tid}"} for tid in track_ids],
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return output_path

        mock_crawl.side_effect = mock_crawl_impl
        mock_run.return_value = MagicMock(returncode=0)
        for filename in ("intro.mp4", "song_info.md", "release_report.md", "publish_copy.md", "final_video.mp4"):
            (self.job_dir / filename).write_bytes(b"artifact")

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
            url="https://music.163.com/#/playlist?id=PL_12345",
        )

        self.assertEqual(ret, 0)
        mock_crawl.assert_called_once()
        call_kwargs = mock_crawl.call_args.kwargs
        self.assertEqual(call_kwargs["track_ids"], ordered_ids)
        self.assertEqual(call_kwargs["playlist_id"], "PL_12345")

        # Verify run.py was called with from_raw, NOT full
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("from_raw", cmd)
        self.assertNotIn("full", cmd)
        self.assertIn("--raw-path", cmd)
        self.assertIn(str(self.job_dir / "raw_playlist.json"), cmd)

        # Verify DB status updated to completed
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["error"], "")

    def test_fail_fast_when_job_not_found(self):
        ret = run_video_workflow(
            db_path=self.db_path,
            job_id="non_existent_job",
            job_dir_str=str(self.job_dir),
        )
        self.assertEqual(ret, 1)

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_resume_from_researched_preserves_order_and_skips_crawl(self, mock_crawl, mock_run):
        job_id = "video_test_123"
        ordered_ids = ["101", "102", "103"]
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_resume",
            "playlist_id": "PL_resume",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_resume",
            "ordered_track_ids": ordered_ids,
            "status": "cancelled",
            "log_path": str(self.job_dir / "workflow.log"),
        })
        with (self.job_dir / "researched_playlist.json").open("w", encoding="utf-8") as f:
            json.dump({"tracks": [{"id": value} for value in ordered_ids]}, f)
        mock_run.return_value = MagicMock(returncode=0)
        for filename in ("intro.mp4", "song_info.md", "release_report.md", "publish_copy.md", "final_video.mp4"):
            (self.job_dir / filename).write_bytes(b"artifact")

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
            resume_from_researched=True,
        )

        self.assertEqual(ret, 0)
        mock_crawl.assert_not_called()
        command = mock_run.call_args[0][0]
        self.assertIn("resume_researched", command)
        self.assertIn(str(self.job_dir / "researched_playlist.json"), command)

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_resume_from_raw_preserves_order_and_skips_crawl(self, mock_crawl, mock_run):
        job_id = "video_test_123"
        ordered_ids = ["201", "202", "203"]
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_raw_resume",
            "playlist_id": "PL_raw_resume",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_raw_resume",
            "ordered_track_ids": ordered_ids,
            "status": "cancelled",
            "log_path": str(self.job_dir / "workflow.log"),
        })
        (self.job_dir / "raw_playlist.json").write_text(
            json.dumps({"tracks": [{"id": value} for value in ordered_ids]}),
            encoding="utf-8",
        )
        mock_run.return_value = MagicMock(returncode=0)
        for filename in ("intro.mp4", "song_info.md", "release_report.md", "publish_copy.md", "final_video.mp4"):
            (self.job_dir / filename).write_bytes(b"artifact")

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
            resume_from_raw=True,
        )

        self.assertEqual(ret, 0)
        mock_crawl.assert_not_called()
        command = mock_run.call_args[0][0]
        self.assertIn("from_raw", command)
        self.assertIn(str(self.job_dir / "raw_playlist.json"), command)
        self.assertEqual(self.db.get_video_workflow_job(job_id)["status"], "completed")

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    def test_pipeline_success_without_artifacts_is_failed(self, mock_run):
        job_id = "video_test_123"
        ordered_ids = ["101"]
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_missing_artifacts",
            "playlist_id": "PL_missing",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_missing",
            "ordered_track_ids": ordered_ids,
            "status": "cancelled",
            "log_path": str(self.job_dir / "workflow.log"),
        })
        (self.job_dir / "researched_playlist.json").write_text(
            json.dumps({"tracks": [{"id": "101"}]}), encoding="utf-8"
        )
        mock_run.return_value = MagicMock(returncode=0)
        ret = run_video_workflow(
            db_path=self.db_path, job_id=job_id, job_dir_str=str(self.job_dir),
            resume_from_researched=True,
        )
        self.assertEqual(ret, 1)
        self.assertEqual(self.db.get_video_workflow_job(job_id)["status"], "failed")

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    def test_pipeline_fails_when_only_publish_copy_missing(self, mock_run):
        job_id = "video_test_pubcopy_missing"
        ordered_ids = ["101"]
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_missing_pubcopy",
            "playlist_id": "PL_missing",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_missing",
            "ordered_track_ids": ordered_ids,
            "status": "cancelled",
            "log_path": str(self.job_dir / "workflow.log"),
        })
        (self.job_dir / "researched_playlist.json").write_text(
            json.dumps({"tracks": [{"id": "101"}]}), encoding="utf-8"
        )
        mock_run.return_value = MagicMock(returncode=0)
        for filename in ("intro.mp4", "song_info.md", "release_report.md", "final_video.mp4"):
            (self.job_dir / filename).write_bytes(b"artifact")
        ret = run_video_workflow(
            db_path=self.db_path, job_id=job_id, job_dir_str=str(self.job_dir),
            resume_from_researched=True,
        )
        self.assertEqual(ret, 1)
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("publish_copy.md", job["error"])

    def test_fail_fast_when_ordered_track_ids_empty(self):
        job_id = "video_empty_tracks"
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_002",
            "playlist_id": "PL_999",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_999",
            "ordered_track_ids": [],
            "status": "queued",
            "log_path": str(self.job_dir / "workflow.log"),
        })

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
        )
        self.assertEqual(ret, 1)
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("ordered_track_ids", job["error"])

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_fail_fast_when_crawler_fails_or_drops_tracks(self, mock_crawl, mock_run):
        job_id = "video_crawl_fail"
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_003",
            "playlist_id": "PL_123",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_123",
            "ordered_track_ids": ["101", "102", "103"],
            "status": "queued",
            "log_path": str(self.job_dir / "workflow.log"),
        })

        mock_crawl.side_effect = RuntimeError("Failed to fetch details for 1 tracks: ['103']. Expected 3 tracks, received 2. Fail fast!")

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
        )
        self.assertEqual(ret, 1)
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Failed to generate raw_playlist.json", job["error"])
        mock_run.assert_not_called()

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_fail_fast_when_raw_playlist_track_count_mismatch(self, mock_crawl, mock_run):
        job_id = "video_count_mismatch"
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_004",
            "playlist_id": "PL_123",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_123",
            "ordered_track_ids": ["101", "102", "103", "104"],
            "status": "queued",
            "log_path": str(self.job_dir / "workflow.log"),
        })

        # Simulate raw_playlist.json having only 3 tracks instead of 4
        def mock_crawl_count_error(track_ids, output_path="raw_playlist.json", **kwargs):
            data = {
                "version": "2.1",
                "playlist_id": "PL_123",
                "tracks": [{"id": "101"}, {"id": "102"}, {"id": "103"}],
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return output_path

        mock_crawl.side_effect = mock_crawl_count_error

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
        )
        self.assertEqual(ret, 1)
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Track count mismatch", job["error"])
        mock_run.assert_not_called()

    @patch("song_discovery.video_workflow_runner.subprocess.run")
    @patch("song_discovery.video_workflow_runner.crawl_playlist_from_track_ids")
    def test_fail_fast_when_raw_playlist_track_order_mismatch(self, mock_crawl, mock_run):
        job_id = "video_order_mismatch"
        self.db.create_video_workflow_job({
            "job_id": job_id,
            "publication_id": "pub_005",
            "playlist_id": "PL_123",
            "playlist_url": "https://music.163.com/#/playlist?id=PL_123",
            "ordered_track_ids": ["101", "102", "103"],
            "status": "queued",
            "log_path": str(self.job_dir / "workflow.log"),
        })

        # Simulate raw_playlist.json having tracks in the wrong order
        def mock_crawl_order_error(track_ids, output_path="raw_playlist.json", **kwargs):
            data = {
                "version": "2.1",
                "playlist_id": "PL_123",
                "tracks": [{"id": "102"}, {"id": "101"}, {"id": "103"}],
            }
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return output_path

        mock_crawl.side_effect = mock_crawl_order_error

        ret = run_video_workflow(
            db_path=self.db_path,
            job_id=job_id,
            job_dir_str=str(self.job_dir),
        )
        self.assertEqual(ret, 1)
        job = self.db.get_video_workflow_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Track order mismatch", job["error"])
        mock_run.assert_not_called()


class TestCrawlerFromTrackIds(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_json = os.path.join(self.test_dir, "raw_playlist.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("src.crawler.crawler.download_cover", return_value="/fake/cover.jpg")
    @patch("src.crawler.crawler.download_track", return_value="/fake/audio.mp3")
    @patch("src.crawler.crawler.get_album_info", return_value={"description": "", "size": 1, "type": "EP", "company": ""})
    @patch("src.crawler.crawler.apis.track.GetTrackLyrics", return_value={"lrc": {"lyric": "[00:01.00]test lyric"}})
    @patch("src.crawler.crawler.apis.track.GetTrackAudio", return_value={"data": [{"url": "http://audio.mp3"}]})
    @patch("src.crawler.crawler.apis.playlist.GetPlaylistInfo")
    @patch("src.crawler.crawler.apis.track.GetTrackDetail")
    def test_crawl_playlist_from_track_ids_preserves_order(
        self, mock_get_track_detail, mock_get_playlist_info, *args
    ):
        mock_get_playlist_info.return_value = {
            "code": 200,
            "playlist": {
                "id": "12345",
                "name": "本周新歌",
                "coverImgUrl": "http://cover.jpg",
                "description": "每周精选",
            },
        }

        # Return songs from API in a different (reverse) order
        mock_get_track_detail.return_value = {
            "code": 200,
            "songs": [
                {"id": "203", "name": "Third Song", "ar": [{"name": "Artist 3"}], "al": {"id": "a3", "name": "Alb 3"}, "dt": 200000},
                {"id": "201", "name": "First Song", "ar": [{"name": "Artist 1"}], "al": {"id": "a1", "name": "Alb 1"}, "dt": 180000},
                {"id": "202", "name": "Second Song", "ar": [{"name": "Artist 2"}], "al": {"id": "a2", "name": "Alb 2"}, "dt": 210000},
            ],
        }

        target_ids = ["201", "202", "203"]
        out = crawl_playlist_from_track_ids(
            track_ids=target_ids,
            playlist_id="12345",
            output_path=self.output_json,
            cookie_path="non_existent_cookie.txt",
        )

        self.assertTrue(os.path.exists(out))
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["playlist_name"], "本周新歌")
        self.assertEqual(len(data["tracks"]), 3)
        self.assertEqual([t["id"] for t in data["tracks"]], ["201", "202", "203"])
        self.assertEqual(data["tracks"][0]["name"], "First Song")
        self.assertEqual(data["tracks"][1]["name"], "Second Song")
        self.assertEqual(data["tracks"][2]["name"], "Third Song")

    @patch("src.crawler.crawler.apis.track.GetTrackDetail")
    def test_crawl_playlist_from_track_ids_fails_fast_on_missing_track(self, mock_get_track_detail):
        # API only returns 2 songs instead of 3 requested
        mock_get_track_detail.return_value = {
            "code": 200,
            "songs": [
                {"id": "201", "name": "First Song", "ar": [{"name": "Artist 1"}], "al": {"id": "a1", "name": "Alb 1"}, "dt": 180000},
                {"id": "202", "name": "Second Song", "ar": [{"name": "Artist 2"}], "al": {"id": "a2", "name": "Alb 2"}, "dt": 210000},
            ],
        }

        target_ids = ["201", "202", "203"]
        with self.assertRaises(RuntimeError) as ctx:
            crawl_playlist_from_track_ids(
                track_ids=target_ids,
                playlist_id="12345",
                output_path=self.output_json,
                cookie_path="non_existent_cookie.txt",
            )

        self.assertIn("203", str(ctx.exception))
        self.assertIn("Fail fast", str(ctx.exception))

    def test_crawl_playlist_from_track_ids_rejects_empty_ids(self):
        with self.assertRaises(ValueError):
            crawl_playlist_from_track_ids(
                track_ids=[],
                output_path=self.output_json,
            )


if __name__ == "__main__":
    unittest.main()
