"""Unit tests for human review helper functions, tiered machine filtering, data editor, and notification debouncer."""

import datetime
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from song_discovery.db import DiscoveryDB
from song_discovery.review_helpers import (
    apply_machine_filter_to_legacy_pending,
    build_batch_decision_updates,
    classify_candidate_tier,
    compute_preview_signature,
    deduplicate_candidates,
    extract_selected_row_indices,
    filter_candidates_by_pool_and_criteria,
    generate_default_weekly_playlist_name,
    get_platforms_overview,
    get_publication_readiness,
    is_incomplete_kkbox_candidate,
    prepare_editor_rows,
    resolve_editor_decisions,
    schedule_refresh_command,
    should_send_notification,
    update_pending_tracking,
)


class TestReviewHelpers(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_review.db")
        self.db = DiscoveryDB(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_classify_candidate_tier_high_score_and_band_traits(self):
        # 1. High score (>=60.0) -> Tier 1
        c1 = {"relevance_score": 65.0, "artist_names": "歌手A", "relevance_reasons": ["基准分"]}
        tier_code, label, is_band = classify_candidate_tier(c1)
        self.assertEqual(tier_code, "tier1")
        self.assertIn("Tier 1", label)

        # 2. Score 50.0 but with Band keyword -> Tier 1
        c2 = {"relevance_score": 50.0, "artist_names": "安子与九妹乐队", "relevance_reasons": ["基准分"]}
        tier_code, label, is_band = classify_candidate_tier(c2)
        self.assertEqual(tier_code, "tier1")
        self.assertTrue(is_band)

        # 3. Score 50.0 but with Rock reason -> Tier 1
        c3 = {"relevance_score": 50.0, "artist_names": "某歌手", "relevance_reasons": ["摇滚流派加分"]}
        tier_code, label, is_band = classify_candidate_tier(c3)
        self.assertEqual(tier_code, "tier1")
        self.assertTrue(is_band)

        # 4. Standard baseline 50.0 without any band/rock signal -> Tier 2 (Secondary / Possible False Positives)
        c4 = {"relevance_score": 50.0, "artist_names": "普通流行歌手", "relevance_reasons": ["基准分: 标准音乐发行"]}
        tier_code, label, is_band = classify_candidate_tier(c4)
        self.assertEqual(tier_code, "tier2")
        self.assertIn("Tier 2", label)
        self.assertFalse(is_band)

        # 5. Explicit virtual-singer / soundtrack noise overrides collaboration score.
        c5 = {
            "relevance_score": 60.0,
            "artist_names": "洛天依 / 某制作人",
            "track_title": "游戏主题曲",
            "relevance_reasons": ["多人/合作音乐人参与 (+10分)"],
        }
        tier_code, _, is_band = classify_candidate_tier(c5)
        self.assertEqual(tier_code, "tier2")
        self.assertFalse(is_band)

    def test_filter_candidates_by_pool_and_criteria_preserves_all_data(self):
        cands = [
            {"id": 1, "relevance_score": 80.0, "artist_names": "刺猬乐队", "track_title": "火车", "platform": "qq", "review_status": "pending"},
            {"id": 2, "relevance_score": 50.0, "artist_names": "普通流行", "track_title": "小调", "platform": "netease", "review_status": "pending"},
            {"id": 3, "relevance_score": 60.0, "artist_names": "合作音乐人 / A / B", "track_title": "合唱", "platform": "kkbox", "review_status": "approved"},
        ]

        # Primary pool (Tier 1)
        tier1_items = filter_candidates_by_pool_and_criteria(cands, pool="tier1")
        self.assertEqual(len(tier1_items), 2)
        self.assertEqual({c["id"] for c in tier1_items}, {1, 3})

        # Secondary pool (Tier 2 - Low score retained)
        tier2_items = filter_candidates_by_pool_and_criteria(cands, pool="tier2")
        self.assertEqual(len(tier2_items), 1)
        self.assertEqual(tier2_items[0]["id"], 2)

        # All pool
        all_items = filter_candidates_by_pool_and_criteria(cands, pool="all")
        self.assertEqual(len(all_items), 3)

        # Quick toggle: only band / rock
        band_items = filter_candidates_by_pool_and_criteria(cands, pool="all", only_band_rock=True)
        self.assertEqual(len(band_items), 1)
        self.assertEqual({c["id"] for c in band_items}, {1})

    def test_prepare_editor_rows_and_build_batch_decisions(self):
        cands = [
            {"id": 101, "track_title": "歌A", "artist_names": "乐队A", "relevance_score": 80.0, "review_status": "pending", "platform": "qq"},
            {"id": 102, "track_title": "歌B", "artist_names": "歌手B", "relevance_score": 50.0, "review_status": "approved", "platform": "netease"},
        ]

        rows = prepare_editor_rows(cands, selected_ids=[101])
        self.assertEqual(len(rows), 2)
        # Check single selection column
        self.assertTrue(rows[0]["选择"])
        self.assertFalse(rows[1]["选择"])
        self.assertEqual(rows[0]["当前状态"], "待审核")
        self.assertEqual(rows[1]["当前状态"], "✅ 已通过")

        # Test batch update building: Approve
        app_updates = build_batch_decision_updates([101], decision="approved", notes="听感极好")
        self.assertEqual(len(app_updates), 1)
        self.assertEqual(app_updates[0]["candidate_id"], 101)
        self.assertEqual(app_updates[0]["status"], "approved")

        # Test batch update building: Reject without reason automatically defaults to 'other' and 'track'
        empty_rej_updates = build_batch_decision_updates([102], decision="rejected", reason_code="")
        self.assertEqual(len(empty_rej_updates), 1)
        self.assertEqual(empty_rej_updates[0]["status"], "rejected")
        self.assertEqual(empty_rej_updates[0]["reason_code"], "other")
        self.assertEqual(empty_rej_updates[0]["reason_scope"], "track")

        # Test batch update building: Invalid non-empty reason still raises ValueError
        with self.assertRaises(ValueError):
            build_batch_decision_updates([102], decision="rejected", reason_code="invalid_reason_xyz")

        # Test batch update building: Reject with explicit structured reason
        rej_updates = build_batch_decision_updates([102], decision="rejected", reason_code="mainstream_pop_idol", reason_scope="artist", notes="流行偶像")
        self.assertEqual(len(rej_updates), 1)
        self.assertEqual(rej_updates[0]["status"], "rejected")
        self.assertEqual(rej_updates[0]["reason_code"], "mainstream_pop_idol")
        self.assertEqual(rej_updates[0]["reason_scope"], "artist")

    def test_cross_platform_deduplication_preserves_raw_membership(self):
        candidates = [
            {
                "id": 11, "platform": "qq", "track_title": "涟漪",
                "artist_names": "供销社乐队", "track_url": "https://qq/11",
                "review_status": "pending",
            },
            {
                "id": 12, "platform": "netease", "track_title": "涟漪",
                "artist_names": "供销社乐队", "track_url": "https://netease/12",
                "review_status": "pending",
            },
            {
                "id": 13, "platform": "kkbox", "track_title": "涟漪 (Live)",
                "artist_names": "供销社乐队", "review_status": "pending",
            },
        ]

        result = deduplicate_candidates(candidates)
        self.assertEqual(len(result), 2)
        studio = next(item for item in result if item["track_title"] == "涟漪")
        self.assertEqual(studio["platform"], "netease")
        self.assertEqual(studio["raw_ids"], [11, 12])
        self.assertEqual(studio["platforms_display"], "网易云 / QQ音乐")

    def test_dedup_uses_filtered_representative_but_full_group_membership(self):
        qq = {"id": 21, "platform": "qq", "track_title": "LOOP", "artist_names": "A乐队"}
        netease = {"id": 22, "platform": "netease", "track_title": "LOOP", "artist_names": "A乐队"}
        result = deduplicate_candidates([qq], all_candidates=[qq, netease])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 21)
        self.assertEqual(result[0]["raw_ids"], [21, 22])

    def test_placeholder_artist_rows_are_not_merged(self):
        candidates = [
            {"id": 31, "platform": "kkbox", "track_title": "同名歌", "artist_names": "KKBOX Artist"},
            {"id": 32, "platform": "kkbox", "track_title": "同名歌", "artist_names": "KKBOX Artist"},
        ]
        self.assertEqual(len(deduplicate_candidates(candidates)), 2)
        self.assertTrue(is_incomplete_kkbox_candidate({
            "platform": "kkbox", "artist_names": "KKBOX Artist", "release_title": "KKBOX Web Player",
        }))
        self.assertFalse(is_incomplete_kkbox_candidate({
            "platform": "kkbox", "artist_names": "謝安琪", "release_title": "Kay Tse I",
        }))

    def test_extract_rows_from_row_and_cell_selections(self):
        selection = {
            "selection": {
                "rows": [4],
                "cells": [(1, "歌曲"), {"row": 3, "column": "艺人"}, {"rowIndex": 2}],
            }
        }
        self.assertEqual(extract_selected_row_indices(selection, total_rows=5), [1, 2, 3, 4])

    def test_duplicate_status_sync_does_not_create_extra_feedback(self):
        common = dict(
            platform="qq", release_type="single", release_date="2026-08-01",
            duration_ms=180000, track_number=1, release_url="", track_url="",
            selection_rule="single_first_track", raw_metadata={}, relevance_score=70.0,
            relevance_reasons=["test"],
        )
        first_id, _ = self.db.upsert_candidate(
            release_source_id="release-a", track_source_id="track-a",
            release_title="歌", track_title="歌", artist_names="乐队", **common,
        )
        second_id, _ = self.db.upsert_candidate(
            release_source_id="release-b", track_source_id="track-b",
            release_title="歌", track_title="歌", artist_names="乐队", **common,
        )
        self.db.update_review_status(first_id, "approved")
        self.assertEqual(
            self.db.bulk_sync_review_status([second_id], "approved", "duplicate sync"), 1
        )
        self.assertEqual(self.db.get_feedback_stats()["total"], 1)
        approved_ids = {row["id"] for row in self.db.get_candidates(status="approved")}
        self.assertEqual(approved_ids, {first_id, second_id})

    def test_machine_filter_removes_legacy_noise_from_required_human_review(self):
        common = dict(
            platform="qq", release_type="single", release_date="2026-08-01",
            duration_ms=180000, track_number=1, release_url="", track_url="",
            selection_rule="single_first_track", raw_metadata={},
        )
        high_id, _ = self.db.upsert_candidate(
            release_source_id="high_release", track_source_id="high_track",
            release_title="摇滚新作", track_title="火车", artist_names="刺猬乐队",
            relevance_score=80.0, relevance_reasons=["命中乐队/乐团特征"], **common,
        )
        low_id, _ = self.db.upsert_candidate(
            release_source_id="low_release", track_source_id="low_track",
            release_title="普通单曲", track_title="普通情歌", artist_names="普通歌手",
            relevance_score=50.0, relevance_reasons=["基准分: 标准音乐发行"], **common,
        )

        self.assertEqual(apply_machine_filter_to_legacy_pending(self.db), 1)
        self.assertEqual(self.db.get_stats()["pending"], 1)
        self.assertEqual(self.db.get_stats()["machine_filtered"], 1)
        self.assertEqual(self.db.get_candidates(status="machine_filtered")[0]["id"], low_id)
        self.assertEqual(self.db.get_feedback_stats()["total"], 0)

        self.db.update_review_status(high_id, "approved")
        readiness = get_publication_readiness(self.db)
        self.assertTrue(readiness["is_ready"])
        self.assertEqual(readiness["machine_filtered_count"], 1)

    def test_compute_preview_signature_consistency_and_invalidation(self):
        sig1 = compute_preview_signature([1, 2, 3], "华语新歌周刊 2026年第35周")
        sig2 = compute_preview_signature([3, 1, 2], "华语新歌周刊 2026年第35周")
        self.assertEqual(sig1, sig2)

        sig3 = compute_preview_signature([1, 2, 3], "华语新歌周刊 2026年第36周")
        self.assertNotEqual(sig1, sig3)

    def test_get_platforms_overview_aggregates_equal_sources(self):
        self.db.upsert_candidate(
            platform="qq", release_source_id="QR1", track_source_id="QT1",
            release_title="QQ专辑", track_title="QQ单曲", artist_names="刺猬",
            release_type="single", release_date="2026-08-01", duration_ms=180000,
            track_number=1, release_url="", track_url="", selection_rule="only_track",
            relevance_score=80.0, relevance_reasons=["摇滚"],
        )

        state = {
            "last_run_time": "2026-08-28T12:00:00",
            "platforms_summary": {
                "qq": {"status": "success", "last_run_time": "2026-08-28T12:00:00"},
            },
            "kkbox_sidecar": {
                "online": True,
                "status": "idle",
                "last_seen_iso": "2026-08-28T12:05:00",
            },
        }

        overview = get_platforms_overview(db=self.db, state=state)
        self.assertIn("qq", overview["platforms"])
        self.assertIn("netease", overview["platforms"])
        self.assertIn("kkbox", overview["platforms"])
        self.assertEqual(overview["platforms"]["qq"]["candidates_count"], 1)

    def test_get_platforms_overview_prefers_kkbox_official_api_status(self):
        state = {
            "platforms_summary": {
                "kkbox": {"status": "success", "last_run_time": "2026-08-30T12:00:00"},
            },
            "kkbox_sidecar": {"online": False, "status": "idle"},
        }
        with patch.dict(os.environ, {"KKBOX_CLIENT_ID": "configured", "KKBOX_CLIENT_SECRET": "configured"}):
            overview = get_platforms_overview(db=self.db, state=state)
        kkbox = overview["platforms"]["kkbox"]
        self.assertEqual(kkbox["status"], "success")
        self.assertEqual(kkbox["source_mode"], "official_open_api")
        self.assertTrue(kkbox["is_online"])
        self.assertIn("Official API Online", kkbox["health"])

    def test_schedule_refresh_command_idempotent(self):
        cmd_path = os.path.join(self.test_dir, "test_cmd.json")
        cmd1 = schedule_refresh_command(command_path=cmd_path)
        self.assertEqual(cmd1["status"], "pending")

        cmd2 = schedule_refresh_command(command_path=cmd_path)
        self.assertEqual(cmd1["command_id"], cmd2["command_id"])

    def test_publication_readiness_pending_remaining(self):
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

        readiness = get_publication_readiness(self.db)
        self.assertFalse(readiness["is_ready"])
        self.assertEqual(readiness["pending_count"], 1)

    def test_publication_readiness_ignores_incomplete_kkbox_placeholder(self):
        placeholder_id, _ = self.db.upsert_candidate(
            platform="kkbox",
            release_source_id="legacy-web-player",
            track_source_id="legacy-track",
            release_title="KKBOX Web Player",
            track_title="只有歌名的旧记录",
            artist_names="KKBOX Artist",
            release_type="single",
            release_date="2026-08-01",
            duration_ms=0,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="legacy_dom",
            relevance_score=0.0,
            relevance_reasons=[],
        )
        approved_id, _ = self.db.upsert_candidate(
            platform="netease",
            release_source_id="approved-release",
            track_source_id="approved-track",
            release_title="完整专辑",
            track_title="完整歌曲",
            artist_names="完整乐队",
            release_type="single",
            release_date="2026-08-01",
            duration_ms=180000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="single_first_track",
            relevance_score=80.0,
            relevance_reasons=["乐队特征"],
        )
        self.db.update_review_status(approved_id, "approved")
        readiness = get_publication_readiness(self.db)
        self.assertTrue(readiness["is_ready"])
        self.assertEqual(readiness["pending_count"], 0)
        self.assertEqual(readiness["incomplete_pending_count"], 1)
        self.assertEqual(self.db.get_candidate_by_id(placeholder_id)["review_status"], "pending")

    def test_generate_default_weekly_playlist_name(self):
        d = datetime.date(2026, 8, 28)
        name = generate_default_weekly_playlist_name(d)
        self.assertEqual(name, "华语新歌周刊 2026年第35周")

    def test_should_send_notification_debouncing_and_watermark(self):
        state = {
            "last_pending_count": 0,
            "pending_changed_at": 1000.0,
            "last_notified_pending_count": 0,
            "last_notified_time": 900.0,
        }
        self.assertFalse(should_send_notification(state, current_pending=0, debounce_seconds=60.0, current_time=1100.0))
        update_pending_tracking(state, current_pending=10, current_time=1000.0)
        self.assertFalse(should_send_notification(state, current_pending=10, debounce_seconds=60.0, current_time=1030.0))
        self.assertTrue(should_send_notification(state, current_pending=10, debounce_seconds=60.0, current_time=1070.0))


if __name__ == "__main__":
    unittest.main()
