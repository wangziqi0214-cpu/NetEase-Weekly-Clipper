"""Comprehensive unit and integration tests for structured feedback, preference learning,
rejection reasons validation, human decision protection, cold start, activation threshold,
exploration sampling, 3-pool routing, model version rollback, and recall safety gate.
"""

import os
import shutil
import tempfile
import unittest

from song_discovery.db import DiscoveryDB
from song_discovery.models import (
    REJECTION_REASON_LABELS,
    REJECTION_REASONS,
    REASON_SCOPE_LABELS,
    ReasonScope,
    RejectionReason,
)
from song_discovery.preference_learner import (
    DEFAULT_EXPLORATION_RATE,
    MIN_NEGATIVE_SAMPLES,
    MIN_POSITIVE_SAMPLES,
    MIN_TOTAL_SAMPLES,
    RECALL_SAFETY_GATE_MIN,
    PreferenceLearner,
    PreferenceModel,
    extract_features,
    is_exploration_candidate,
    normalize_artist_name,
    tokenize_text,
    validate_rejection_reason,
)
from song_discovery.review_helpers import (
    build_batch_decision_updates,
    classify_candidate_tier,
    filter_candidates_by_pool_and_criteria,
    maybe_train_preference_model_from_db,
    prepare_editor_rows,
    resolve_editor_decisions,
    train_preference_model_from_db,
)


class TestFeedbackAndPreferenceLearner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_feedback.db")
        self.db = DiscoveryDB(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_candidate(
        self,
        source_id: str,
        title: str,
        artist: str,
        score: float = 50.0,
        reasons: list = None,
        platform: str = "qq",
        rel_type: str = "album",
    ) -> int:
        cid, _ = self.db.upsert_candidate(
            platform=platform,
            release_source_id=f"rel_{source_id}",
            track_source_id=f"trk_{source_id}",
            release_title=f"专辑_{title}",
            track_title=title,
            artist_names=artist,
            release_type=rel_type,
            release_date="2026-08-01",
            duration_ms=200000,
            track_number=1,
            release_url="",
            track_url="",
            selection_rule="only_track",
            relevance_score=score,
            relevance_reasons=reasons or ["基准分: 标准音乐发行"],
        )
        return cid

    # -------------------------------------------------------------------------
    # 1. Structured Feedback & SQLite Storage
    # -------------------------------------------------------------------------
    def test_structured_feedback_recording_and_history_compatibility(self):
        """Record structured feedback with reason code, scope, note, snapshot, and verify review_history."""
        cid = self._create_mock_candidate("001", "普通情歌", "流行歌手A", 50.0)

        # 1. Record approved feedback
        ok = self.db.record_feedback(
            candidate_id=cid,
            decision="approved",
            note="很好听的独立流行",
            model_version="v0_cold_start",
        )
        self.assertTrue(ok)

        cand = self.db.get_candidate_by_id(cid)
        self.assertEqual(cand["review_status"], "approved")
        self.assertEqual(cand["review_notes"], "很好听的独立流行")

        # Check review_feedbacks table
        feedbacks = self.db.get_feedbacks(candidate_id=cid)
        self.assertEqual(len(feedbacks), 1)
        fb = feedbacks[0]
        self.assertEqual(fb["decision"], "approved")
        self.assertIsNone(fb["reason_code"])
        self.assertEqual(fb["reason_scope"], "track")
        self.assertEqual(fb["model_version"], "v0_cold_start")
        self.assertEqual(fb["feature_snapshot"]["track_title"], "普通情歌")

        # Check review_history table backward compatibility
        with self.db.get_connection() as conn:
            hist = conn.execute("SELECT * FROM review_history WHERE candidate_id = ?", (cid,)).fetchall()
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0]["new_status"], "approved")

        # 2. Record rejected feedback with structured reason and scope
        self.db.record_feedback(
            candidate_id=cid,
            decision="rejected",
            reason_code=RejectionReason.MAINSTREAM_POP_IDOL.value,
            reason_scope=ReasonScope.ARTIST.value,
            note="纯商业偶像，排除该艺人",
            model_version="v0_cold_start",
        )

        feedbacks_after = self.db.get_feedbacks(candidate_id=cid)
        self.assertEqual(len(feedbacks_after), 2)
        latest_fb = feedbacks_after[0]
        self.assertEqual(latest_fb["decision"], "rejected")
        self.assertEqual(latest_fb["reason_code"], "mainstream_pop_idol")
        self.assertEqual(latest_fb["reason_scope"], "artist")
        stats = self.db.get_feedback_stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["approved"], 0)
        self.assertEqual(stats["rejected"], 1)

    def test_rejected_feedback_requires_valid_reason_and_scope(self):
        cid = self._create_mock_candidate("strict", "普通歌", "普通歌手")
        with self.assertRaises(ValueError):
            self.db.record_feedback(cid, "rejected")
        with self.assertRaises(ValueError):
            self.db.record_feedback(cid, "rejected", reason_code="not_a_reason")
        with self.assertRaises(ValueError):
            self.db.record_feedback(cid, "rejected", reason_code="other", reason_scope="invalid")

    def test_latest_decision_only_counts_toward_activation(self):
        learner = PreferenceLearner(min_total_samples=2, min_pos_samples=1, min_neg_samples=1)
        feedbacks = [
            {"id": 1, "candidate_id": 7, "decision": "approved"},
            {"id": 2, "candidate_id": 7, "decision": "rejected", "reason_code": "other"},
            {"id": 3, "candidate_id": 8, "decision": "approved"},
        ]
        criteria = learner.check_activation_criteria(feedbacks)
        self.assertTrue(criteria["can_activate"])
        self.assertEqual(criteria["total_count"], 2)
        self.assertEqual(criteria["positive_count"], 1)
        self.assertEqual(criteria["negative_count"], 1)

    # -------------------------------------------------------------------------
    # 2. Rejection Reasons Validation & All Mandatory Codes
    # -------------------------------------------------------------------------
    def test_rejection_reasons_contain_all_required_categories(self):
        """Ensure all required structured rejection reasons exist and validate properly."""
        required_codes = [
            "non_rock_band",
            "mainstream_pop_idol",
            "hiphop_rap",
            "virtual_singer",
            "game_bgm_ost",
            "classical_instrumental",
            "cover_accompaniment_remix",
            "artist_not_needed",
            "duplicate",
            "other",
        ]
        for code in required_codes:
            self.assertIn(code, REJECTION_REASON_LABELS)
            self.assertTrue(validate_rejection_reason(code))

        self.assertFalse(validate_rejection_reason("invalid_reason_code"))
        self.assertFalse(validate_rejection_reason(""))
        self.assertFalse(validate_rejection_reason(None))

    # -------------------------------------------------------------------------
    # 3. Human Decision Protection (Never Overwritten by Model)
    # -------------------------------------------------------------------------
    def test_human_decision_protection_never_overwritten(self):
        """Candidates with human decisions (approved, rejected, deferred) are preserved and never overridden."""
        cid1 = self._create_mock_candidate("h1", "摇滚金曲", "万能青年旅店", 85.0)
        cid2 = self._create_mock_candidate("h2", "游戏原声", "某游戏工作室", 30.0)

        # Human approves cid1 and rejects cid2
        self.db.record_feedback(cid1, "approved", note="人工确认通过")
        self.db.record_feedback(cid2, "rejected", reason_code="game_bgm_ost", note="人工确认排除")

        # Mock model re-scoring / filtering
        cand1 = self.db.get_candidate_by_id(cid1)
        cand2 = self.db.get_candidate_by_id(cid2)

        model = PreferenceModel(version_id="test_model", weights={"art:万能青年旅店": -5.0}, is_active=True)
        scored1 = model.score_candidate(cand1)

        # Even if model gave low score, DB review_status remains 'approved'
        self.assertEqual(cand1["review_status"], "approved")
        self.assertEqual(cand2["review_status"], "rejected")

        # Filter preserves candidate review_status
        filtered = filter_candidates_by_pool_and_criteria([cand1, cand2], pool="all", model=model)
        self.assertEqual(filtered[0]["review_status"], "approved")
        self.assertEqual(filtered[1]["review_status"], "rejected")

    # -------------------------------------------------------------------------
    # 4. Cold Start & Activation Thresholds (>=60 total, >=15 pos, >=15 neg)
    # -------------------------------------------------------------------------
    def test_cold_start_and_activation_threshold_reporting(self):
        """Learner reports exact missing counts during cold start and refuses to fake training."""
        learner = PreferenceLearner(min_total_samples=60, min_pos_samples=15, min_neg_samples=15)

        # Empty feedback
        check0 = learner.check_activation_criteria([])
        self.assertFalse(check0["can_activate"])
        self.assertEqual(check0["missing_total"], 60)
        self.assertEqual(check0["missing_positive"], 15)
        self.assertEqual(check0["missing_negative"], 15)
        self.assertIn("冷启动中", check0["message"])

        # Add 10 positive and 5 negative (total 15 < 60)
        feedbacks = []
        for i in range(10):
            feedbacks.append({"decision": "approved", "artist_names": f"乐队_{i}", "track_title": f"歌_{i}"})
        for i in range(5):
            feedbacks.append({"decision": "rejected", "artist_names": f"流行_{i}", "track_title": f"曲_{i}"})

        check1 = learner.check_activation_criteria(feedbacks)
        self.assertFalse(check1["can_activate"])
        self.assertEqual(check1["missing_total"], 45)
        self.assertEqual(check1["missing_positive"], 5)
        self.assertEqual(check1["missing_negative"], 10)

        # Attempt to train with insufficient data returns cold model and failure report
        model, report = learner.train_model(feedbacks)
        self.assertFalse(report["success"])
        self.assertEqual(report["reason"], "insufficient_samples")
        self.assertTrue(model.is_cold_start)
        auto_result = maybe_train_preference_model_from_db(self.db)
        self.assertFalse(auto_result["attempted"])
        self.assertEqual(auto_result["reason"], "cold_start")

    # -------------------------------------------------------------------------
    # 5. Model Training, Grouped Validation & Feature Extraction
    # -------------------------------------------------------------------------
    def test_model_training_when_threshold_met_with_grouped_cv(self):
        """When >=60 total, >=15 pos, >=15 neg samples exist, model trains with Grouped CV."""
        learner = PreferenceLearner(min_total_samples=60, min_pos_samples=15, min_neg_samples=15)
        feedbacks = []

        # 35 positive (rock/band tracks)
        for i in range(35):
            art_name = f"摇滚乐队_{i % 7}"
            feedbacks.append({
                "candidate_id": i + 1,
                "decision": "approved",
                "artist_names": art_name,
                "track_title": f"摇滚现场单曲 {i} (Live)",
                "release_title": f"摇滚现场专辑 {i % 5}",
                "release_type": "album",
                "platform": "qq",
                "relevance_score": 80.0,
                "relevance_reasons": ["命中乐队特征", "现场特征"],
            })

        # 30 negative (game ost / pop tracks)
        for i in range(30):
            art_name = f"流行歌手_{i % 6}"
            feedbacks.append({
                "candidate_id": 100 + i,
                "decision": "rejected",
                "reason_code": "mainstream_pop_idol" if i % 2 == 0 else "game_bgm_ost",
                "reason_scope": "track",
                "artist_names": art_name,
                "track_title": f"商业电视剧插曲伴奏 {i}",
                "release_title": f"流行单曲集 {i % 4}",
                "release_type": "single",
                "platform": "netease",
                "relevance_score": 50.0,
                "relevance_reasons": ["基准分"],
            })

        check = learner.check_activation_criteria(feedbacks)
        self.assertTrue(check["can_activate"])

        model, report = learner.train_model(feedbacks, version_id="v_test_activated", cv_folds=3)
        self.assertTrue(report["success"])
        self.assertFalse(model.is_cold_start)
        self.assertEqual(model.version_id, "v_test_activated")
        self.assertGreater(len(model.weights), 0)

        # Test score breakdown and explainability
        test_rock_cand = {
            "id": 999,
            "track_title": "新专辑现场 (Live)",
            "artist_names": "摇滚乐队_1",
            "release_title": "摇滚现场专辑 1",
            "release_type": "album",
            "platform": "qq",
            "relevance_score": 75.0,
            "relevance_reasons": ["命中乐队特征"],
        }
        scored = model.score_candidate(test_rock_cand)
        self.assertEqual(scored.pool, "primary")
        self.assertGreaterEqual(scored.personalized_score, 70.0)
        self.assertTrue(any("艺人偏好" in exp or "现场" in exp for exp in scored.explanations))

    # -------------------------------------------------------------------------
    # 6. Recall Safety Gate (>=95% Approved Recall Constraint)
    # -------------------------------------------------------------------------
    def test_recall_safety_gate_enforcement(self):
        """Model evaluator verifies whether >=95% recall on approved samples is satisfied."""
        learner = PreferenceLearner(recall_gate_min=0.95)

        # Create model that recognizes rock well
        model = PreferenceModel(
            version_id="gate_test_v1",
            weights={"art:痛仰乐队": 2.0, "art:刺猬": 2.0, "tok:kw_rock": 1.5},
            is_active=True,
        )

        test_data = [
            {"decision": "approved", "artist_names": "痛仰乐队", "track_title": "公路之歌", "relevance_score": 80.0},
            {"decision": "approved", "artist_names": "刺猬", "track_title": "火车", "relevance_score": 80.0},
            {"decision": "rejected", "artist_names": "未知POP", "track_title": "白噪音助眠", "relevance_score": 10.0},
        ]

        metrics = learner.evaluate_model(model, test_data)
        self.assertGreaterEqual(metrics["recall"], 0.95)
        self.assertTrue(metrics["recall_gate_passed"])

    # -------------------------------------------------------------------------
    # 7. No Global Ban from a Single Rejection (Anti-Overkill)
    # -------------------------------------------------------------------------
    def test_no_global_ban_from_single_rejection(self):
        """Rejecting a single track of an artist does not globally blackball the artist."""
        learner = PreferenceLearner(min_total_samples=60, min_pos_samples=15, min_neg_samples=15)
        feedbacks = []

        # 30 approved songs
        for i in range(30):
            feedbacks.append({
                "decision": "approved", "artist_names": f"乐队_{i}", "track_title": f"摇滚曲_{i}",
                "relevance_score": 70.0, "platform": "qq", "release_type": "album",
            })
        # 30 rejected songs, one of which is by "万能青年旅店" (track scope reject)
        for i in range(29):
            feedbacks.append({
                "decision": "rejected", "artist_names": f"流行_{i}", "track_title": f"商业曲_{i}",
                "reason_code": "mainstream_pop_idol", "reason_scope": "track",
                "relevance_score": 50.0, "platform": "netease", "release_type": "single",
            })
        feedbacks.append({
            "decision": "rejected", "artist_names": "万能青年旅店", "track_title": "不插电翻唱小品",
            "reason_code": "cover_accompaniment_remix", "reason_scope": "track",
            "relevance_score": 50.0, "platform": "qq", "release_type": "single",
        })

        model, _ = learner.train_model(feedbacks)
        # Test a real rock track by "万能青年旅店"
        new_track = {
            "id": 888,
            "artist_names": "万能青年旅店",
            "track_title": "十万嬉皮 摇滚现场 (Live)",
            "release_title": "万能青年旅店同名专辑",
            "release_type": "album",
            "platform": "qq",
            "relevance_score": 85.0,
            "relevance_reasons": ["命中乐队特征", "现场特征"],
        }
        scored = model.score_candidate(new_track)
        # Should NOT be dropped to machine_filtered (remains in primary or uncertain)
        self.assertIn(scored.pool, ("primary", "uncertain"))
        self.assertGreater(scored.personalized_score, 50.0)

    # -------------------------------------------------------------------------
    # 8. Deterministic Exploration Sampling (Anti-Narrowing)
    # -------------------------------------------------------------------------
    def test_exploration_sampling_deterministic_and_bounded(self):
        """Exploration sampling is stable and produces expected proportion."""
        sample_ids = list(range(1, 1001))
        exp_count = sum(1 for cid in sample_ids if is_exploration_candidate(cid, rate=0.10))

        # Expected ~10% (between 7% and 13%)
        self.assertGreaterEqual(exp_count, 70)
        self.assertLessEqual(exp_count, 130)

        # Idempotent
        self.assertEqual(is_exploration_candidate(42, 0.10), is_exploration_candidate(42, 0.10))

    # -------------------------------------------------------------------------
    # 9. 3-Pool Classification & Table Operations
    # -------------------------------------------------------------------------
    def test_three_pool_classification_and_data_editor_resolution(self):
        """Prepare single-selection editor rows and build batch decisions with structured rejection reasons."""
        cands = [
            {"id": 1, "track_title": "公路之歌", "artist_names": "痛仰乐队", "relevance_score": 85.0, "review_status": "pending", "platform": "qq"},
            {"id": 2, "track_title": "口水歌", "artist_names": "网络歌手", "relevance_score": 50.0, "review_status": "pending", "platform": "netease"},
        ]

        editor_rows = prepare_editor_rows(cands, selected_ids=[2])
        self.assertEqual(len(editor_rows), 2)
        self.assertIn("选择", editor_rows[0])
        self.assertFalse(editor_rows[0]["选择"])
        self.assertTrue(editor_rows[1]["选择"])
        self.assertEqual(editor_rows[0]["当前状态"], "待审核")
        self.assertIn("评分", editor_rows[0])
        self.assertIn("审听池", editor_rows[0])

        # Test batch decision: Reject candidate 2 with structured reason
        rej_updates = build_batch_decision_updates(
            selected_candidate_ids=[2],
            decision="rejected",
            reason_code="mainstream_pop_idol",
            reason_scope="artist",
            notes="非摇滚偶像",
            active_model_version="v1_test",
        )
        self.assertEqual(len(rej_updates), 1)
        self.assertEqual(rej_updates[0]["candidate_id"], 2)
        self.assertEqual(rej_updates[0]["status"], "rejected")
        self.assertEqual(rej_updates[0]["reason_code"], "mainstream_pop_idol")
        self.assertEqual(rej_updates[0]["reason_scope"], "artist")
        self.assertEqual(rej_updates[0]["notes"], "非摇滚偶像")

        # Test batch decision: Reject candidate without specifying reason defaults to other
        empty_rej_updates = build_batch_decision_updates(
            selected_candidate_ids=[2],
            decision="rejected",
            reason_code="",
            active_model_version="v1_test",
        )
        self.assertEqual(len(empty_rej_updates), 1)
        self.assertEqual(empty_rej_updates[0]["candidate_id"], 2)
        self.assertEqual(empty_rej_updates[0]["status"], "rejected")
        self.assertEqual(empty_rej_updates[0]["reason_code"], "other")
        self.assertEqual(empty_rej_updates[0]["reason_scope"], "track")

        # Test batch decision: Approve candidate 1
        app_updates = build_batch_decision_updates(
            selected_candidate_ids=[1],
            decision="approved",
            notes="经典独立摇滚",
            active_model_version="v1_test",
        )
        self.assertEqual(len(app_updates), 1)
        self.assertEqual(app_updates[0]["candidate_id"], 1)
        self.assertEqual(app_updates[0]["status"], "approved")

    # -------------------------------------------------------------------------
    # 10. Model Registry, Version Tracking & Rollback
    # -------------------------------------------------------------------------
    def test_model_version_registry_and_rollback(self):
        """Save multiple model versions and roll back to previous active version."""
        # 1. Save v1
        self.db.save_model_version(
            version_id="v1_alpha",
            weights={"art:乐队A": 1.5},
            metrics={"eval_recall": 0.96, "eval_precision": 0.80},
            sample_stats={"total": 60, "positive": 30, "negative": 30},
            status="active",
        )
        active1 = self.db.get_active_model_version()
        self.assertEqual(active1["version_id"], "v1_alpha")

        # 2. Save v2
        self.db.save_model_version(
            version_id="v2_beta",
            weights={"art:乐队A": 2.0, "tok:kw_rock": 1.2},
            metrics={"eval_recall": 0.98, "eval_precision": 0.85},
            sample_stats={"total": 80, "positive": 40, "negative": 40},
            status="active",
        )
        active2 = self.db.get_active_model_version()
        self.assertEqual(active2["version_id"], "v2_beta")

        # 3. Roll back to v1
        ok = self.db.rollback_model_version("v1_alpha")
        self.assertTrue(ok)
        active_after = self.db.get_active_model_version()
        self.assertEqual(active_after["version_id"], "v1_alpha")

        all_models = self.db.get_all_model_versions()
        self.assertEqual(len(all_models), 2)


if __name__ == "__main__":
    unittest.main()
