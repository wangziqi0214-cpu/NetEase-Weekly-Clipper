"""Explainable single-user preference feedback learner, feature extraction, 3-pool classification, exploration sampling, and offline evaluation."""

import datetime
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from song_discovery.models import (
    REJECTION_REASON_LABELS,
    REJECTION_REASONS,
    REASON_SCOPE_LABELS,
    ReasonScope,
    RejectionReason,
)

# Minimum threshold constants required to enable personalized scoring
MIN_TOTAL_SAMPLES = 60
MIN_POSITIVE_SAMPLES = 15
MIN_NEGATIVE_SAMPLES = 15

# Default exploration sampling rate (10%) to prevent feedback loop narrowing
DEFAULT_EXPLORATION_RATE = 0.10

# High recall safety constraint on approved samples
RECALL_SAFETY_GATE_MIN = 0.95

# Rock & Band lexical cues for feature tokenization
CORE_GENRE_TOKENS = {
    "摇滚", "rock", "朋克", "punk", "金属", "metal", "独立", "indie",
    "民谣", "folk", "后摇", "post-rock", "自赏", "shoegaze", "另类",
    "alternative", "迷幻", "psychedelic", "车库", "garage", "蓝调",
    "blues", "放克", "funk", "硬核", "hardcore", "emo", "ska",
    "grunge", "britpop", "英伦", "噪音", "noise", "synth", "合成器",
    "city pop", "城市流行", "蒸汽波", "vaporwave", "lo-fi", "新浪潮",
    "乐队", "band", "乐团", "组合", "三人组", "四人组", "五人组",
    "live", "现场", "不插电", "unplugged", "吉他", "贝斯", "鼓",
    "demo", "小样", "排练室", "专场", "巡演", "tour",
}

# Negative / Non-target noise tokens
NOISE_TOKENS = {
    "洛天依", "乐正绫", "言和", "初音未来", "vocaloid",
    "hoyo-mix", "第五人格", "游戏原声", "游戏音乐", "soundtrack", "原声带", "ost",
    "symphony", "philharmonic", "orchestra", "concerto", "sonata",
    "交响曲", "协奏曲", "奏鸣曲", "古典乐", "钢琴独奏", "纯音乐",
    "有声书", "广播剧", "相声", "评书", "脱口秀", "郭德纲", "朗读", "有声",
    "asmr", "助眠", "催眠", "冥想", "白噪音", "白噪声", "睡眠", "胎教",
    "儿歌", "睡前故事", "早教", "童谣", "幼教", "伴奏", "伴奏带", "伴奏版",
    "卡拉ok", "减压", "纯音效", "音效", "环境音", "bgm素材", "remix",
}


def validate_rejection_reason(reason_code: Optional[str]) -> bool:
    """Check if a given rejection reason code is valid."""
    if not reason_code:
        return False
    return reason_code in REJECTION_REASON_LABELS


def normalize_artist_name(name: str) -> List[str]:
    """Split and normalize artist strings into individual artist names."""
    if not name:
        return []
    # Split by standard separators: /, &, feat., feat, with, ,, 、
    parts = re.split(r"[/&,、+，]|(?:\bfeat\.?\b)|(?:\bwith\b)", name, flags=re.IGNORECASE)
    cleaned = []
    for p in parts:
        token = p.strip().lower()
        if token and len(token) >= 1:
            cleaned.append(token)
    return cleaned


def tokenize_text(text: str) -> List[str]:
    """Tokenize Chinese and English text into meaningful keywords and n-grams."""
    if not text:
        return []
    lower = text.lower()
    tokens = []

    # 1. Match known genre & noise tokens
    for kw in CORE_GENRE_TOKENS:
        if kw in lower:
            tokens.append(f"kw_{kw}")
    for kw in NOISE_TOKENS:
        if kw in lower:
            tokens.append(f"noise_{kw}")

    # 2. English words (>= 2 chars)
    en_words = re.findall(r"\b[a-zA-Z0-9_-]{2,}\b", lower)
    for w in en_words:
        tokens.append(f"w_{w}")

    # 3. Chinese 2-grams and 3-grams for title semantics
    cn_text = re.sub(r"[^\u4e00-\u9fa5]", "", lower)
    for i in range(len(cn_text) - 1):
        tokens.append(f"bi_{cn_text[i:i+2]}")
    for i in range(len(cn_text) - 2):
        tokens.append(f"tri_{cn_text[i:i+3]}")

    return list(dict.fromkeys(tokens))  # preserve order while deduplicating


def is_exploration_candidate(candidate_id: int, rate: float = DEFAULT_EXPLORATION_RATE, salt: str = "exp_seed_2026") -> bool:
    """
    Deterministic pseudo-random exploration sampling to avoid filter bubble narrowing.
    Uses SHA-256 hash of candidate ID and salt to guarantee consistent assignment.
    """
    if rate <= 0.0:
        return False
    key = f"{salt}:{candidate_id}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    val = int(h[:6], 16) % 10000 / 10000.0
    return val < rate


def extract_features(item: Dict[str, Any]) -> Dict[str, float]:
    """
    Extract sparse feature dict from a candidate or feedback item.
    Features include artist names, title tokens, release type, platform, static scorer cues, base score.
    """
    features: Dict[str, float] = {}

    # 1. Artists
    artist_str = str(item.get("artist_names") or item.get("artist") or "")
    artists = normalize_artist_name(artist_str)
    for art in artists:
        features[f"art:{art}"] = 1.0

    # 2. Track & Release Titles
    track_title = str(item.get("track_title") or item.get("title") or "")
    rel_title = str(item.get("release_title") or item.get("album_title") or "")
    title_text = f"{track_title} {rel_title}"
    tokens = tokenize_text(title_text)
    for t in tokens:
        features[f"tok:{t}"] = 1.0

    # 3. Release Type
    rel_type = str(item.get("release_type") or "other").lower().strip()
    if rel_type:
        features[f"rel_type:{rel_type}"] = 1.0

    # 4. Platform
    platform_name = str(item.get("platform") or "unknown").lower().strip()
    if platform_name:
        features[f"plat:{platform_name}"] = 1.0

    # 5. Static Scorer Reasons & Signals
    reasons = item.get("relevance_reasons", [])
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except Exception:
            reasons = [reasons]
    reasons_str = " ".join(reasons).lower() if isinstance(reasons, list) else str(reasons).lower()

    if "乐队" in reasons_str or "band" in reasons_str:
        features["cue:band"] = 1.0
    if "摇滚" in reasons_str or "rock" in reasons_str:
        features["cue:rock"] = 1.0
    if "现场" in reasons_str or "live" in reasons_str:
        features["cue:live"] = 1.0
    if "合作" in reasons_str:
        features["cue:collab"] = 1.0
    if "排除" in reasons_str or "非音乐" in reasons_str:
        features["cue:hard_excluded"] = 1.0

    # 6. Normalized Base Relevance Score
    base_score = float(item.get("relevance_score") or item.get("base_score") or 50.0)
    features["meta:base_score_norm"] = max(0.0, min(100.0, base_score)) / 100.0

    return features


@dataclass
class ScoredCandidate:
    """Full personalized scoring output for a candidate track."""
    candidate_id: int
    base_score: float
    personalized_score: float
    pool: str  # 'primary', 'uncertain', 'machine_filtered'
    pool_display: str
    model_version: str
    is_exploration: bool = False
    explanations: List[str] = field(default_factory=list)
    feature_contributions: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "base_score": self.base_score,
            "personalized_score": self.personalized_score,
            "pool": self.pool,
            "pool_display": self.pool_display,
            "model_version": self.model_version,
            "is_exploration": self.is_exploration,
            "explanations": self.explanations,
            "feature_contributions": self.feature_contributions,
        }


class PreferenceModel:
    """
    Transparent, explainable additive scoring model based on smoothed log-odds and calibrated priors.
    Guarantees no global artist ban from a single rejection, explicit point breakdowns, and safe bounding.
    """

    def __init__(
        self,
        version_id: str = "v0_cold_start",
        weights: Optional[Dict[str, float]] = None,
        bias: float = 0.0,
        threshold: float = 50.0,
        trained_at: Optional[str] = None,
        sample_stats: Optional[Dict[str, int]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
    ):
        self.version_id = version_id
        self.weights = weights or {}
        self.bias = bias
        self.threshold = threshold
        self.trained_at = trained_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.sample_stats = sample_stats or {"total": 0, "positive": 0, "negative": 0}
        self.metrics = metrics or {}
        self.is_active = is_active

    @property
    def is_cold_start(self) -> bool:
        return (
            self.version_id == "v0_cold_start"
            or self.sample_stats.get("total", 0) < MIN_TOTAL_SAMPLES
            or self.sample_stats.get("positive", 0) < MIN_POSITIVE_SAMPLES
            or self.sample_stats.get("negative", 0) < MIN_NEGATIVE_SAMPLES
        )

    def score_candidate(
        self,
        candidate: Dict[str, Any],
        exploration_rate: float = DEFAULT_EXPLORATION_RATE,
    ) -> ScoredCandidate:
        """
        Compute personalized score, pool assignment, explainable point breakdown, and exploration status.
        """
        cid = int(candidate.get("id") or candidate.get("candidate_id") or 0)
        base_score = float(candidate.get("relevance_score") or 50.0)

        # Handle cold-start or unactivated state
        if self.is_cold_start:
            # Conservative static fallback: use base score
            is_exp = is_exploration_candidate(cid, rate=exploration_rate)
            current_status = str(candidate.get("review_status") or "")
            if current_status == "machine_filtered" and is_exp:
                pool = "uncertain"
                pool_display = "🎲 探索抽样池（从机器排除中复核）"
            elif current_status == "machine_filtered":
                pool = "machine_filtered"
                pool_display = "🚫 机器排除池（可恢复）"
            elif base_score >= 60.0:
                pool = "primary"
                pool_display = "🎯 主审核池 (Tier 1)"
            elif is_exp and base_score >= 40.0:
                pool = "uncertain"
                pool_display = "🎲 探索抽样池 (冷启动)"
            elif base_score >= 45.0:
                pool = "uncertain"
                pool_display = "⚠️ 不确定池 (Tier 2)"
            else:
                pool = "machine_filtered"
                pool_display = "🚫 机器排除池 (可恢复)"

            reasons_list = candidate.get("relevance_reasons", [])
            if isinstance(reasons_list, str):
                try:
                    reasons_list = json.loads(reasons_list)
                except Exception:
                    reasons_list = [reasons_list]
            reasons_summary = "; ".join(reasons_list) if isinstance(reasons_list, list) else str(reasons_list)

            explanations = [
                f"基准机审分: {base_score:.1f}分 (冷启动模式: 个人偏好尚未激活)",
                f"初始判定: {reasons_summary or '标准音乐发行'}",
            ]
            if is_exp:
                explanations.append("包含稳定探索抽样特征 (防止冷启动遗漏)")

            return ScoredCandidate(
                candidate_id=cid,
                base_score=base_score,
                personalized_score=base_score,
                pool=pool,
                pool_display=pool_display,
                model_version=self.version_id,
                is_exploration=is_exp,
                explanations=explanations,
                feature_contributions={},
            )

        # Active Learned Model Scoring
        feats = extract_features(candidate)
        contribs: Dict[str, float] = {}
        delta_score = 0.0

        # Calculate feature contributions with bounded dampening
        for fname, fval in feats.items():
            if fname in self.weights:
                w = self.weights[fname]
                pts = w * fval * 10.0  # Scale log-odds to points
                # Clamp single feature impact to avoid extreme single-shot over-reliance
                pts_clamped = max(-25.0, min(25.0, pts))
                contribs[fname] = pts_clamped
                delta_score += pts_clamped

        # Base relevance weight
        raw_score = base_score + delta_score + (self.bias * 5.0)
        personalized_score = max(0.0, min(100.0, raw_score))

        # Check for exploration candidate
        is_exp = is_exploration_candidate(cid, rate=exploration_rate)

        # Check if candidate has obvious hard noise
        titles_and_artists = f"{candidate.get('track_title', '')} {candidate.get('artist_names', '')}".lower()
        has_obvious_noise = any(nk in titles_and_artists for nk in [
            "洛天依", "言和", "乐正绫", "初音未来", "vocaloid", "hoyo-mix",
            "有声书", "相声", "广播剧", "白噪音", "asmr", "催眠",
        ])

        # Pool determination
        # Threshold default around 58 for primary, 38-58 for uncertain, <38 for machine_filtered
        if has_obvious_noise and personalized_score < 65.0:
            pool = "machine_filtered"
            pool_display = "🚫 机器排除池 (明显非音乐/功能音频)"
        elif personalized_score >= 58.0:
            pool = "primary"
            pool_display = "🎯 主审核池 (偏好推荐)"
        elif is_exp:
            pool = "uncertain"
            pool_display = "🎲 探索样本池 (防窄化抽样)"
        elif personalized_score >= 38.0:
            pool = "uncertain"
            pool_display = "⚠️ 不确定池 (存疑待审)"
        else:
            pool = "machine_filtered"
            pool_display = "🚫 机器排除池 (可恢复)"

        # Generate human-readable point breakdown explanation
        explanations = [f"基准机审分: {base_score:.1f}分"]

        # Sort contributions by absolute magnitude
        sorted_contribs = sorted(contribs.items(), key=lambda x: abs(x[1]), reverse=True)
        for fname, pts in sorted_contribs[:5]:
            if abs(pts) < 0.5:
                continue
            display_name = fname
            if fname.startswith("art:"):
                display_name = f"艺人偏好 '{fname[4:]}'"
            elif fname.startswith("tok:kw_"):
                display_name = f"摇滚流派关键词 '{fname[7:]}'"
            elif fname.startswith("tok:noise_"):
                display_name = f"排除/功能词 '{fname[10:]}'"
            elif fname.startswith("cue:"):
                display_name = f"机审线索 '{fname[4:]}'"
            elif fname.startswith("rel_type:"):
                display_name = f"发行类型 '{fname[9:]}'"
            elif fname.startswith("plat:"):
                display_name = f"来源平台 '{fname[5:]}'"

            sign = "+" if pts >= 0 else ""
            explanations.append(f"{display_name}: {sign}{pts:.1f}分")

        if is_exp:
            explanations.append("命中固定比例稳定探索抽样 (防止偏好收敛窄化)")

        explanations.append(f"综合个性化评分: {personalized_score:.1f}分 (使用模型: {self.version_id})")

        return ScoredCandidate(
            candidate_id=cid,
            base_score=base_score,
            personalized_score=personalized_score,
            pool=pool,
            pool_display=pool_display,
            model_version=self.version_id,
            is_exploration=is_exp,
            explanations=explanations,
            feature_contributions=contribs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "weights": self.weights,
            "bias": self.bias,
            "threshold": self.threshold,
            "trained_at": self.trained_at,
            "sample_stats": self.sample_stats,
            "metrics": self.metrics,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PreferenceModel":
        return cls(
            version_id=data.get("version_id", "v0_cold_start"),
            weights=data.get("weights", {}),
            bias=float(data.get("bias", 0.0)),
            threshold=float(data.get("threshold", 50.0)),
            trained_at=data.get("trained_at"),
            sample_stats=data.get("sample_stats", {}),
            metrics=data.get("metrics", {}),
            is_active=data.get("is_active", True),
        )


class PreferenceLearner:
    """
    Offline trainer, cross-validator, and evaluator for single-user editorial feedback.
    Enforces the >=95% recall safety gate and grouped validation.
    """

    def __init__(
        self,
        min_total_samples: int = MIN_TOTAL_SAMPLES,
        min_pos_samples: int = MIN_POSITIVE_SAMPLES,
        min_neg_samples: int = MIN_NEGATIVE_SAMPLES,
        recall_gate_min: float = RECALL_SAFETY_GATE_MIN,
    ):
        self.min_total_samples = min_total_samples
        self.min_pos_samples = min_pos_samples
        self.min_neg_samples = min_neg_samples
        self.recall_gate_min = recall_gate_min

    def check_activation_criteria(self, feedbacks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Verify whether feedback dataset meets the activation criteria.
        Returns missing sample counts and clear human-readable status.
        """
        labeled = self._latest_human_labels(feedbacks)
        pos_count = sum(1 for f in labeled if str(f.get("decision")).lower() == "approved")
        neg_count = sum(1 for f in labeled if str(f.get("decision")).lower() == "rejected")
        total_count = len(labeled)

        missing_total = max(0, self.min_total_samples - total_count)
        missing_pos = max(0, self.min_pos_samples - pos_count)
        missing_neg = max(0, self.min_neg_samples - neg_count)

        can_activate = (
            total_count >= self.min_total_samples
            and pos_count >= self.min_pos_samples
            and neg_count >= self.min_neg_samples
        )

        if can_activate:
            status_msg = f"✅ 已满足激活门槛 (总反馈: {total_count}, 批准: {pos_count}, 排除: {neg_count})"
        else:
            status_msg = (
                f"❄️ 学习未启用 (冷启动中): 当前样本 {total_count}/{self.min_total_samples} "
                f"(批准: {pos_count}/{self.min_pos_samples}, 排除: {neg_count}/{self.min_neg_samples})。"
                f"还差 {missing_total} 条有效样本 (至少还需 {missing_pos} 条批准、{missing_neg} 条排除)。"
            )

        return {
            "can_activate": can_activate,
            "total_count": total_count,
            "positive_count": pos_count,
            "negative_count": neg_count,
            "min_total": self.min_total_samples,
            "min_positive": self.min_pos_samples,
            "min_negative": self.min_neg_samples,
            "missing_total": missing_total,
            "missing_positive": missing_pos,
            "missing_negative": missing_neg,
            "message": status_msg,
        }

    @staticmethod
    def _latest_human_labels(feedbacks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Use only the latest decision per candidate; non-label states cancel older labels."""
        ordered = list(feedbacks)
        if any(feedback.get("id") is not None for feedback in ordered):
            ordered.sort(key=lambda feedback: int(feedback.get("id") or 0), reverse=True)
        seen: Set[Any] = set()
        labeled: List[Dict[str, Any]] = []
        for index, feedback in enumerate(ordered):
            candidate_id = feedback.get("candidate_id")
            key = ("candidate", candidate_id) if candidate_id is not None else ("row", index)
            if key in seen:
                continue
            seen.add(key)
            if str(feedback.get("decision")).lower() in ("approved", "rejected"):
                labeled.append(feedback)
        return labeled

    def train_model(
        self,
        feedbacks: List[Dict[str, Any]],
        version_id: Optional[str] = None,
        cv_folds: int = 5,
        smoothing_alpha: float = 1.0,
    ) -> Tuple[PreferenceModel, Dict[str, Any]]:
        """
        Train a personalized preference model with grouped cross-validation and recall safety gate.
        """
        criteria = self.check_activation_criteria(feedbacks)
        if not criteria["can_activate"]:
            cold_model = PreferenceModel(
                version_id="v0_cold_start",
                sample_stats={
                    "total": criteria["total_count"],
                    "positive": criteria["positive_count"],
                    "negative": criteria["negative_count"],
                },
                metrics={"status": "cold_start", "message": criteria["message"]},
            )
            return cold_model, {
                "success": False,
                "reason": "insufficient_samples",
                "criteria": criteria,
                "metrics": {},
            }

        v_id = version_id or f"v{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}_{hashlib.sha256(str(len(feedbacks)).encode()).hexdigest()[:6]}"

        labeled_items = self._latest_human_labels(feedbacks)

        # Group items by artist to prevent near-duplicate leakage
        artist_groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in labeled_items:
            art = normalize_artist_name(str(item.get("artist_names") or item.get("artist") or "unknown"))
            key = art[0] if art else "unknown"
            artist_groups.setdefault(key, []).append(item)

        # Cross Validation (Grouped by Artist)
        group_keys = sorted(artist_groups.keys())
        if len(group_keys) < 2:
            return PreferenceModel(), {
                "success": False,
                "reason": "insufficient_artist_groups",
                "criteria": criteria,
                "metrics": {},
            }
        effective_folds = max(2, min(cv_folds, len(group_keys)))
        cv_counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}

        for f_idx in range(effective_folds):
            val_keys = {key for idx, key in enumerate(group_keys) if idx % effective_folds == f_idx}
            train_items = [it for k, items in artist_groups.items() if k not in val_keys for it in items]
            val_items = [it for k, items in artist_groups.items() if k in val_keys for it in items]

            if not train_items or not val_items:
                continue

            # Train on fold
            w_fold, b_fold = self._fit_weights(train_items, smoothing_alpha=smoothing_alpha)
            fold_model = PreferenceModel(version_id=f"cv_{f_idx}", weights=w_fold, bias=b_fold, sample_stats={"total": len(train_items)})

            # Eval on val fold
            m_fold = self.evaluate_model(fold_model, val_items)
            for key in cv_counts:
                cv_counts[key] += int(m_fold.get(key, 0))

        tp, fp, fn = cv_counts["tp"], cv_counts["fp"], cv_counts["fn"]
        avg_recall = tp / (tp + fn) if (tp + fn) else 0.0
        avg_precision = tp / (tp + fp) if (tp + fp) else 0.0
        avg_f1 = (2 * avg_precision * avg_recall / (avg_precision + avg_recall)) if (avg_precision + avg_recall) else 0.0

        # Safety Gate Check: Recall must be >= 0.95
        recall_gate_passed = (avg_recall >= self.recall_gate_min)

        # Train final model on all labeled data
        final_weights, final_bias = self._fit_weights(labeled_items, smoothing_alpha=smoothing_alpha)
        pos_cnt = sum(1 for it in labeled_items if it.get("decision") == "approved")
        neg_cnt = sum(1 for it in labeled_items if it.get("decision") == "rejected")

        metrics_summary = {
            "cv_folds": effective_folds,
            "grouped_by": "artist",
            "eval_recall": round(avg_recall, 4),
            "eval_precision": round(avg_precision, 4),
            "eval_f1": round(avg_f1, 4),
            "recall_gate_min": self.recall_gate_min,
            "recall_gate_passed": recall_gate_passed,
            "total_samples": len(labeled_items),
            "positive_samples": pos_cnt,
            "negative_samples": neg_cnt,
            "learned_features_count": len(final_weights),
        }

        model = PreferenceModel(
            version_id=v_id,
            weights=final_weights,
            bias=final_bias,
            sample_stats={"total": len(labeled_items), "positive": pos_cnt, "negative": neg_cnt},
            metrics=metrics_summary,
            is_active=recall_gate_passed,
        )

        return model, {
            "success": True,
            "version_id": v_id,
            "criteria": criteria,
            "metrics": metrics_summary,
            "recall_gate_passed": recall_gate_passed,
        }

    def _fit_weights(
        self,
        items: List[Dict[str, Any]],
        smoothing_alpha: float = 1.0,
    ) -> Tuple[Dict[str, float], float]:
        """
        Fit smoothed log-odds feature weights from labeled feedback items.
        Applies reason-scope damping so single-track rejects don't ban entire artists.
        """
        pos_items = [it for it in items if str(it.get("decision")).lower() == "approved"]
        neg_items = [it for it in items if str(it.get("decision")).lower() == "rejected"]

        n_pos = max(1, len(pos_items))
        n_neg = max(1, len(neg_items))

        # Prior log odds
        prior_log_odds = math.log((n_pos + smoothing_alpha) / (n_neg + smoothing_alpha))

        feature_pos_counts: Dict[str, float] = {}
        feature_neg_counts: Dict[str, float] = {}

        for it in pos_items:
            feats = extract_features(it)
            for f in feats:
                feature_pos_counts[f] = feature_pos_counts.get(f, 0.0) + 1.0

        for it in neg_items:
            feats = extract_features(it)
            scope = str(it.get("reason_scope") or "track").lower()
            reason = str(it.get("reason_code") or "other").lower()
            # If rejected with scope='track', artist feature penalty is dampened to prevent global ban
            for f in feats:
                weight_factor = 1.0
                if f.startswith("art:") and scope == "track":
                    weight_factor = 0.5  # Soften artist penalty for track-specific veto
                if reason == "duplicate":
                    # Duplicate is an item-level housekeeping decision, not a taste signal.
                    weight_factor = 0.05
                elif reason == "artist_not_needed":
                    weight_factor = 1.25 if f.startswith("art:") else 0.25
                elif reason in {
                    "non_rock_band", "mainstream_pop_idol", "hiphop_rap",
                    "virtual_singer", "game_bgm_ost", "classical_instrumental",
                    "cover_accompaniment_remix",
                } and not f.startswith("art:"):
                    weight_factor *= 1.2
                feature_neg_counts[f] = feature_neg_counts.get(f, 0.0) + weight_factor

        all_feats = set(feature_pos_counts.keys()).union(set(feature_neg_counts.keys()))
        weights: Dict[str, float] = {}

        for f in all_feats:
            pos_c = feature_pos_counts.get(f, 0.0)
            neg_c = feature_neg_counts.get(f, 0.0)

            # Laplace / Dirichlet smoothed likelihoods
            p_pos = (pos_c + smoothing_alpha) / (n_pos + 2.0 * smoothing_alpha)
            p_neg = (neg_c + smoothing_alpha) / (n_neg + 2.0 * smoothing_alpha)

            log_odds = math.log(p_pos / p_neg)

            # Regularize: damp small sample noise
            total_count = pos_c + neg_c
            shrinkage = total_count / (total_count + 2.0)
            reg_weight = log_odds * shrinkage

            if abs(reg_weight) >= 0.05:
                weights[f] = round(reg_weight, 4)

        return weights, round(prior_log_odds, 4)

    def evaluate_model(
        self,
        model: PreferenceModel,
        test_items: List[Dict[str, Any]],
        group_by: str = "artist",
    ) -> Dict[str, Any]:
        """
        Evaluate a model on a test feedback set and calculate precision, recall, and safety gate.
        """
        labeled = [it for it in test_items if str(it.get("decision")).lower() in ("approved", "rejected")]
        if not labeled:
            return {"recall": 1.0, "precision": 1.0, "f1": 1.0, "accuracy": 1.0, "total": 0}

        tp = 0
        fp = 0
        tn = 0
        fn = 0

        for it in labeled:
            is_pos = (it.get("decision") == "approved")
            res = model.score_candidate(it, exploration_rate=0.0)  # No random exploration in eval
            # Approved in Primary or Uncertain pool is considered positive/retained candidate
            pred_pos = (res.pool in ("primary", "uncertain"))

            if is_pos and pred_pos:
                tp += 1
            elif not is_pos and pred_pos:
                fp += 1
            elif not is_pos and not pred_pos:
                tn += 1
            elif is_pos and not pred_pos:
                fn += 1

        total_pos = tp + fn
        total_neg = tn + fp

        recall = tp / total_pos if total_pos > 0 else 1.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        accuracy = (tp + tn) / len(labeled) if len(labeled) > 0 else 1.0
        fp_reduction = tn / total_neg if total_neg > 0 else 0.0

        return {
            "total_tested": len(labeled),
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "accuracy": round(accuracy, 4),
            "false_positive_reduction": round(fp_reduction, 4),
            "recall_gate_passed": (recall >= self.recall_gate_min),
        }

    def compare_models(
        self,
        old_model: Optional[PreferenceModel],
        new_model: PreferenceModel,
        test_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate a side-by-side comparison report between an old model and a new candidate model.
        """
        new_metrics = self.evaluate_model(new_model, test_items)
        old_metrics = self.evaluate_model(old_model, test_items) if old_model else {
            "recall": 1.0, "precision": 0.5, "f1": 0.6667, "accuracy": 0.5, "false_positive_reduction": 0.0
        }

        delta_recall = new_metrics["recall"] - old_metrics["recall"]
        delta_precision = new_metrics["precision"] - old_metrics["precision"]
        delta_f1 = new_metrics["f1"] - old_metrics["f1"]

        return {
            "old_version": old_model.version_id if old_model else "none",
            "new_version": new_model.version_id,
            "old_metrics": old_metrics,
            "new_metrics": new_metrics,
            "delta": {
                "recall": round(delta_recall, 4),
                "precision": round(delta_precision, 4),
                "f1": round(delta_f1, 4),
            },
            "recommendation": "accept" if new_metrics["recall_gate_passed"] and delta_precision >= -0.05 else "reject",
        }
