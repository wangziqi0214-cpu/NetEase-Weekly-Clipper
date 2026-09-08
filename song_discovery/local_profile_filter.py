"""Second-pass candidate routing from the local artist knowledge database.

The filter is intentionally conservative: missing, incomplete, failed, or
sparse artist knowledge never rejects a candidate.  Only completed local
profiles with explicit non-target identity evidence may move an untouched
pending candidate into the recoverable machine-filtered pool.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

PROFILE_REASON_PREFIX = "本地画像筛选:"
PROFILE_FILTER_NOTE_PREFIX = "本地画像机筛:"

SAFE_STATUSES = {"completed"}

TARGET_KEYWORDS = {
    "乐队", "乐团", "摇滚", "后摇", "朋克", "金属", "独立", "另类", "民谣",
    "自赏", "盯鞋", "迷幻", "车库", "布鲁斯", "放克", "硬核", "新浪潮",
    "band", "rock", "post-rock", "punk", "metal", "indie", "alternative",
    "folk", "shoegaze", "dream pop", "psychedelic", "garage", "blues",
    "funk", "hardcore", "new wave", "britpop", "emo", "math rock",
}

NON_TARGET_KEYWORDS = {
    "dj": "明确 DJ 身份",
    "制作人": "明确制作人身份",
    "producer": "明确 Producer 身份",
    "唱作制作人": "明确制作人身份",
    "音乐制作": "明确制作人身份",
    "流行歌手": "明确流行歌手身份",
    "普通流行": "明确普通流行定位",
    "mainstream pop": "明确主流流行定位",
    "pop singer": "明确流行歌手身份",
    "偶像": "明确偶像/流行艺人定位",
    "idol": "明确偶像/流行艺人定位",
    "rapper": "明确说唱艺人定位",
    "说唱歌手": "明确说唱艺人定位",
    "影视原声": "明确影视/OST 定位",
    "ost": "明确 OST 定位",
    "古典": "明确古典/非目标定位",
    "纯音乐": "明确纯音乐/非目标定位",
}

NON_TARGET_TYPES = {
    "dj": "类型为 DJ",
    "producer": "类型为 Producer",
    "制作人": "类型为制作人",
}

# KKBOX/legacy adapters can emit these values when the real artist metadata
# was not extracted.  A profile collected for one placeholder row must never
# be reused to reject every other song carrying the same placeholder.
PLACEHOLDER_ARTIST_KEYS = {
    "kkboxartist",
    "unknownartist",
    "unknownartistname",
    "未知歌手",
    "未知艺人",
}
PLACEHOLDER_RELEASE_KEYS = {"kkboxwebplayer", "kkbox", "letsmusickkbox"}


def _canonical_placeholder_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


def _has_placeholder_metadata(candidate: Dict[str, Any], artist_names: Iterable[str]) -> bool:
    if any(_canonical_placeholder_key(name) in PLACEHOLDER_ARTIST_KEYS for name in artist_names):
        return True
    if str(candidate.get("platform") or "").casefold() == "kkbox":
        release_key = _canonical_placeholder_key(candidate.get("release_title"))
        if release_key in PLACEHOLDER_RELEASE_KEYS:
            return True
    return False


@dataclass
class LocalProfileDecision:
    """Local artist-profile screening result for one candidate."""

    status: str
    action: str
    reasons: List[str] = field(default_factory=list)
    matched_artists: List[str] = field(default_factory=list)

    @property
    def should_machine_filter(self) -> bool:
        return self.action == "machine_filter"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "reasons": list(self.reasons),
            "matched_artists": list(self.matched_artists),
        }


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _load_json_maybe(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return fallback


def _profile_text(record: Dict[str, Any]) -> str:
    identity = _load_json_maybe(record.get("identity_context"), {})
    if not isinstance(identity, dict):
        identity = {}
    parts: List[str] = [
        str(record.get("display_name") or ""),
        str(record.get("factual_summary") or ""),
        str(identity.get("type") or ""),
        str(identity.get("origin") or ""),
        str(identity.get("active_years") or ""),
    ]
    for key in ("genre", "members", "notable_works", "tags"):
        parts.extend(_as_list(identity.get(key)))
    return " ".join(parts).casefold()


def _profile_type(record: Dict[str, Any]) -> str:
    identity = _load_json_maybe(record.get("identity_context"), {})
    if isinstance(identity, dict):
        return str(identity.get("type") or "").strip().casefold()
    return ""


def _has_sources(record: Dict[str, Any]) -> bool:
    sources = _load_json_maybe(record.get("sources"), [])
    return bool(sources) if isinstance(sources, list) else bool(record.get("sources"))


def _is_completed_and_usable(record: Optional[Dict[str, Any]]) -> bool:
    if not record:
        return False
    status = str(record.get("status") or "").strip().lower()
    if status not in SAFE_STATUSES:
        return False
    if str(record.get("uncertainty") or "").strip().lower() == "high":
        return False
    if not _has_sources(record):
        return False
    return True


def _has_target_evidence(text: str, candidate: Dict[str, Any]) -> bool:
    candidate_reasons = candidate.get("relevance_reasons") or []
    if isinstance(candidate_reasons, str):
        candidate_reasons = [candidate_reasons]
    combined = " ".join([
        text,
        str(candidate.get("artist_names") or ""),
        str(candidate.get("track_title") or ""),
        str(candidate.get("release_title") or ""),
        " ".join(str(item) for item in candidate_reasons),
    ]).casefold()
    return any(keyword.casefold() in combined for keyword in TARGET_KEYWORDS)


def _explicit_non_target_reasons(record: Dict[str, Any], text: str) -> List[str]:
    reasons: List[str] = []
    profile_type = _profile_type(record)
    for type_keyword, reason in NON_TARGET_TYPES.items():
        if profile_type == type_keyword or profile_type.startswith(type_keyword):
            reasons.append(reason)
    for keyword, reason in NON_TARGET_KEYWORDS.items():
        pattern = r"\bdj\b" if keyword == "dj" else re.escape(keyword.casefold())
        if re.search(pattern, text):
            reasons.append(reason)
    return list(dict.fromkeys(reasons))


def evaluate_candidate_with_local_profiles(
    candidate: Dict[str, Any],
    knowledge_by_artist: Dict[str, Optional[Dict[str, Any]]],
) -> LocalProfileDecision:
    """Evaluate one candidate using already-loaded local artist profiles only."""

    artists = [name for name in knowledge_by_artist.keys() if name]
    if not artists:
        return LocalProfileDecision(
            status="not_found",
            action="keep_pending",
            reasons=[f"{PROFILE_REASON_PREFIX} 未找到可用艺人名，保留人工审核"],
        )

    if _has_placeholder_metadata(candidate, artists):
        return LocalProfileDecision(
            status="incomplete",
            action="keep_pending",
            reasons=[f"{PROFILE_REASON_PREFIX} 抓取字段为占位符，未拿到真实艺人，保留人工审核"],
            matched_artists=artists,
        )

    incomplete: List[str] = []
    usable_records: List[tuple[str, Dict[str, Any]]] = []
    for artist_name, record in knowledge_by_artist.items():
        if _is_completed_and_usable(record):
            usable_records.append((artist_name, record or {}))
        else:
            status = str((record or {}).get("status") or "not_found").strip().lower()
            label = "未收录" if status in ("", "not_found") else status
            incomplete.append(f"{artist_name}({label})")

    if incomplete:
        return LocalProfileDecision(
            status="incomplete",
            action="keep_pending",
            reasons=[f"{PROFILE_REASON_PREFIX} 资料未完成或稀疏：{', '.join(incomplete)}；不作为排除依据"],
            matched_artists=artists,
        )

    non_target_reasons: List[str] = []
    checked_names: List[str] = []
    for artist_name, record in usable_records:
        checked_names.append(str(record.get("display_name") or artist_name))
        text = _profile_text(record)
        if _has_target_evidence(text, candidate):
            continue
        for reason in _explicit_non_target_reasons(record, text):
            non_target_reasons.append(f"{artist_name}: {reason}")

    if non_target_reasons:
        return LocalProfileDecision(
            status="completed",
            action="machine_filter",
            reasons=[
                f"{PROFILE_REASON_PREFIX} 本地资料明确非目标：{'; '.join(list(dict.fromkeys(non_target_reasons)))}"
            ],
            matched_artists=checked_names,
        )

    return LocalProfileDecision(
        status="completed",
        action="keep_pending",
        reasons=[f"{PROFILE_REASON_PREFIX} 本地资料未发现明确非目标证据，保留人工审核"],
        matched_artists=checked_names,
    )


def strip_profile_reasons(reasons: Iterable[Any]) -> List[str]:
    """Remove stale local-profile screening reasons before writing fresh ones."""

    cleaned: List[str] = []
    for reason in reasons or []:
        text = str(reason)
        if not text.startswith(PROFILE_REASON_PREFIX):
            cleaned.append(text)
    return cleaned
