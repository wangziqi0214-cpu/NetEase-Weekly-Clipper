"""Unit tests for SQLite persistence layer."""

import os
import shutil
import tempfile
import unittest
from song_discovery.db import DiscoveryDB
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track


class TestDiscoveryDB(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_discovery.db")
        self.db = DiscoveryDB(db_path=self.db_path)
        self.artist = Artist(name="痛仰乐队", id="A101")
        self.track = Track(
            platform="qq",
            source_id="T101",
            title="公路之歌",
            artists=[self.artist],
            album_title="今日青年",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=240000,
            track_number=2,
            source_url="https://y.qq.com/n/ryqq/songDetail/T101",
        )
        self.release = Release(
            platform="qq",
            source_id="REL101",
            title="今日青年",
            artists=[self.artist],
            album_title="今日青年",
            release_type="album",
            release_date="2026-08-01",
            track_count=10,
            source_url="https://y.qq.com/n/ryqq/albumDetail/REL101",
            tracks=[self.track],
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_runs_lifecycle(self):
        run_id = self.db.start_run(platform="all", notes="test run")
        self.assertIsNotNone(run_id)
        self.db.finish_run(run_id=run_id, status="success", collected_count=5, candidates_count=4)

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            self.assertEqual(row["status"], "success")
            self.assertEqual(row["collected_count"], 5)
            self.assertEqual(row["candidates_count"], 4)

    def test_idempotent_upsert_release(self):
        id1 = self.db.upsert_release(self.release)
        id2 = self.db.upsert_release(self.release)
        self.assertEqual(id1, id2)

        stats = self.db.get_stats()
        self.assertEqual(stats["total_releases"], 1)

    def test_idempotent_upsert_candidate_preserves_review_status(self):
        cid, is_new = self.db.upsert_candidate(
            platform=self.release.platform,
            release_source_id=self.release.source_id,
            track_source_id=self.track.source_id,
            release_title=self.release.title,
            track_title=self.track.title,
            artist_names=self.track.artist_names_str,
            release_type=self.release.release_type,
            release_date=self.release.release_date,
            duration_ms=self.track.duration_ms,
            track_number=self.track.track_number,
            release_url=self.release.source_url,
            track_url=self.track.source_url,
            selection_rule="2nd_track (2/10)",
            relevance_score=85.0,
            relevance_reasons=["命中乐队特征 (+30分)"],
        )
        self.assertTrue(is_new)

        # Mark as approved
        self.db.update_review_status(candidate_id=cid, status="approved", notes="通过推荐")
        cand_before = self.db.get_candidate_by_id(cid)
        self.assertEqual(cand_before["review_status"], "approved")

        # Re-upsert candidate
        cid2, is_new2 = self.db.upsert_candidate(
            platform=self.release.platform,
            release_source_id=self.release.source_id,
            track_source_id=self.track.source_id,
            release_title=self.release.title,
            track_title=self.track.title,
            artist_names=self.track.artist_names_str,
            release_type=self.release.release_type,
            release_date=self.release.release_date,
            duration_ms=self.track.duration_ms,
            track_number=self.track.track_number,
            release_url=self.release.source_url,
            track_url=self.track.source_url,
            selection_rule="2nd_track (2/10)",
            relevance_score=90.0,
            relevance_reasons=["更新评分"],
        )
        self.assertEqual(cid, cid2)
        self.assertFalse(is_new2)

        # Check that review_status was preserved
        cand_after = self.db.get_candidate_by_id(cid)
        self.assertEqual(cand_after["review_status"], "approved")
        self.assertEqual(cand_after["relevance_score"], 90.0)

    def test_bulk_update_and_filter(self):
        # Insert 2 candidates
        c1, _ = self.db.upsert_candidate(
            platform="qq",
            release_source_id="R1",
            track_source_id="T1",
            release_title="专辑1",
            track_title="曲目1",
            artist_names="歌手A",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=75.0,
            relevance_reasons=["基准分"],
        )
        c2, _ = self.db.upsert_candidate(
            platform="netease",
            release_source_id="R2",
            track_source_id="T2",
            release_title="专辑2",
            track_title="曲目2",
            artist_names="歌手B",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=200000,
            track_number=2,
            release_url="",
            track_url="",
            selection_rule="2nd_track",
            relevance_score=60.0,
            relevance_reasons=["基准分"],
        )

        updates = [
            {"candidate_id": c1, "status": "approved", "notes": "good"},
            {"candidate_id": c2, "status": "rejected", "notes": "not rock"},
        ]
        cnt = self.db.bulk_update_review_status(updates)
        self.assertEqual(cnt, 2)

        approved = self.db.get_candidates(status="approved")
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], c1)

        netease_cands = self.db.get_candidates(platform="netease")
        self.assertEqual(len(netease_cands), 1)
        self.assertEqual(netease_cands[0]["id"], c2)

    def test_export_candidates(self):
        self.db.upsert_candidate(
            platform="qq",
            release_source_id="R1",
            track_source_id="T1",
            release_title="专辑1",
            track_title="曲目1",
            artist_names="歌手A",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=75.0,
            relevance_reasons=["基准分"],
        )
        all_cands = self.db.export_candidates(status="all")
        self.assertEqual(len(all_cands), 1)

    def test_delivery_order_and_video_job_persistence(self):
        self.db.save_delivery_order("周刊 W35", [9, 3, 7])
        self.assertEqual(self.db.get_delivery_order("周刊 W35"), [9, 3, 7])
        self.db.create_video_workflow_job({
            "job_id": "video_test", "publication_id": "pub_test",
            "playlist_id": "123", "playlist_url": "https://music.163.com/#/playlist?id=123",
            "ordered_track_ids": ["a", "b"], "status": "queued", "log_path": "/tmp/test.log",
        })
        self.db.update_video_workflow_job("video_test", "running", pid=321)
        job = self.db.get_latest_video_workflow_job()
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["ordered_track_ids"], ["a", "b"])
        job_by_id = self.db.get_video_workflow_job("video_test")
        self.assertIsNotNone(job_by_id)
        self.assertEqual(job_by_id["job_id"], "video_test")
        self.assertEqual(job_by_id["ordered_track_ids"], ["a", "b"])
        self.assertIsNone(self.db.get_video_workflow_job("non_existent"))


if __name__ == "__main__":
    unittest.main()
