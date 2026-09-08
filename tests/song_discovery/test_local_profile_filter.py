import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from song_discovery.db import DiscoveryDB
import song_discovery.review_api as review_api
from song_discovery.review_api import app


def seed_candidate(db: DiscoveryDB, artist_names: str, score: float = 50.0) -> int:
    candidate_id, _ = db.upsert_candidate(
        platform="qq",
        release_source_id=f"rel-{artist_names}",
        track_source_id=f"trk-{artist_names}",
        release_title="New Single",
        track_title="New Song",
        artist_names=artist_names,
        release_type="single",
        release_date="2026-09-01",
        duration_ms=180000,
        track_number=1,
        release_url="https://example.com/release",
        track_url="https://example.com/track",
        selection_rule="single_only_track",
        relevance_score=score,
        relevance_reasons=["基准分: 标准音乐发行 (高召回保留待审)"],
        raw_metadata={},
        initial_review_status="pending",
    )
    return candidate_id


class LocalProfileFilterTests(unittest.TestCase):
    def test_missing_failed_and_sparse_profiles_never_machine_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "profiles.db"))
            missing_id = seed_candidate(db, "Missing Artist")
            failed_id = seed_candidate(db, "Failed Artist")
            sparse_id = seed_candidate(db, "Sparse Artist")

            db.upsert_artist_knowledge({
                "artist_name": "Failed Artist",
                "display_name": "Failed Artist",
                "status": "failed",
                "error": "search timeout",
                "uncertainty": "high",
                "sources": [],
                "identity_context": {"type": "DJ", "genre": ["Pop"]},
            })
            db.upsert_artist_knowledge({
                "artist_name": "Sparse Artist",
                "display_name": "Sparse Artist",
                "status": "sparse",
                "uncertainty": "high",
                "sources": [],
                "identity_context": {"type": "DJ", "genre": ["Pop"]},
            })

            for candidate_id in (missing_id, failed_id, sparse_id):
                result = db.apply_local_profile_filter_to_candidate(candidate_id)
                candidate = db.get_candidate_by_id(candidate_id)
                self.assertEqual(candidate["review_status"], "pending")
                self.assertEqual(result["decision"]["action"], "keep_pending")
                self.assertTrue(
                    any("不作为排除依据" in reason for reason in candidate["relevance_reasons"])
                )

    def test_completed_non_target_profile_machine_filters_with_explainable_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "non_target.db"))
            candidate_id = seed_candidate(db, "Club Producer")
            db.upsert_artist_knowledge({
                "artist_name": "Club Producer",
                "display_name": "Club Producer",
                "factual_summary": "Club Producer 是主流流行音乐制作人，也以 DJ 身份发行舞曲。",
                "sources": ["https://music.example.com/club-producer"],
                "uncertainty": "medium",
                "identity_context": {"type": "Producer", "genre": ["Mainstream Pop", "Dance"]},
                "status": "completed",
                "search_queries": ["Club Producer"],
            })

            candidate = db.get_candidate_by_id(candidate_id)
            self.assertEqual(candidate["review_status"], "machine_filtered")
            self.assertTrue(candidate["review_notes"].startswith("本地画像机筛:"))
            self.assertTrue(any("明确非目标" in reason for reason in candidate["relevance_reasons"]))

    def test_completed_target_profile_does_not_machine_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "target.db"))
            candidate_id = seed_candidate(db, "Indie Pop Band")
            db.upsert_artist_knowledge({
                "artist_name": "Indie Pop Band",
                "display_name": "Indie Pop Band",
                "factual_summary": "Indie Pop Band 是独立摇滚乐队，风格包含 dream pop 与 shoegaze。",
                "sources": ["https://music.example.com/indie-pop-band"],
                "uncertainty": "low",
                "identity_context": {"type": "Band", "genre": ["Indie Pop", "Shoegaze"]},
                "status": "completed",
                "search_queries": ["Indie Pop Band"],
            })

            candidate = db.get_candidate_by_id(candidate_id)
            self.assertEqual(candidate["review_status"], "pending")
            self.assertTrue(any("未发现明确非目标" in reason for reason in candidate["relevance_reasons"]))

    def test_kkbox_placeholder_artist_never_filters_other_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "kkbox_placeholder.db"))
            candidate_id = db.upsert_candidate(
                platform="kkbox",
                release_source_id="rel-placeholder",
                track_source_id="trk-placeholder",
                release_title="KKBOX Web Player",
                track_title="A song with incomplete metadata",
                artist_names="KKBOX Artist",
                release_type="single",
                release_date="2026-09-01",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/release",
                track_url="https://example.com/track",
                selection_rule="single_only_track",
                relevance_score=50.0,
                relevance_reasons=["基准分: 标准音乐发行 (高召回保留待审)"],
                raw_metadata={},
                initial_review_status="pending",
            )[0]
            db.upsert_artist_knowledge({
                "artist_name": "KKBOX Artist",
                "display_name": "KKBOX Artist（实际对应艺人：右右）",
                "factual_summary": "这是一条由占位字段触发的资料，不能代表所有 KKBOX Artist 行。",
                "sources": ["https://music.example.com/placeholder"],
                "uncertainty": "medium",
                "identity_context": {"genre": ["流行"], "type": "Unknown"},
                "status": "completed",
            })

            candidate = db.get_candidate_by_id(candidate_id)
            self.assertEqual(candidate["review_status"], "pending")
            self.assertTrue(any("占位符" in reason for reason in candidate["relevance_reasons"]))

    def test_completed_profile_backfills_and_manual_restore_is_protected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "backfill.db"))
            auto_id = seed_candidate(db, "Pop Idol")
            manual_id = seed_candidate(db, "Pop Idol Manual")

            db.update_review_status(manual_id, "pending", notes="人工恢复保留")
            db.upsert_artist_knowledge({
                "artist_name": "Pop Idol",
                "display_name": "Pop Idol",
                "factual_summary": "Pop Idol 是普通流行偶像歌手。",
                "sources": ["https://music.example.com/pop-idol"],
                "uncertainty": "low",
                "identity_context": {"type": "Solo", "genre": ["Pop"]},
                "status": "completed",
                "search_queries": ["Pop Idol"],
            })
            db.upsert_artist_knowledge({
                "artist_name": "Pop Idol Manual",
                "display_name": "Pop Idol Manual",
                "factual_summary": "Pop Idol Manual 是普通流行偶像歌手。",
                "sources": ["https://music.example.com/pop-idol-manual"],
                "uncertainty": "low",
                "identity_context": {"type": "Solo", "genre": ["Pop"]},
                "status": "completed",
                "search_queries": ["Pop Idol Manual"],
            })

            self.assertEqual(db.get_candidate_by_id(auto_id)["review_status"], "machine_filtered")
            self.assertEqual(db.get_candidate_by_id(manual_id)["review_status"], "pending")
            self.assertEqual(db.get_candidate_by_id(manual_id)["review_notes"], "人工恢复保留")

    def test_profile_rescreen_restores_unreviewed_old_machine_filter_when_not_explicitly_non_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "restore_old.db"))
            candidate_id = seed_candidate(db, "Unknown Indie")
            self.assertTrue(db.set_machine_filtered(candidate_id, "旧机器初筛：低相关候选"))

            db.upsert_artist_knowledge({
                "artist_name": "Unknown Indie",
                "display_name": "Unknown Indie",
                "factual_summary": "Unknown Indie 是独立摇滚乐队。",
                "sources": ["https://music.example.com/unknown-indie"],
                "uncertainty": "low",
                "identity_context": {"type": "Band", "genre": ["Indie Rock"]},
                "status": "completed",
                "search_queries": ["Unknown Indie"],
            })

            candidate = db.get_candidate_by_id(candidate_id)
            self.assertEqual(candidate["review_status"], "pending")
            self.assertIn("恢复待人工审核", candidate["review_notes"])

    def test_human_review_is_immutable_during_profile_rescreen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "human_boundary.db"))
            candidate_id = seed_candidate(db, "Reviewed Pop Idol")
            self.assertTrue(db.update_review_status(candidate_id, "rejected", notes="人工否决"))
            before = db.get_candidate_by_id(candidate_id)

            db.upsert_artist_knowledge({
                "artist_name": "Reviewed Pop Idol",
                "display_name": "Reviewed Pop Idol",
                "factual_summary": "Reviewed Pop Idol 是普通流行偶像歌手。",
                "sources": ["https://music.example.com/reviewed-pop-idol"],
                "uncertainty": "low",
                "identity_context": {"type": "Producer", "genre": ["Mainstream Pop"]},
                "status": "completed",
            })

            after = db.get_candidate_by_id(candidate_id)
            self.assertEqual(after["review_status"], "rejected")
            self.assertEqual(after["review_notes"], before["review_notes"])
            self.assertEqual(after["relevance_reasons"], before["relevance_reasons"])

    def test_candidate_api_exposes_local_profile_screening(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_profiles.db"
            db = DiscoveryDB(str(db_path))
            seed_candidate(db, "Club Producer")
            db.upsert_artist_knowledge({
                "artist_name": "Club Producer",
                "display_name": "Club Producer",
                "factual_summary": "Club Producer 是流行音乐制作人与 DJ。",
                "sources": ["https://music.example.com/club-producer"],
                "uncertainty": "low",
                "identity_context": {"type": "Producer", "genre": ["Pop"]},
                "status": "completed",
                "search_queries": ["Club Producer"],
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", Path(temp_dir)):
                payload = TestClient(app).get(
                    "/api/candidates",
                    params={"status": "machine_filtered", "dedup": "false"},
                ).json()

            self.assertEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertEqual(item["local_profile_screening"]["action"], "machine_filter")
            self.assertTrue(any("本地画像筛选:" in reason for reason in item["screening_reasons"]))

    def test_candidate_api_labels_explicit_keep_pending_reason_correctly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_keep_pending.db"
            db = DiscoveryDB(str(db_path))
            seed_candidate(db, "Indie Band")
            db.upsert_artist_knowledge({
                "artist_name": "Indie Band",
                "display_name": "Indie Band",
                "factual_summary": "Indie Band 是独立摇滚乐队。",
                "sources": ["https://music.example.com/indie-band"],
                "uncertainty": "low",
                "identity_context": {"type": "Band", "genre": ["Indie Rock"]},
                "status": "completed",
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", Path(temp_dir)):
                payload = TestClient(app).get(
                    "/api/candidates",
                    params={"status": "pending", "dedup": "false"},
                ).json()

            self.assertEqual(payload["items"][0]["local_profile_screening"]["status"], "completed")
            self.assertEqual(payload["items"][0]["local_profile_screening"]["action"], "keep_pending")


if __name__ == "__main__":
    unittest.main()
