"""Helper functions for Streamlit human review UI, tiered machine filtering, data editor, preference learning, and 3-platform status aggregation."""

import datetime
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

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
    PreferenceLearner,
    PreferenceModel,
    ScoredCandidate,
    validate_rejection_reason,
)

logger = logging.getLogger("song_discovery.review_helpers")

# Keywords that indicate rock / indie / band / folk / collaborative / live musical traits
BAND_ROCK_KEYWORDS = [
    "乐队", "乐团", "乐社", "trio", "quartet", "band", "group", "duo",
    "rock", "indie", "punk", "metal", "jazz", "folk", "emo", "synth",
    "摇滚", "独立", "民谣", "朋克", "金属", "爵士", "现场", "乐器", "巡演",
]

# Strong, explainable noise signals observed in the current discovery database.
# They override generic collaboration/band-name bonuses, but never delete rows.
OBVIOUS_NOISE_KEYWORDS = [
    "洛天依", "乐正绫", "言和", "初音未来", "vocaloid",
    "hoyo-mix", "第五人格", "游戏原声", "游戏音乐", "soundtrack", "原声带",
    "symphony", "philharmonic", "orchestra", "concerto", "sonata",
    "交响曲", "协奏曲", "奏鸣曲", "古典乐", "钢琴独奏",
]

PLATFORM_DISPLAY_NAMES = {
    "netease": "网易云",
    "kkbox": "KKBOX",
    "qq": "QQ音乐",
}
PLATFORM_REPRESENTATIVE_PRIORITY = {"netease": 0, "kkbox": 1, "qq": 2}
PLACEHOLDER_ARTIST_KEYS = {
    "kkboxartist",
    "unknownartist",
    "未知歌手",
    "未知艺人",
}


def is_incomplete_kkbox_candidate(candidate: Dict[str, Any]) -> bool:
    """Return True for legacy KKBOX rows that lack reviewable metadata."""
    if str(candidate.get("platform") or "").lower() != "kkbox":
        return False
    artist_key = _canonical_token(candidate.get("artist_names"))
    release_key = _canonical_token(candidate.get("release_title"))
    return artist_key in PLACEHOLDER_ARTIST_KEYS or release_key in {
        "kkboxwebplayer", "kkbox", "letsmusickkbox",
    }

_FEAT_PATTERN = re.compile(
    r"[\(\[（【]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]）】]+)[\)\]）】]",
    re.IGNORECASE,
)
_ARTIST_SPLIT_PATTERN = re.compile(
    r"\s*(?:/|&|、|,|，|\+|;|；|\||\s+(?:feat\.?|ft\.?|featuring|with|x|与|和)\s+)\s*",
    re.IGNORECASE,
)
_OFFICIAL_MEDIA_PATTERN = re.compile(
    r"[\(\[（【]\s*official\s+(?:music\s+)?(?:video|audio|mv)\s*[\)\]）】]",
    re.IGNORECASE,
)


def _canonical_token(value: Any) -> str:
    """Unicode-aware token normalization that keeps non-Latin song titles."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


def _canonical_artists(value: Any) -> List[str]:
    parts = _ARTIST_SPLIT_PATTERN.split(str(value or ""))
    return sorted({token for token in (_canonical_token(part) for part in parts) if token})


def get_song_dedup_key(candidate: Dict[str, Any]) -> str:
    """
    Build a conservative cross-platform song identity.

    Live/remix/acoustic/instrumental markers are deliberately retained so
    different recordings are not collapsed merely because their base title is
    similar. Only generic Official Video/Audio wrappers and feat placement are
    normalized.
    """
    raw_title = unicodedata.normalize("NFKC", str(candidate.get("track_title") or "")).strip()
    featured: List[str] = []

    def remove_feat(match: re.Match) -> str:
        featured.extend(_ARTIST_SPLIT_PATTERN.split(match.group(1)))
        return ""

    base_title = _FEAT_PATTERN.sub(remove_feat, raw_title)
    base_title = _OFFICIAL_MEDIA_PATTERN.sub("", base_title)
    title_key = _canonical_token(base_title)
    artist_tokens = set(_canonical_artists(candidate.get("artist_names")))
    artist_tokens.update(_canonical_artists(" / ".join(featured)))

    # Never merge rows whose identity is missing. KKBOX can temporarily emit a
    # placeholder artist while extraction is incomplete; isolate those by ID.
    if not title_key or not artist_tokens or artist_tokens.issubset(PLACEHOLDER_ARTIST_KEYS):
        return f"raw:{candidate.get('platform')}:{candidate.get('id')}"
    return f"{title_key}::{'|'.join(sorted(artist_tokens))}"


def _metadata_completeness(candidate: Dict[str, Any]) -> int:
    return sum(
        [
            2 if candidate.get("duration_ms") else 0,
            2 if candidate.get("release_date") else 0,
            1 if candidate.get("release_title") else 0,
            1 if candidate.get("track_url") else 0,
            1 if candidate.get("release_url") else 0,
        ]
    )


def deduplicate_candidates(
    candidates: List[Dict[str, Any]],
    all_candidates: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Deduplicate the review view without deleting raw platform records.

    `candidates` controls which representatives are visible after filtering;
    `all_candidates` supplies the complete duplicate membership so one human
    decision can be propagated to every platform copy.
    """
    if not candidates:
        return []

    full_groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in all_candidates or candidates:
        full_groups.setdefault(get_song_dedup_key(item), []).append(item)

    visible_groups: Dict[str, List[Dict[str, Any]]] = {}
    visible_order: List[str] = []
    for item in candidates:
        key = get_song_dedup_key(item)
        if key not in visible_groups:
            visible_groups[key] = []
            visible_order.append(key)
        visible_groups[key].append(item)

    results: List[Dict[str, Any]] = []
    for key in visible_order:
        visible_group = visible_groups[key]
        representative = min(
            visible_group,
            key=lambda item: (
                PLATFORM_REPRESENTATIVE_PRIORITY.get(str(item.get("platform") or "").lower(), 9),
                -_metadata_completeness(item),
                int(item.get("id") or 0),
            ),
        )
        full_group = full_groups.get(key, visible_group)
        merged = dict(representative)
        platforms = sorted(
            {str(item.get("platform") or "").lower() for item in full_group if item.get("platform")},
            key=lambda value: PLATFORM_REPRESENTATIVE_PRIORITY.get(value, 9),
        )
        raw_ids = sorted({int(item["id"]) for item in full_group if item.get("id") is not None})
        source_urls: Dict[str, str] = {}
        for item in full_group:
            platform_name = str(item.get("platform") or "").lower()
            url = item.get("track_url") or item.get("release_url")
            if platform_name and url and platform_name not in source_urls:
                source_urls[platform_name] = str(url)

        merged.update(
            {
                "canonical_key": key,
                "raw_ids": raw_ids,
                "dedup_count": len(raw_ids),
                "source_platforms": platforms,
                "platforms_display": " / ".join(
                    PLATFORM_DISPLAY_NAMES.get(value, value.upper()) for value in platforms
                ),
                "source_urls": source_urls,
            }
        )
        results.append(merged)
    return results


def extract_selected_row_indices(selection: Any, total_rows: int = 0) -> List[int]:
    """Extract row indices from Streamlit multi-row and multi-cell selections."""
    if selection is None:
        return []
    if hasattr(selection, "selection"):
        selection = selection.selection
    elif isinstance(selection, dict) and "selection" in selection:
        selection = selection.get("selection")
    if selection is None:
        return []

    def read_field(name: str, default: Any) -> Any:
        if isinstance(selection, dict):
            return selection.get(name, default)
        return getattr(selection, name, default)

    indices: Set[int] = set()
    for row in read_field("rows", []) or []:
        try:
            indices.add(int(row))
        except (TypeError, ValueError):
            pass

    for cell in read_field("cells", []) or []:
        row = None
        if isinstance(cell, dict):
            row = cell.get("row", cell.get("rowIndex"))
        elif isinstance(cell, (list, tuple)) and cell:
            row = cell[0]
        else:
            row = getattr(cell, "row", getattr(cell, "rowIndex", None))
        try:
            indices.add(int(row))
        except (TypeError, ValueError):
            pass

    return sorted(index for index in indices if index >= 0 and (not total_rows or index < total_rows))


def get_active_preference_model(db: DiscoveryDB) -> PreferenceModel:
    """Retrieve active preference model from DB registry or return cold-start model."""
    active_row = db.get_active_model_version()
    if active_row:
        return PreferenceModel(
            version_id=active_row.get("version_id", "v0_cold_start"),
            weights=active_row.get("weights", {}),
            bias=float(active_row.get("metrics", {}).get("bias", 0.0) if isinstance(active_row.get("metrics"), dict) else 0.0),
            sample_stats={
                "total": active_row.get("sample_count", 0),
                "positive": active_row.get("positive_count", 0),
                "negative": active_row.get("negative_count", 0),
            },
            metrics=active_row.get("metrics", {}),
            is_active=True,
        )

    # Check feedback stats for sample counts
    fb_stats = db.get_feedback_stats()
    return PreferenceModel(
        version_id="v0_cold_start",
        weights={},
        bias=0.0,
        sample_stats={
            "total": fb_stats.get("total", 0),
            "positive": fb_stats.get("approved", 0),
            "negative": fb_stats.get("rejected", 0),
        },
        metrics={"status": "cold_start"},
        is_active=False,
    )


def classify_candidate_tier(
    candidate: Dict[str, Any],
    model: Optional[PreferenceModel] = None,
) -> Tuple[str, str, bool]:
    """
    Data-driven tiered machine filtering and personalized pool routing for candidate tracks.
    Returns:
      (tier_code, tier_label, is_band_or_rock)
      - tier_code: 'tier1' (Primary Review Pool), 'tier2' (Uncertain Pool), or 'machine_filtered'
      - tier_label: Display string with icon
      - is_band_or_rock: bool flag indicating high band/rock affinity
    """
    # 1. Check reasons list
    reasons = candidate.get("relevance_reasons", [])
    if isinstance(reasons, str) and reasons.startswith("["):
        try:
            reasons_list = json.loads(reasons)
        except Exception:
            reasons_list = [reasons]
    elif isinstance(reasons, list):
        reasons_list = reasons
    else:
        reasons_list = [str(reasons)] if reasons else []

    reasons_str = " ".join(reasons_list).lower()
    has_rock_reason = any(kw in reasons_str for kw in BAND_ROCK_KEYWORDS)

    # 2. Check artist names
    artists = str(candidate.get("artist_names") or "").lower()
    has_band_artist = any(b_kw in artists for b_kw in [
        "乐队", "乐团", "乐社", "trio", "quartet", "band", "group", "duo",
    ])

    # 3. Check track and release titles
    titles = f"{candidate.get('track_title', '')} {candidate.get('release_title', '')}".lower()
    has_rock_title = any(t_kw in titles for t_kw in ["(live", "[live", "demo", "remix", "band", "rock", "ep"])

    is_band_or_rock = (has_rock_reason or has_band_artist or has_rock_title)

    combined = f"{artists} {titles} {reasons_str}"
    is_obvious_noise = any(keyword in combined for keyword in OBVIOUS_NOISE_KEYWORDS)

    if is_obvious_noise and not has_rock_title:
        return "tier2", "⚠️ 机器排除（明显非目标）", False

    # If active personalized model is provided and not cold start, use model's pool
    if model and not model.is_cold_start:
        scored = model.score_candidate(candidate)
        if scored.pool == "primary":
            return "tier1", scored.pool_display, is_band_or_rock
        elif scored.pool == "uncertain":
            return "tier2", scored.pool_display, is_band_or_rock
        else:
            return "tier2", scored.pool_display, False

    # Cold start baseline rule
    score = float(candidate.get("relevance_score") or 0.0)
    if score >= 60.0 or is_band_or_rock:
        return "tier1", "🎯 主审核池 (Tier 1)", is_band_or_rock
    else:
        return "tier2", "⚠️ 可能误报 (Tier 2)", False


def initial_review_status_for_candidate(
    candidate: Dict[str, Any],
    model: Optional[PreferenceModel] = None,
) -> str:
    """Route newly discovered candidates into human review (pending) or recoverable machine-filtered pool."""
    if model and not model.is_cold_start:
        scored = model.score_candidate(candidate)
        return "machine_filtered" if scored.pool == "machine_filtered" else "pending"
    tier_code, _, _ = classify_candidate_tier(candidate, model=model)
    return "pending" if tier_code == "tier1" else "machine_filtered"


def apply_machine_filter_to_legacy_pending(db: DiscoveryDB) -> int:
    """
    Classify legacy, never-reviewed pending rows created before tier routing existed.
    Manually reset rows have reviewed_at set and are deliberately left pending.
    """
    pending = db.get_candidates(status="pending", limit=100000)
    updated = 0
    for candidate in pending:
        if candidate.get("reviewed_at"):
            continue
        if initial_review_status_for_candidate(candidate) == "machine_filtered":
            if db.set_machine_filtered(
                candidate["id"], "机器初筛：低相关候选，完整保留可恢复"
            ):
                updated += 1
    return updated


def filter_candidates_by_pool_and_criteria(
    candidates: List[Dict[str, Any]],
    pool: str = "tier1",
    only_band_rock: bool = False,
    platform: Optional[str] = None,
    min_score: float = 0.0,
    search_keyword: str = "",
    model: Optional[PreferenceModel] = None,
) -> List[Dict[str, Any]]:
    """
    Filter candidate records based on pool, quick toggles, and metadata criteria.
    Never deletes or drops items from DB. Preserves human status unconditionally.
    """
    results = []
    kw = search_keyword.strip().lower()

    for cand in candidates:
        tier_code, _, is_band_rock = classify_candidate_tier(cand, model=model)
        # 1. Pool filtering. Preserve legacy tier1/tier2 behavior when no
        # preference model is supplied; model-aware callers get true 3-pool routing.
        if model:
            scored_pool = model.score_candidate(cand).pool
            if pool in ("tier1", "primary") and scored_pool != "primary":
                continue
            elif pool in ("tier2", "uncertain") and scored_pool != "uncertain":
                continue
            elif pool == "machine_filtered" and scored_pool != "machine_filtered":
                continue
        else:
            if pool in ("tier1", "primary") and tier_code != "tier1":
                continue
            elif pool in ("tier2", "uncertain") and tier_code != "tier2":
                continue
            elif pool == "machine_filtered" and cand.get("review_status") != "machine_filtered":
                continue

        # 2. Quick toggle: Band / Rock only
        if only_band_rock and not is_band_rock:
            continue

        # 3. Platform filter
        if platform and platform != "all" and cand.get("platform") != platform:
            continue

        # 4. Score filter
        score = float(cand.get("relevance_score") or 0.0)
        if score < min_score:
            continue

        # 5. Keyword search
        if kw:
            t_str = f"{cand.get('track_title', '')} {cand.get('artist_names', '')} {cand.get('release_title', '')}".lower()
            if kw not in t_str:
                continue

        results.append(cand)

    return results


REVIEW_STATUS_DISPLAY_MAP: Dict[str, str] = {
    "pending": "待审核",
    "approved": "✅ 已通过",
    "rejected": "🚫 已排除",
    "deferred": "⚠️ 存疑",
    "machine_filtered": "机器排除",
}


def prepare_editor_rows(
    candidates: List[Dict[str, Any]],
    model: Optional[PreferenceModel] = None,
    selected_ids: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Convert candidate dictionaries into clean, single-selection st.data_editor table rows with
    one leftmost '选择' checkbox column, current status, rejection reason/scope/notes, and personalized scores.
    """
    active_model = model or PreferenceModel()
    selected_set = set(selected_ids or [])
    rows = []

    for cand in candidates:
        cid = cand["id"]
        canonical_key = cand.get("canonical_key") or get_song_dedup_key(cand)
        status = cand.get("review_status", "pending")
        tier_code, tier_label, is_band_rock = classify_candidate_tier(cand, model=active_model)
        scored = active_model.score_candidate(cand)

        reasons = cand.get("relevance_reasons", [])
        if isinstance(reasons, list):
            reasons_summary = "; ".join(reasons)
        else:
            reasons_summary = str(reasons)

        current_reason_code = cand.get("reason_code") or ""
        current_reason_scope = cand.get("reason_scope") or "track"
        current_notes = cand.get("review_notes") or ""

        # Format rejection reason display
        reason_display = REJECTION_REASON_LABELS.get(current_reason_code, current_reason_code if status == "rejected" else "")
        scope_display = REASON_SCOPE_LABELS.get(current_reason_scope, REASON_SCOPE_LABELS.get("track", "当前单曲 (Track)")) if status == "rejected" else ""
        status_display = REVIEW_STATUS_DISPLAY_MAP.get(status, status)

        rows.append({
            "id": cid,
            "选择": (cid in selected_set) or bool(cand.get("selected", False)),
            "批选": "✅" if canonical_key in selected_set else "⬜",
            "当前状态": status_display,
            "否决原因": reason_display,
            "否决范围": scope_display,
            "备注": current_notes,
            "平台": cand.get("platforms_display") or str(cand.get("platform", "")).upper(),
            "歌曲": cand.get("track_title", ""),
            "艺人": cand.get("artist_names", ""),
            "发行专辑/单曲": cand.get("release_title", ""),
            "发行日期": cand.get("release_date", "未知"),
            "类型": cand.get("release_type", "album"),
            "音轨": cand.get("track_number", 1),
            "评分": round(scored.personalized_score, 1),
            "审听池": scored.pool_display,
            "判定依据与解释": "; ".join(scored.explanations),
            "模型版本": scored.model_version,
            "链接": cand.get("track_url") or cand.get("release_url") or "",
            "_canonical_key": canonical_key,
            "_raw_ids": list(cand.get("raw_ids") or [cid]),
            "_original_status": status,
            "_original_reason": current_reason_code,
            "_original_scope": current_reason_scope,
            "_original_notes": current_notes,
        })
    return rows


def build_batch_decision_updates(
    selected_candidate_ids: List[int],
    decision: str,
    reason_code: Optional[str] = None,
    reason_scope: str = "track",
    notes: str = "",
    active_model_version: str = "v0_cold_start",
) -> List[Dict[str, Any]]:
    """
    Build standardized feedback updates for multiple selected candidate IDs.
    When decision is 'rejected' and reason is empty, automatically falls back to 'other'
    (泛化不喜欢 / 不符合选歌偏好) and scope='track'. Rejects invalid non-empty reasons.
    """
    valid_decisions = {"approved", "rejected", "deferred", "pending", "machine_filtered"}
    dec = str(decision).lower().strip()
    if dec not in valid_decisions:
        raise ValueError(f"无效的审核决定: {decision}。必须是 {valid_decisions} 之一。")

    if dec == "rejected":
        raw_reason = str(reason_code or "").strip()
        if not raw_reason:
            actual_reason = "other"
            actual_scope = "track"
        else:
            reason_label_to_code = {label: code for code, label in REJECTION_REASON_LABELS.items()}
            code = reason_label_to_code.get(raw_reason, raw_reason)
            if code not in REJECTION_REASON_LABELS:
                raise ValueError(f"无效的否决原因: '{reason_code}'。必须是有效的原因代码或标签。")
            actual_reason = code
            scope_label_to_code = {label: code for code, label in REASON_SCOPE_LABELS.items()}
            actual_scope = scope_label_to_code.get(reason_scope, reason_scope if reason_scope in REASON_SCOPE_LABELS else "track")
    else:
        actual_reason = None
        actual_scope = "track"

    updates = []
    for cid in selected_candidate_ids:
        updates.append({
            "candidate_id": int(cid),
            "status": dec,
            "decision": dec,
            "reason_code": actual_reason,
            "reason_scope": actual_scope,
            "notes": str(notes or "").strip(),
            "note": str(notes or "").strip(),
            "model_version": active_model_version,
        })
    return updates


def resolve_editor_decisions(
    edited_rows: List[Dict[str, Any]],
    original_rows: List[Dict[str, Any]],
    active_model_version: str = "v0_cold_start",
    default_reject_reason: str = "",
) -> List[Dict[str, Any]]:
    """
    Compare edited rows against original rows and resolve table changes into feedback updates for SQLite.
    Supports both single-selection rows and legacy multi-checkbox rows for backward compatibility.
    """
    updates = []
    orig_map = {r["id"]: r for r in original_rows}
    reason_label_to_code = {label: code for code, label in REJECTION_REASON_LABELS.items()}
    scope_label_to_code = {label: code for code, label in REASON_SCOPE_LABELS.items()}

    for row in edited_rows:
        cid = row["id"]
        orig = orig_map.get(cid)
        if not orig:
            continue

        # Check for legacy 3-column format
        if "通过" in row and "排除" in row and "存疑" in row:
            c_app = bool(row.get("通过", False))
            c_rej = bool(row.get("排除", False))
            c_def = bool(row.get("存疑", False))
            raw_reason = str(row.get("否决原因") or "").strip()
            raw_scope = str(row.get("否决范围") or "").strip()
            c_reason = reason_label_to_code.get(raw_reason, raw_reason)
            c_scope = scope_label_to_code.get(raw_scope, raw_scope or "track")
            c_note = str(row.get("备注") or "").strip()

            o_app = bool(orig.get("通过", False))
            o_rej = bool(orig.get("排除", False))
            o_def = bool(orig.get("存疑", False))
            o_reason = str(orig.get("_original_reason") or orig.get("否决原因") or "").strip()
            o_scope = str(orig.get("_original_scope") or orig.get("否决范围") or "track").strip()
            o_note = str(orig.get("_original_notes") or orig.get("备注") or "").strip()

            status_changed = (c_app != o_app or c_rej != o_rej or c_def != o_def)
            meta_changed = (c_reason != o_reason or c_scope != o_scope or c_note != o_note)

            if not status_changed and not meta_changed:
                continue

            if c_app and not o_app:
                new_status = "approved"
            elif c_rej and not o_rej:
                new_status = "rejected"
            elif c_def and not o_def:
                new_status = "deferred"
            elif c_app:
                new_status = "approved"
            elif c_rej:
                new_status = "rejected"
            elif c_def:
                new_status = "deferred"
            else:
                new_status = "pending"

            reason_to_save = None
            if new_status == "rejected":
                if c_reason and c_reason in REJECTION_REASON_LABELS:
                    reason_to_save = c_reason
                elif default_reject_reason in REJECTION_REASON_LABELS:
                    reason_to_save = default_reject_reason
                elif default_reject_reason in reason_label_to_code:
                    reason_to_save = reason_label_to_code[default_reject_reason]
                elif not c_reason and not default_reject_reason:
                    reason_to_save = "other"
                else:
                    raise ValueError(f"歌曲 #{cid} 否决原因 '{c_reason or default_reject_reason}' 无效")

            updates.append({
                "candidate_id": cid,
                "status": new_status,
                "decision": new_status,
                "reason_code": reason_to_save,
                "reason_scope": c_scope if c_scope in REASON_SCOPE_LABELS else "track",
                "notes": c_note or "Table editor decision",
                "note": c_note or "Table editor decision",
                "model_version": active_model_version,
            })
        else:
            # Single-selection format: check if manual inline edit occurred
            c_status_raw = str(row.get("当前状态") or "").strip()
            status_map_rev = {v: k for k, v in REVIEW_STATUS_DISPLAY_MAP.items()}
            c_status = status_map_rev.get(c_status_raw, row.get("_original_status", "pending"))
            o_status = orig.get("_original_status", "pending")
            raw_reason = str(row.get("否决原因") or "").strip()
            raw_scope = str(row.get("否决范围") or "").strip()
            c_reason = reason_label_to_code.get(raw_reason, raw_reason)
            c_scope = scope_label_to_code.get(raw_scope, raw_scope or "track")
            c_note = str(row.get("备注") or "").strip()
            o_reason = str(orig.get("_original_reason") or "").strip()
            o_scope = str(orig.get("_original_scope") or "track").strip()
            o_note = str(orig.get("_original_notes") or "").strip()

            if c_status != o_status or c_reason != o_reason or c_scope != o_scope or c_note != o_note:
                reason_to_save = None
                if c_status == "rejected":
                    if c_reason and c_reason in REJECTION_REASON_LABELS:
                        reason_to_save = c_reason
                    elif default_reject_reason in REJECTION_REASON_LABELS:
                        reason_to_save = default_reject_reason
                    elif default_reject_reason in reason_label_to_code:
                        reason_to_save = reason_label_to_code[default_reject_reason]
                    elif not c_reason and not default_reject_reason:
                        reason_to_save = "other"
                    else:
                        raise ValueError(f"歌曲 #{cid} 否决原因 '{c_reason or default_reject_reason}' 无效")

                updates.append({
                    "candidate_id": cid,
                    "status": c_status,
                    "decision": c_status,
                    "reason_code": reason_to_save,
                    "reason_scope": c_scope if c_scope in REASON_SCOPE_LABELS else "track",
                    "notes": c_note or "Table editor decision",
                    "note": c_note or "Table editor decision",
                    "model_version": active_model_version,
                })

    return updates


def compute_preview_signature(approved_candidate_ids: List[int], playlist_name: str) -> str:
    """Compute a deterministic signature for a set of approved candidate IDs and playlist name."""
    sorted_ids = sorted([int(cid) for cid in approved_candidate_ids])
    ids_str = ",".join(str(i) for i in sorted_ids)
    raw = f"{playlist_name.strip()}||{ids_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_publication_readiness(db: DiscoveryDB) -> Dict[str, Any]:
    """
    Check if the candidate database is ready for NetEase publication.
    Criteria:
    - pending == 0: All discovered candidates have been reviewed.
    - approved > 0: At least one candidate has been approved.
    """
    stats = db.get_stats()
    pending_rows = db.get_candidates(status="pending", limit=100000)
    pending = sum(1 for row in pending_rows if not is_incomplete_kkbox_candidate(row))
    incomplete_pending = len(pending_rows) - pending
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)
    deferred = stats.get("deferred", 0)
    machine_filtered = stats.get("machine_filtered", 0)
    total = stats.get("total_candidates", 0)

    is_ready = (pending == 0 and approved > 0)

    if pending > 0:
        message = f"当前还有 {pending} 首歌曲待审核。发布前必须完成全部候选审核。"
    elif approved == 0:
        message = "当前没有已通过 (Approved) 的曲目可发布。"
    else:
        message = f"审核已完成！共有 {approved} 首已通过曲目可发布至网易云歌单。"

    return {
        "is_ready": is_ready,
        "pending_count": pending,
        "incomplete_pending_count": incomplete_pending,
        "approved_count": approved,
        "rejected_count": rejected,
        "deferred_count": deferred,
        "machine_filtered_count": machine_filtered,
        "total_candidates": total,
        "message": message,
    }


def generate_default_weekly_playlist_name(date: Optional[datetime.date] = None) -> str:
    """Generate default weekly playlist name, e.g. '华语新歌周刊 2026年第35周'."""
    d = date or datetime.date.today()
    year, week, _ = d.isocalendar()
    return f"华语新歌周刊 {year}年第{week:02d}周"


def update_pending_tracking(
    state: Dict[str, Any],
    current_pending: int,
    current_time: Optional[float] = None,
) -> None:
    """Update pending change timestamp in state when current pending count changes."""
    now = current_time if current_time is not None else datetime.datetime.now().timestamp()
    last_pending = state.get("last_pending_count", 0)

    if current_pending != last_pending:
        state["last_pending_count"] = current_pending
        state["pending_changed_at"] = now
    elif "pending_changed_at" not in state:
        state["pending_changed_at"] = now


def should_send_notification(
    state: Dict[str, Any],
    current_pending: int,
    debounce_seconds: float = 60.0,
    current_time: Optional[float] = None,
) -> bool:
    """Determine if a notification should be triggered for pending candidates."""
    if current_pending <= 0:
        return False

    last_notified_pending = state.get("last_notified_pending_count", 0)
    if current_pending <= last_notified_pending:
        return False

    now = current_time if current_time is not None else datetime.datetime.now().timestamp()
    pending_changed_at = state.get("pending_changed_at", now)
    last_notified_time = state.get("last_notified_time", 0.0)

    if (now - pending_changed_at) < debounce_seconds:
        return False

    if (now - last_notified_time) < debounce_seconds:
        return False

    return True


def send_macos_notification(title: str, subtitle: str, message: str) -> bool:
    """Send desktop notification on macOS using AppleScript osascript."""
    if platform.system() != "Darwin":
        return False

    safe_title = title.replace('"', '\\"')
    safe_sub = subtitle.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')

    script = (
        f'display notification "{safe_msg}" '
        f'with title "{safe_title}" '
        f'subtitle "{safe_sub}"'
    )

    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def schedule_refresh_command(command_path: str = "output/refresh_command.json") -> Dict[str, Any]:
    """Atomically schedule a full 3-platform refresh command for the supervisor to consume."""
    os.makedirs(os.path.dirname(os.path.abspath(command_path)), exist_ok=True)
    if os.path.exists(command_path):
        try:
            with open(command_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("status") in ("pending", "running"):
                return existing
        except Exception:
            pass

    cmd_record = {
        "command_id": f"refresh_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "action": "refresh_all_platforms",
        "status": "pending",
        "requested_at": datetime.datetime.now().isoformat(),
    }
    tmp_path = f"{command_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cmd_record, f, indent=2)
    os.replace(tmp_path, command_path)
    return cmd_record


def get_platforms_overview(
    db: DiscoveryDB,
    state: Optional[Dict[str, Any]] = None,
    sidecar_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Aggregate the health, candidate count, and last run time across all 3 platforms."""
    stats = db.get_stats()
    platform_counts = stats.get("platforms", {})
    state_data = state or {}
    platform_runs = state_data.get("platforms_summary", {})

    # 1. QQ Music
    qq_run = platform_runs.get("qq", {})
    fallback_run_status = state_data.get("last_run_status")
    qq_status = qq_run.get("status") or (
        "success" if fallback_run_status in ("success", "success_with_warnings") else "ready"
    )
    if qq_status == "success":
        qq_health = "正常在线 (Online)"
    elif qq_status == "failed":
        qq_health = "最近采集异常 (Warning)"
    else:
        qq_health = "正常就绪 (Ready)"

    qq_info = {
        "platform": "qq",
        "display_name": "QQ 音乐 (QQ Music)",
        "candidates_count": platform_counts.get("qq", 0),
        "health": qq_health,
        "status": qq_status,
        "last_run_time": qq_run.get("last_run_time") or state_data.get("last_run_time") or "待首次采集",
        "error": qq_run.get("error"),
    }

    # 2. NetEase Music
    ne_run = platform_runs.get("netease", {})
    ne_status = ne_run.get("status") or (
        "success" if fallback_run_status in ("success", "success_with_warnings") else "ready"
    )
    if ne_status == "success":
        ne_health = "正常在线 (Online)"
    elif ne_status == "failed":
        ne_health = "最近采集异常 (Warning)"
    else:
        ne_health = "正常就绪 (Ready)"

    ne_info = {
        "platform": "netease",
        "display_name": "网易云音乐 (NetEase)",
        "candidates_count": platform_counts.get("netease", 0),
        "health": ne_health,
        "status": ne_status,
        "last_run_time": ne_run.get("last_run_time") or state_data.get("last_run_time") or "待首次采集",
        "error": ne_run.get("error"),
    }

    # 3. KKBOX official API, with Sidecar only as a credential-less fallback.
    kk_run = platform_runs.get("kkbox", {})
    api_configured = bool(os.environ.get("KKBOX_CLIENT_ID") and os.environ.get("KKBOX_CLIENT_SECRET"))
    kk_sidecar = sidecar_status or state_data.get("kkbox_sidecar", {})
    is_online = kk_sidecar.get("online", False)
    sub_status = kk_sidecar.get("status", "idle")

    if api_configured:
        kk_status = kk_run.get("status") or "ready"
        if kk_status == "success":
            kk_health = "官方 API 正常在线 (Official API Online)"
        elif kk_status == "failed":
            kk_health = "官方 API 最近采集异常 (Warning)"
        else:
            kk_health = "官方 API 已就绪 (Official API Ready)"
    elif is_online:
        kk_status = sub_status
        if sub_status == "crawling":
            kk_health = "采集同步中 (Syncing)"
        elif sub_status == "auth_required":
            kk_health = "需在浏览器登录 KKBOX (Auth Required)"
        elif sub_status == "geoblocked":
            kk_health = "地区限制需开台/港节点 (Geoblocked)"
        else:
            kk_health = "会话适配器在线 (Sidecar Online)"
    else:
        kk_status = "waiting_sidecar"
        kk_health = "尚未连接（需一次性浏览器初始化）"

    kk_info = {
        "platform": "kkbox",
        "display_name": "KKBOX",
        "candidates_count": platform_counts.get("kkbox", 0),
        "health": kk_health,
        "status": kk_status,
        "source_mode": "official_open_api" if api_configured else "sidecar_fallback",
        "is_online": api_configured or is_online,
        "last_run_time": kk_run.get("last_run_time") or kk_sidecar.get("last_seen_iso") or "待首次同步",
        "error": kk_run.get("error") if api_configured else kk_sidecar.get("error"),
    }

    return {
        "platforms": {
            "qq": qq_info,
            "netease": ne_info,
            "kkbox": kk_info,
        },
        "total_candidates": stats.get("total_candidates", 0),
        "pending_count": stats.get("pending", 0),
        "approved_count": stats.get("approved", 0),
    }


def train_preference_model_from_db(
    db: DiscoveryDB,
    version_id: Optional[str] = None,
    cv_folds: int = 5,
) -> Tuple[PreferenceModel, Dict[str, Any]]:
    """
    Train a personalized preference model using feedback records in SQLite.
    Saves and activates the model if recall safety gate passes.
    """
    feedbacks = db.get_feedbacks(limit=20000)
    previous_model = get_active_preference_model(db)
    learner = PreferenceLearner()
    model, summary = learner.train_model(feedbacks=feedbacks, version_id=version_id, cv_folds=cv_folds)

    new_precision = float(summary.get("metrics", {}).get("eval_precision", 0.0))
    old_precision = float(previous_model.metrics.get("eval_precision", 0.0)) if not previous_model.is_cold_start else None
    precision_gate_passed = old_precision is None or new_precision >= old_precision
    summary["previous_version"] = previous_model.version_id
    summary["previous_precision"] = old_precision
    summary["precision_gate_passed"] = precision_gate_passed
    activated = bool(
        summary.get("success")
        and summary.get("recall_gate_passed")
        and precision_gate_passed
    )
    summary["activated"] = activated
    if activated:
        persisted_metrics = dict(model.metrics)
        persisted_metrics["bias"] = model.bias
        persisted_metrics["threshold"] = model.threshold
        db.save_model_version(
            version_id=model.version_id,
            weights=model.weights,
            metrics=persisted_metrics,
            sample_stats=model.sample_stats,
            status="active",
            notes=f"Grouped CV trained on {len(feedbacks)} samples. Recall: {model.metrics.get('eval_recall'):.3f}",
        )
    return model, summary


def maybe_train_preference_model_from_db(
    db: DiscoveryDB,
    min_new_labels: int = 20,
) -> Dict[str, Any]:
    """Automatically train at first eligibility, then after each label increment."""
    feedbacks = db.get_feedbacks(limit=20000)
    learner = PreferenceLearner()
    criteria = learner.check_activation_criteria(feedbacks)
    if not criteria["can_activate"]:
        return {"attempted": False, "reason": "cold_start", "criteria": criteria}

    active_row = db.get_active_model_version()
    active_sample_count = int(active_row.get("sample_count", 0)) if active_row else 0
    if active_row and criteria["total_count"] - active_sample_count < min_new_labels:
        return {
            "attempted": False,
            "reason": "waiting_for_more_labels",
            "new_labels": criteria["total_count"] - active_sample_count,
            "required_new_labels": min_new_labels,
        }

    model, summary = train_preference_model_from_db(db)
    return {
        "attempted": True,
        "activated": bool(summary.get("activated")),
        "model_version": model.version_id,
        "summary": summary,
    }
