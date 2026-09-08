"""FastAPI backend for the AGY-designed high-density review workbench."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from song_discovery.artist_knowledge import (
    artist_knowledge_needs_refresh,
    get_artist_collector,
    normalize_artist_name,
)
from song_discovery.db import DiscoveryDB
from song_discovery.exceptions import LoginRequiredError, PublishError
from song_discovery.local_profile_filter import PROFILE_REASON_PREFIX
from song_discovery.preference_learner import PreferenceLearner
from song_discovery.publisher import NetEasePublisher
from song_discovery.video_workflow import resume_video_workflow, start_video_workflow
from song_discovery.review_helpers import (
    PLATFORM_REPRESENTATIVE_PRIORITY,
    build_batch_decision_updates,
    classify_candidate_tier,
    deduplicate_candidates,
    get_active_preference_model,
    generate_default_weekly_playlist_name,
    get_publication_readiness,
    get_song_dedup_key,
    is_incomplete_kkbox_candidate,
    maybe_train_preference_model_from_db,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "output" / "discovery.db"
FRONTEND_DIST = PROJECT_ROOT / "review_workbench" / "dist"
VALID_STATUSES = {"pending", "approved", "rejected", "deferred", "machine_filtered"}
VALID_TIERS = {"TIER_1_HOT", "TIER_2_RULE", "TIER_3_ATTENTION", "TIER_4_NOISE"}
VIDEO_ARTIFACTS = {
    "video": ("final_video.mp4", "video/mp4"),
    "intro": ("intro.mp4", "video/mp4"),
    "song_info": ("song_info.md", "text/markdown"),
    "release_report": ("release_report.md", "text/markdown"),
    "publish_copy": ("publish_copy.md", "text/markdown"),
    "publish_copy_auto": ("publish_copy.auto.md", "text/markdown"),
    "workflow_log": ("workflow.log", "text/plain; charset=utf-8"),
    "log": ("workflow.log", "text/plain; charset=utf-8"),
}


class BatchStatusRequest(BaseModel):
    candidate_ids: List[int] = Field(min_length=1)
    status: str
    notes: str = ""
    reason_code: Optional[str] = None
    reason_scope: str = "track"


class PublishCopyUpdateRequest(BaseModel):
    text: str = Field(..., max_length=100000)


class PublishRequest(BaseModel):
    playlist_name: str = ""
    allow_partial: bool = False
    confirm: bool = False
    ordered_candidate_ids: List[int] = Field(default_factory=list)
    start_video_workflow: bool = True


class PublicationOrderRequest(BaseModel):
    candidate_ids: List[int] = Field(min_length=1)


class ManualMatchRequest(BaseModel):
    candidate_id: int
    netease_track_id: str = Field(..., min_length=1, max_length=100)
    matched_title: str = Field(default="", max_length=200)
    matched_artists: str = Field(default="", max_length=200)
    target_release_date: str = Field(default="", max_length=50)
    notes: str = Field(default="", max_length=500)


class ArtistKnowledgeCollectRequest(BaseModel):
    artist_name: str
    song_title: Optional[str] = ""
    album_title: Optional[str] = ""
    force: Optional[bool] = False


class ArtistKnowledgeBatchRequest(BaseModel):
    artists: Optional[List[str]] = None
    artist_names: Optional[List[str]] = None
    auto_collect: Optional[bool] = False
    auto_collect_missing: Optional[bool] = None


def _db_path() -> str:
    return os.environ.get("DISCOVERY_DB_PATH", str(DEFAULT_DB_PATH))


def get_db() -> DiscoveryDB:
    return DiscoveryDB(db_path=_db_path())


def _metadata_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _find_cover_url(metadata: Dict[str, Any]) -> str:
    direct_keys = (
        "cover_url", "coverUrl", "cover", "picUrl", "pic_url", "image",
        "image_url", "imageUrl", "album_cover", "albumCover", "artworkUrl",
    )
    for key in direct_keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    for key in ("album", "al", "release", "images", "artwork", "photo", "songInfo", "data"):
        nested = metadata.get(key)
        if isinstance(nested, dict):
            found = _find_cover_url(nested)
            if found:
                return found
        elif isinstance(nested, list):
            items = nested[:5]
            if key == "images":
                items = sorted(
                    items,
                    key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0)
                    if isinstance(item, dict) else 0,
                    reverse=True,
                )
            for item in items:
                if isinstance(item, dict):
                    if key == "images":
                        image_url = item.get("url")
                        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                            return image_url
                    found = _find_cover_url(item)
                    if found:
                        return found
                elif isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item
    return ""


def _candidate_cover_url(candidate: Dict[str, Any]) -> str:
    """Resolve track/release artwork, including deterministic QQ album artwork."""
    for field in ("raw_metadata", "release_raw_metadata"):
        found = _find_cover_url(_metadata_dict(candidate.get(field)))
        if found:
            return found

    if str(candidate.get("platform") or "") == "qq":
        release_mid = str(candidate.get("release_source_id") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{8,40}", release_mid):
            return f"https://y.qq.com/music/photo_new/T002R500x500M000{release_mid}.jpg"
    return ""


_FEAT_RE = re.compile(r"[\(\[（【]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]）】]+)[\)\]）】]", re.I)


def _title_parts(title: str) -> tuple[str, List[str]]:
    featured: List[str] = []

    def replace(match: re.Match[str]) -> str:
        featured.extend(part.strip() for part in re.split(r"[/,&、，+]|\s+x\s+", match.group(1), flags=re.I) if part.strip())
        return ""

    clean = re.sub(r"\s+", " ", _FEAT_RE.sub(replace, title or "")).strip()
    return clean or title, featured


def _completeness(candidate: Dict[str, Any]) -> int:
    return sum(bool(candidate.get(key)) for key in (
        "track_title", "artist_names", "release_title", "release_date", "duration_ms", "track_url",
    ))


def _screening(candidate: Dict[str, Any], model: Any) -> tuple[str, List[str]]:
    tier_code, tier_label, is_band_or_rock = classify_candidate_tier(candidate, model=model)
    status = str(candidate.get("review_status") or "pending")
    platform_count = len(candidate.get("source_platforms") or [candidate.get("platform")])
    if status == "machine_filtered":
        visual_tier = "TIER_4_NOISE"
    elif tier_code == "tier1" and platform_count >= 3:
        visual_tier = "TIER_1_HOT"
    elif tier_code == "tier1":
        visual_tier = "TIER_2_RULE"
    else:
        visual_tier = "TIER_3_ATTENTION"
    reasons = candidate.get("relevance_reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    explanations: List[str] = [tier_label]
    explanations.extend(str(value) for value in reasons if value)
    if is_band_or_rock:
        explanations.insert(0, "乐队/摇滚特征")
    return visual_tier, list(dict.fromkeys(explanations))[:5]


def _local_profile_screening(candidate: Dict[str, Any]) -> Dict[str, Any]:
    reasons = candidate.get("relevance_reasons") or []
    if isinstance(reasons, str):
        try:
            parsed = json.loads(reasons)
            reasons = parsed if isinstance(parsed, list) else [reasons]
        except Exception:
            reasons = [reasons]
    profile_reasons = [
        str(reason)
        for reason in reasons
        if str(reason).startswith(PROFILE_REASON_PREFIX)
    ]
    status = "not_evaluated"
    action = "keep_pending"
    if profile_reasons:
        reason_text = " ".join(profile_reasons)
        if "资料未完成" in reason_text or "稀疏" in reason_text:
            status = "incomplete"
        elif "未发现明确非目标" in reason_text:
            status = "completed"
        elif "明确非目标" in reason_text:
            status = "completed"
            action = "machine_filter"
    return {
        "status": status,
        "action": action,
        "reasons": profile_reasons,
    }


def _raw_record(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(candidate["id"]),
        "platform": str(candidate.get("platform") or ""),
        "source_id": str(candidate.get("track_source_id") or ""),
        "title": str(candidate.get("track_title") or ""),
        "artist_names": str(candidate.get("artist_names") or ""),
        "album_title": str(candidate.get("release_title") or ""),
        "release_type": str(candidate.get("release_type") or "other"),
        "release_date": str(candidate.get("release_date") or ""),
        "duration_ms": int(candidate.get("duration_ms") or 0),
        "track_number": int(candidate.get("track_number") or 0),
        "source_url": str(candidate.get("track_url") or candidate.get("release_url") or ""),
        "raw_metadata": _metadata_dict(candidate.get("raw_metadata")),
        "status": str(candidate.get("review_status") or "pending"),
        "reviewed_at": candidate.get("reviewed_at"),
    }


def _serialize_candidate(candidate: Dict[str, Any], groups: Dict[str, List[Dict[str, Any]]], model: Any) -> Dict[str, Any]:
    key = str(candidate.get("canonical_key") or get_song_dedup_key(candidate))
    group = groups.get(key, [candidate])
    clean_title, featured = _title_parts(str(candidate.get("track_title") or ""))
    tier, reasons = _screening(candidate, model)
    platforms = list(candidate.get("source_platforms") or [candidate.get("platform")])
    return {
        "id": int(candidate["id"]),
        "canonical_key": key,
        "title": str(candidate.get("track_title") or ""),
        "clean_title": clean_title,
        "featured_artists": featured,
        "artist_names": str(candidate.get("artist_names") or ""),
        "album_title": str(candidate.get("release_title") or ""),
        "release_type": str(candidate.get("release_type") or "other"),
        "release_date": str(candidate.get("release_date") or ""),
        "duration_ms": int(candidate.get("duration_ms") or 0),
        "status": str(candidate.get("review_status") or "pending"),
        "platform": str(candidate.get("platform") or ""),
        "platforms": platforms,
        "platforms_display": str(candidate.get("platforms_display") or str(candidate.get("platform") or "").upper()),
        "dedup_count": int(candidate.get("dedup_count") or len(group)),
        "source_url": str(candidate.get("track_url") or candidate.get("release_url") or ""),
        "source_urls": dict(candidate.get("source_urls") or {}),
        "raw_ids": list(candidate.get("raw_ids") or [candidate["id"]]),
        "raw_candidates": [_raw_record(item) for item in group],
        "cover_url": next(
            (url for item in [candidate, *group] if (url := _candidate_cover_url(item))),
            "",
        ),
        "screening_tier": tier,
        "screening_reasons": reasons,
        "local_profile_screening": _local_profile_screening(candidate),
        "completeness_score": _completeness(candidate),
        "rule_applied": str(candidate.get("selection_rule") or ""),
    }


def _reviewable(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [candidate for candidate in candidates if not is_incomplete_kkbox_candidate(candidate)]


app = FastAPI(title="AGY Clipper Review API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "db_path": _db_path()}


@app.get("/api/stats")
def stats() -> Dict[str, Any]:
    db = get_db()
    raw = db.get_stats()
    raw_rows = db.get_candidates(limit=100000)
    reviewable_rows = _reviewable(raw_rows)
    hidden_rows = [row for row in raw_rows if is_incomplete_kkbox_candidate(row)]

    def unique_count(status_value: Optional[str] = None) -> int:
        rows = reviewable_rows if status_value is None else [
            row for row in reviewable_rows if row.get("review_status") == status_value
        ]
        return len(deduplicate_candidates(rows, all_candidates=reviewable_rows))

    return {
        "total": raw["total_candidates"],
        "reviewable_total": len(reviewable_rows),
        "hidden_incomplete": len(hidden_rows),
        "unique_total": unique_count(),
        "pending": raw["pending"],
        "unique_pending": unique_count("pending"),
        "approved": raw["approved"],
        "unique_approved": unique_count("approved"),
        "rejected": raw["rejected"],
        "unique_rejected": unique_count("rejected"),
        "deferred": raw["deferred"],
        "unique_deferred": unique_count("deferred"),
        "machine_filtered": raw["machine_filtered"],
        "unique_machine_filtered": unique_count("machine_filtered"),
        "by_platform": raw["platforms"],
    }


@app.get("/api/candidates")
def candidates(
    platform: str = "all",
    status: str = "pending",
    tier: str = "all",
    keyword: str = "",
    dedup: bool = Query(default=True),
) -> Dict[str, Any]:
    db = get_db()
    raw_all_rows = db.get_candidates(limit=100000)
    all_rows = _reviewable(raw_all_rows)
    filtered = all_rows
    hidden_filtered = [row for row in raw_all_rows if is_incomplete_kkbox_candidate(row)]
    if platform.lower() != "all":
        filtered = [row for row in filtered if str(row.get("platform") or "").lower() == platform.lower()]
        hidden_filtered = [row for row in hidden_filtered if str(row.get("platform") or "").lower() == platform.lower()]
    if status.lower() != "all":
        filtered = [row for row in filtered if str(row.get("review_status") or "").lower() == status.lower()]
        hidden_filtered = [row for row in hidden_filtered if str(row.get("review_status") or "").lower() == status.lower()]
    needle = keyword.casefold().strip()
    if needle:
        filtered = [row for row in filtered if needle in " ".join(str(row.get(key) or "") for key in ("track_title", "artist_names", "release_title")).casefold()]
        hidden_filtered = [row for row in hidden_filtered if needle in " ".join(str(row.get(key) or "") for key in ("track_title", "artist_names", "release_title")).casefold()]

    raw_count = len(filtered)
    display_rows = deduplicate_candidates(filtered, all_candidates=all_rows) if dedup else filtered
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_rows:
        groups.setdefault(get_song_dedup_key(row), []).append(row)
    model = get_active_preference_model(db)
    items = [_serialize_candidate(row, groups, model) for row in display_rows]
    if tier != "all":
        if tier not in VALID_TIERS:
            raise HTTPException(status_code=422, detail="Invalid screening tier")
        items = [item for item in items if item["screening_tier"] == tier]
    return {
        "items": items,
        "count": len(items),
        "raw_count": raw_count,
        "hidden_incomplete": len(hidden_filtered),
    }


@app.post("/api/candidates/batch-status")
def batch_status(payload: BatchStatusRequest) -> Dict[str, Any]:
    status = payload.status.lower().strip()
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid review status")
    db = get_db()
    all_rows = db.get_candidates(limit=100000)
    requested = set(payload.candidate_ids)
    requested_rows = [row for row in all_rows if int(row["id"]) in requested]
    if not requested_rows:
        raise HTTPException(status_code=404, detail="No matching candidates")

    all_groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_rows:
        all_groups.setdefault(get_song_dedup_key(row), []).append(row)
    selected_keys = {get_song_dedup_key(row) for row in requested_rows}
    representatives: List[int] = []
    sync_ids: List[int] = []
    for key in selected_keys:
        group = all_groups[key]
        representative = min(group, key=lambda row: (
            PLATFORM_REPRESENTATIVE_PRIORITY.get(str(row.get("platform") or "").lower(), 9),
            int(row["id"]),
        ))
        representatives.append(int(representative["id"]))
        sync_ids.extend(int(row["id"]) for row in group if int(row["id"]) != int(representative["id"]))

    model = get_active_preference_model(db)
    updates = build_batch_decision_updates(
        representatives,
        status,
        reason_code=payload.reason_code,
        reason_scope=payload.reason_scope,
        notes=payload.notes,
        active_model_version=model.version_id,
    )
    learned = db.bulk_update_review_status(updates)
    synced = db.bulk_sync_review_status(sync_ids, status, payload.notes)
    try:
        learning = maybe_train_preference_model_from_db(db)
    except Exception as exc:  # review persistence must not be rolled back by optional training
        learning = {"attempted": False, "reason": "training_error", "detail": str(exc)}
    return {"success": True, "updated": learned + synced, "learning_samples": learned, "learning": learning}


def _publication_preview(db: DiscoveryDB) -> Dict[str, Any]:
    playlist_name = generate_default_weekly_playlist_name()
    saved_order = db.get_delivery_order(playlist_name)
    excluded_ids = set(db.get_delivery_exclusions(playlist_name))
    publisher = NetEasePublisher(base_url="http://127.0.0.1:3000", db=db)
    result = publisher.publish_approved(
        playlist_name=playlist_name,
        cookie_file=str(PROJECT_ROOT / "cookie.txt"),
        dry_run=True,
        ordered_candidate_ids=saved_order,
    )
    items = result.get("resolved_items") or []

    prior_publication = db.get_latest_publication_by_name(playlist_name)
    published_cand_ids = db.get_already_published_candidate_ids(playlist_name)
    published_track_ids = db.get_already_published_track_ids(prior_publication["playlist_id"]) if prior_publication else set()

    ready_all = []
    seen_ready_ids = set()
    for item in items:
        cid = int(item["candidate_id"])
        if cid in excluded_ids:
            continue
        netease_id = str(item.get("netease_track_id") or "")
        if item.get("is_resolved") and netease_id and netease_id not in seen_ready_ids:
            seen_ready_ids.add(netease_id)
            ready_all.append(item)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    ready, out_of_week = [], []
    for item in ready_all:
        effective = str(item.get("target_release_date") or item.get("source_release_date") or "")
        try:
            release_day = date.fromisoformat(effective[:10])
        except ValueError:
            release_day = None
        item["effective_release_date"] = effective
        item["release_gate_reason"] = "本周发行" if release_day and week_start <= release_day <= week_end else ("缺少可靠发行日期" if not release_day else "非本周发行")
        (ready if release_day and week_start <= release_day <= week_end else out_of_week).append(item)

    ambiguous = [item for item in items if item.get("match_status") == "ambiguous" and int(item["candidate_id"]) not in excluded_ids]
    unmatched = [item for item in items if item.get("match_status") == "unmatched" and int(item["candidate_id"]) not in excluded_ids]

    # Resolve excluded items for preview presentation
    excluded_items = []
    if excluded_ids:
        cookie_path = str(PROJECT_ROOT / "cookie.txt") if (PROJECT_ROOT / "cookie.txt").exists() else None
        for cid in sorted(excluded_ids):
            cand = db.get_candidate_by_id(cid)
            if cand:
                res = publisher.resolve_candidate(cand, cookie_file=cookie_path)
                res["is_excluded_from_delivery"] = True
                res["is_published"] = False
                manual_override = db.get_manual_match_override(cid)
                res["is_manual_override"] = bool(manual_override)
                res["manual_override"] = manual_override
                excluded_items.append(res)

    for item in items:
        cid = int(item["candidate_id"])
        nid = str(item.get("netease_track_id") or "")
        manual_override = db.get_manual_match_override(cid)
        item["is_manual_override"] = bool(manual_override)
        item["manual_override"] = manual_override
        item["is_published"] = bool(cid in published_cand_ids or (nid and nid in published_track_ids))

    return {
        **result,
        "playlist_name": playlist_name,
        "ready_count": len({str(item.get("netease_track_id")) for item in ready if item.get("netease_track_id")}),
        "ready_items": ready,
        "ordered_candidate_ids": [int(item["candidate_id"]) for item in ready],
        "out_of_week_count": len(out_of_week),
        "out_of_week_items": out_of_week,
        "release_window": {"start": week_start.isoformat(), "end": week_end.isoformat()},
        "ambiguous_items": ambiguous,
        "unmatched_items": unmatched,
        "excluded_items": excluded_items,
        "excluded_count": len(excluded_items),
        "readiness": get_publication_readiness(db),
    }


@app.get("/api/netease/search")
def netease_search(
    keyword: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    cleaned_kw = keyword.strip()
    url_match = re.search(r"(?:song\?id=|/song/|song/)(\d{4,15})", cleaned_kw)
    direct_id = url_match.group(1) if url_match else (cleaned_kw if cleaned_kw.isdigit() else None)

    publisher = NetEasePublisher(base_url="http://127.0.0.1:3000", db=get_db())
    cookie_path = str(PROJECT_ROOT / "cookie.txt") if (PROJECT_ROOT / "cookie.txt").exists() else None

    raw_songs = []
    if direct_id:
        raw_songs = publisher.search_netease_songs(keywords=direct_id, limit=limit, cookie_file=cookie_path)
    if not raw_songs:
        raw_songs = publisher.search_netease_songs(keywords=cleaned_kw, limit=limit, cookie_file=cookie_path)

    formatted = []
    seen_ids = set()
    for s in raw_songs:
        sid = str(s.get("id") or "")
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)

        ar_list = s.get("ar") or s.get("artists") or []
        artist_str = " / ".join(str(a.get("name") or "") for a in ar_list if a.get("name"))
        al_obj = s.get("al") or s.get("album") or {}
        album_str = str(al_obj.get("name") or "")
        duration_ms = s.get("dt") or s.get("duration") or 0

        pub_time = s.get("publishTime") or al_obj.get("publishTime") or 0
        rel_date = ""
        if pub_time:
            try:
                rel_date = datetime.fromtimestamp(float(pub_time) / 1000.0, tz=timezone.utc).date().isoformat()
            except Exception:
                rel_date = ""

        formatted.append({
            "id": sid,
            "title": str(s.get("name") or ""),
            "artists": artist_str,
            "album": album_str,
            "duration_ms": duration_ms,
            "release_date": rel_date,
            "url": f"https://music.163.com/#/song?id={sid}",
        })

    return {
        "keyword": cleaned_kw,
        "count": len(formatted),
        "results": formatted,
    }


@app.post("/api/publication/manual-match")
def set_manual_match(payload: ManualMatchRequest) -> Dict[str, Any]:
    db = get_db()
    candidate = db.get_candidate_by_id(payload.candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if str(candidate.get("review_status") or "") != "approved":
        raise HTTPException(status_code=409, detail="Only approved candidates can receive a publication match override")

    track_id_str = payload.netease_track_id.strip()
    match = re.fullmatch(r"(\d{4,15})", track_id_str)
    if not match:
        match = re.search(r"(?:song\?id=|[?&]id=)(\d{4,15})(?:\D|$)", track_id_str)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid NetEase track ID; must be numeric or a song URL")
    clean_track_id = match.group(1)

    target_rel_date = payload.target_release_date.strip()
    if not target_rel_date:
        target_rel_date = str(candidate.get("release_date") or "")
    if target_rel_date:
        if len(target_rel_date) != 10:
            raise HTTPException(status_code=400, detail="target_release_date must use YYYY-MM-DD")
        try:
            date.fromisoformat(target_rel_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="target_release_date must use YYYY-MM-DD") from exc

    db.set_manual_match_override(
        candidate_id=payload.candidate_id,
        netease_track_id=clean_track_id,
        matched_title=payload.matched_title.strip() or str(candidate.get("track_title") or ""),
        matched_artists=payload.matched_artists.strip() or str(candidate.get("artist_names") or ""),
        target_release_date=target_rel_date,
        notes=payload.notes.strip(),
    )
    return {
        "success": True,
        "candidate_id": payload.candidate_id,
        "netease_track_id": clean_track_id,
        "target_release_date": target_rel_date,
    }


@app.delete("/api/publication/manual-match/{candidate_id}")
def delete_manual_match(candidate_id: int) -> Dict[str, Any]:
    db = get_db()
    deleted = db.delete_manual_match_override(candidate_id)
    return {"success": True, "candidate_id": candidate_id, "deleted": deleted}


def _video_stage(job: Dict[str, Any], job_dir: Path) -> tuple[str, str]:
    status = str(job.get("status") or "queued")
    terminal_labels = {
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }
    if status in terminal_labels:
        return status, terminal_labels[status]

    log_text = ""
    log_path = job_dir / "workflow.log"
    if log_path.is_file():
        try:
            with log_path.open("rb") as handle:
                handle.seek(max(0, log_path.stat().st_size - 131072))
                log_text = handle.read().decode("utf-8", errors="ignore").lower()
        except OSError:
            log_text = ""

    checks = (
        ("rendering", "视频渲染", ("running core renderer", "starting compilation", "moviepy - writing video")),
        ("report", "报告与文案", ("exporting weekly release report", "exporting song info document")),
        ("intro", "片头生成", ("running intro renderer", "rendering intro")),
        ("tts", "克隆音色 TTS", ("tts track", "[tts]")),
        ("research", "歌曲研究", ("running radio research", "running research")),
        ("crawl", "歌曲抓取", ("starting video workflow", "raw_playlist")),
    )
    for stage, label, markers in checks:
        if any(marker in log_text for marker in markers):
            return stage, label
    return ("queued", "等待启动") if status == "queued" else ("running", "正在处理")


def _decorate_video_job(job: Dict[str, Any]) -> Dict[str, Any]:
    job_dir = PROJECT_ROOT / "output" / "video_jobs" / job["job_id"]
    job["track_count"] = len(job.get("ordered_track_ids") or [])
    job["artifacts"] = {
        key: f"/api/video-workflow/{job['job_id']}/artifacts/{key}"
        for key, (filename, _) in VIDEO_ARTIFACTS.items()
        if (job_dir / filename).is_file() and (job_dir / filename).stat().st_size > 0
    }
    job["stage"], job["stage_label"] = _video_stage(job, job_dir)
    return job


@app.get("/api/video-workflow/jobs")
def list_video_workflow_jobs() -> Dict[str, Any]:
    db = get_db()
    jobs = [_decorate_video_job(job) for job in db.get_video_workflow_jobs(limit=100)]
    return {"jobs": jobs}


@app.get("/api/video-workflow/latest")
def latest_video_workflow() -> Dict[str, Any]:
    job = get_db().get_latest_video_workflow_job()
    if job:
        _decorate_video_job(job)
    return {"job": job}


@app.get("/api/video-workflow/{job_id}")
def get_video_workflow_job_detail(job_id: str) -> Dict[str, Any]:
    if not job_id or not re.match(r"^video_[a-zA-Z0-9_-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    db = get_db()
    job = db.get_video_workflow_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video workflow job not found")
    return {"job": _decorate_video_job(job)}


@app.post("/api/video-workflow/{job_id}/resume")
def resume_video_workflow_job(job_id: str) -> Dict[str, Any]:
    if not job_id or not re.match(r"^video_[a-zA-Z0-9_-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    db = get_db()
    job = db.get_video_workflow_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video workflow job not found")

    current_status = job.get("status")
    if current_status in {"running", "queued"}:
        raise HTTPException(status_code=409, detail=f"Job is already active (status: {current_status})")
    if current_status == "completed":
        raise HTTPException(status_code=400, detail="Cannot resume an already completed job")
    if current_status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=400, detail=f"Job is not in a resumable status ({current_status})")

    try:
        updated_job = resume_video_workflow(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to resume video workflow: {exc}") from exc

    return {"success": True, "job": _decorate_video_job(updated_job)}


@app.get("/api/publication/preview")
def publication_preview() -> Dict[str, Any]:
    return _publication_preview(get_db())


@app.put("/api/publication/order")
def publication_order(payload: PublicationOrderRequest) -> Dict[str, Any]:
    db = get_db()
    playlist_name = generate_default_weekly_playlist_name()
    preview = _publication_preview(db)
    valid = {int(item["candidate_id"]) for item in preview["ready_items"]}
    requested = [int(value) for value in payload.candidate_ids]
    if set(requested) != valid or len(requested) != len(valid):
        raise HTTPException(status_code=400, detail="Order must contain every ready candidate exactly once")
    db.save_delivery_order(playlist_name, requested)
    return {"success": True, "playlist_name": playlist_name, "candidate_ids": requested}


@app.delete("/api/publication/delivery/items/{candidate_id}")
def exclude_delivery_song(
    candidate_id: int,
    playlist_name: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    db = get_db()
    pl_name = (playlist_name or "").strip() or generate_default_weekly_playlist_name()
    cand = db.get_candidate_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if db.is_candidate_published_for_playlist(pl_name, candidate_id):
        raise HTTPException(status_code=400, detail="Cannot exclude already published song from this delivery")
    db.exclude_delivery_candidate(pl_name, candidate_id)
    return {"success": True, "playlist_name": pl_name, "candidate_id": candidate_id}


@app.post("/api/publication/delivery/items/{candidate_id}/restore")
def restore_delivery_song(
    candidate_id: int,
    playlist_name: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    db = get_db()
    pl_name = (playlist_name or "").strip() or generate_default_weekly_playlist_name()
    cand = db.get_candidate_by_id(candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.include_delivery_candidate(pl_name, candidate_id)
    return {"success": True, "playlist_name": pl_name, "candidate_id": candidate_id}


@app.post("/api/publication/export")
def publication_export() -> Dict[str, Any]:
    db = get_db()
    preview = _publication_preview(db)
    approved_raw = _reviewable(db.get_candidates(status="approved", limit=100000))
    approved = deduplicate_candidates(approved_raw, all_candidates=approved_raw)
    output = PROJECT_ROOT / "output" / "weekly_release.json"
    payload = {
        "playlist_name": preview["playlist_name"],
        "approved_count": len(approved),
        "ready_to_publish_count": preview["ready_count"],
        "songs": [_serialize_candidate(row, {get_song_dedup_key(row): [row]}, get_active_preference_model(db)) for row in approved],
        "publication_preview": preview,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "path": str(output), "approved_count": len(approved)}


@app.post("/api/publication/publish")
def publication_publish(payload: PublishRequest) -> Dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required")
    db = get_db()
    playlist_name = payload.playlist_name.strip() or generate_default_weekly_playlist_name()
    if payload.ordered_candidate_ids:
        db.save_delivery_order(playlist_name, payload.ordered_candidate_ids)
    preview = _publication_preview(db)
    unresolved = len(preview["ambiguous_items"]) + len(preview["unmatched_items"])
    if unresolved and not payload.allow_partial:
        raise HTTPException(status_code=409, detail=f"{unresolved} approved songs are unresolved; partial publishing is disabled")
    publisher = NetEasePublisher(base_url="http://127.0.0.1:3000", db=db)
    try:
        result = publisher.publish_approved(
            playlist_name=playlist_name,
            cookie_file=str(PROJECT_ROOT / "cookie.txt"),
            dry_run=False,
            ordered_candidate_ids=payload.ordered_candidate_ids or preview["ordered_candidate_ids"],
            publish_candidate_ids=preview["ordered_candidate_ids"],
        )
    except LoginRequiredError as exc:
        raise HTTPException(status_code=401, detail=f"网易云登录已失效：{exc}") from exc
    except PublishError as exc:
        raise HTTPException(status_code=502, detail=f"网易云发布失败：{exc}") from exc
    if payload.start_video_workflow and result.get("status") == "success":
        ordered_track_ids = []
        seen = set()
        for item in result.get("resolved_items") or []:
            track_id = str(item.get("netease_track_id") or "")
            if item.get("is_resolved") and track_id and track_id not in seen:
                seen.add(track_id); ordered_track_ids.append(track_id)
        result["video_workflow"] = start_video_workflow(
            db, result["publication_id"], result["playlist_id"], result["playlist_url"], ordered_track_ids,
        )
    return result


def _get_validated_job_dir(job_id: str) -> Path:
    if not job_id or not re.match(r"^video_[a-zA-Z0-9_-]+$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID format")
    job = get_db().get_video_workflow_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Video workflow job not found")
    base_dir = (PROJECT_ROOT / "output" / "video_jobs").resolve()
    job_dir = (base_dir / job_id).resolve()
    if not str(job_dir).startswith(str(base_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
    return job_dir


@app.get("/api/video-workflow/{job_id}/publish-copy")
def get_publish_copy(job_id: str) -> Dict[str, Any]:
    job_dir = _get_validated_job_dir(job_id)
    auto_path = job_dir / "publish_copy.auto.md"
    editable_path = job_dir / "publish_copy.md"

    if not auto_path.is_file() and not editable_path.is_file():
        raise HTTPException(status_code=404, detail="Publish copy not available for this job")

    auto_text = auto_path.read_text(encoding="utf-8") if auto_path.is_file() else ""
    editable_text = editable_path.read_text(encoding="utf-8") if editable_path.is_file() else ""

    return {
        "job_id": job_id,
        "auto_text": auto_text,
        "editable_text": editable_text,
        "is_modified": auto_text != editable_text,
        "has_auto": auto_path.is_file(),
        "has_editable": editable_path.is_file(),
    }


@app.put("/api/video-workflow/{job_id}/publish-copy")
def update_publish_copy(job_id: str, payload: PublishCopyUpdateRequest) -> Dict[str, Any]:
    job_dir = _get_validated_job_dir(job_id)
    editable_path = job_dir / "publish_copy.md"

    from song_discovery.publish_copy import _atomic_write_text
    _atomic_write_text(editable_path, payload.text)

    auto_path = job_dir / "publish_copy.auto.md"
    auto_text = auto_path.read_text(encoding="utf-8") if auto_path.is_file() else ""

    return {
        "success": True,
        "job_id": job_id,
        "text": payload.text,
        "is_modified": auto_text != payload.text,
    }


@app.post("/api/video-workflow/{job_id}/publish-copy/reset")
def reset_publish_copy(job_id: str) -> Dict[str, Any]:
    job_dir = _get_validated_job_dir(job_id)
    auto_path = job_dir / "publish_copy.auto.md"
    editable_path = job_dir / "publish_copy.md"

    if not auto_path.is_file():
        raise HTTPException(status_code=404, detail="Auto draft not found to reset from")

    auto_text = auto_path.read_text(encoding="utf-8")
    from song_discovery.publish_copy import _atomic_write_text
    _atomic_write_text(editable_path, auto_text)

    return {
        "success": True,
        "job_id": job_id,
        "text": auto_text,
        "is_modified": False,
    }


@app.get("/api/video-workflow/{job_id}/artifacts/{artifact_key}")
def video_workflow_artifact(job_id: str, artifact_key: str) -> FileResponse:
    job_dir = _get_validated_job_dir(job_id)
    spec = VIDEO_ARTIFACTS.get(artifact_key)
    if not spec:
        raise HTTPException(status_code=404, detail="Unknown video artifact")
    filename, media_type = spec
    artifact = job_dir / filename
    if not artifact.is_file() or artifact.stat().st_size == 0:
        raise HTTPException(status_code=404, detail="Video artifact not available")
    return FileResponse(artifact, media_type=media_type, filename=filename)


@app.get("/api/learning/safety")
def learning_safety() -> Dict[str, Any]:
    db = get_db()
    all_rows = db.get_candidates(limit=100000)
    approved = [row for row in all_rows if row.get("review_status") == "approved"]
    approved_unique = deduplicate_candidates(approved, all_candidates=all_rows)
    with db.get_connection() as conn:
        from_machine = {
            int(row["candidate_id"])
            for row in conn.execute(
                "SELECT DISTINCT candidate_id FROM review_history WHERE previous_status='machine_filtered' AND new_status='approved'"
            ).fetchall()
        }
    missed_unique = sum(
        1 for row in approved_unique
        if any(int(candidate_id) in from_machine for candidate_id in row.get("raw_ids") or [row["id"]])
    )
    missed_rate = missed_unique / len(approved_unique) if approved_unique else 0.0
    feedbacks = db.get_feedbacks(limit=20000)
    candidate_model, training = PreferenceLearner().train_model(feedbacks)
    active = get_active_preference_model(db)
    candidate_metrics = training.get("metrics") or {}
    precision = float(candidate_metrics.get("eval_precision") or 0.0)
    recall = float(candidate_metrics.get("eval_recall") or 0.0)
    gates = {
        "historical_miss_gate": missed_rate <= 0.005,
        "candidate_quality_gate": recall >= 0.99 and precision >= 0.25,
        "sample_maturity_gate": len(feedbacks) >= 2500,
    }
    return {
        "verdict": "green" if all(gates.values()) else "locked",
        "mode": "active" if all(gates.values()) else "shadow_only",
        "auto_filter_enabled": all(gates.values()),
        "approved_unique": len(approved_unique),
        "approved_missed_by_old_filter": missed_unique,
        "historical_miss_rate": round(missed_rate, 4),
        "feedback_count": len(feedbacks),
        "feedback_target": 2500,
        "candidate_model": {
            "version": candidate_model.version_id,
            "recall": recall,
            "precision": precision,
            "activated": False,
        },
        "active_model": {
            "version": active.version_id,
            "recall": active.metrics.get("eval_recall"),
            "precision": active.metrics.get("eval_precision"),
        },
        "gates": gates,
    }


@app.get("/api/artist-knowledge")
def get_artist_knowledge_endpoint(
    artist: Optional[str] = Query(default=None, max_length=200),
    artist_name: Optional[str] = Query(default=None, max_length=200),
    auto_collect: bool = Query(default=False),
    force: bool = Query(default=False),
    song_title: Optional[str] = Query(default=""),
    album_title: Optional[str] = Query(default=""),
) -> Dict[str, Any]:
    target_name = (artist or artist_name or "").strip()
    if not target_name:
        raise HTTPException(status_code=422, detail="Artist name is required (use 'artist' or 'artist_name')")
    db = get_db()
    norm = normalize_artist_name(target_name)
    rec = db.get_artist_knowledge(norm)
    if rec:
        status = rec.get("status")
        if status in ("pending", "collecting"):
            return {"found": False, "status": "collecting", "artist_name": norm}
        if not force:
            if status == "completed":
                return {"found": True, "knowledge": rec}
            if status == "sparse":
                refresh_queued = False
                if auto_collect and artist_knowledge_needs_refresh(rec):
                    refresh_queued = get_artist_collector(db).enqueue_artist(
                        norm,
                        song_title=song_title or "",
                        album_title=album_title or "",
                        force=True,
                    )
                # Return the provisional record immediately so the UI can still
                # show what is known, while exposing whether a fresh multi-source
                # search has been queued in the background.
                return {"found": True, "knowledge": rec, "refresh_queued": refresh_queued}
            elif status == "failed":
                if auto_collect:
                    collector = get_artist_collector(db)
                    queued = collector.enqueue_artist(
                        norm,
                        song_title=song_title or "",
                        album_title=album_title or "",
                        force=True,
                    )
                    latest = db.get_artist_knowledge(norm)
                    is_collecting = queued or (latest and latest.get("status") in ("pending", "collecting"))
                    return {
                        "found": False,
                        "status": "collecting" if is_collecting else "pending",
                        "artist_name": norm,
                        "error": rec.get("error") or "Previous attempt failed, retrying",
                    }
                return {
                    "found": False,
                    "status": "failed",
                    "artist_name": norm,
                    "error": rec.get("error") or "Research failed",
                    "knowledge": rec,
                }

    collector = get_artist_collector(db)
    if collector.is_active_or_queued(norm):
        return {"found": False, "status": "collecting", "artist_name": norm}

    if auto_collect:
        queued = collector.enqueue_artist(
            norm,
            song_title=song_title or "",
            album_title=album_title or "",
            force=force,
        )
        latest = db.get_artist_knowledge(norm)
        is_collecting = queued or (latest and latest.get("status") in ("pending", "collecting"))
        return {"found": False, "status": "collecting" if is_collecting else "pending", "artist_name": norm}

    return {"found": False, "status": "not_found", "local_missing": True, "artist_name": norm}


@app.post("/api/artist-knowledge/batch")
def get_artist_knowledge_batch_endpoint(payload: ArtistKnowledgeBatchRequest) -> Dict[str, Any]:
    db = get_db()
    targets = payload.artists or payload.artist_names or []
    results = db.get_artist_knowledge_batch(targets)
    auto_col = payload.auto_collect if payload.auto_collect_missing is None else payload.auto_collect_missing
    if auto_col:
        collector = get_artist_collector(db)
        for name in targets:
            norm = normalize_artist_name(name)
            rec = results.get(name) or results.get(norm)
            if rec and rec.get("status") in ("pending", "collecting"):
                continue
            if not rec or rec.get("status") == "failed" or artist_knowledge_needs_refresh(rec):
                collector.enqueue_artist(
                    norm,
                    force=bool(rec and (rec.get("status") == "failed" or artist_knowledge_needs_refresh(rec))),
                )
    return {"results": results, "items": results}


@app.post("/api/artist-knowledge/collect")
def collect_artist_knowledge_endpoint(payload: ArtistKnowledgeCollectRequest) -> Dict[str, Any]:
    db = get_db()
    collector = get_artist_collector(db)
    queued = collector.enqueue_artist(
        artist_name=payload.artist_name,
        song_title=payload.song_title or "",
        album_title=payload.album_title or "",
        force=bool(payload.force),
    )
    return {"success": True, "queued": queued, "artist_name": payload.artist_name}


@app.on_event("startup")
def on_api_startup() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    def _bg_startup_sync():
        try:
            database = get_db()
            needed = database.get_artists_needing_backfill(limit=100, status_filter="active")
            if needed:
                col = get_artist_collector(database)
                for it in needed:
                    col.enqueue_artist(
                        artist_name=it["artist_name"],
                        song_title=it.get("song_title", ""),
                        album_title=it.get("album_title", ""),
                    )
        except Exception:
            pass

    threading.Thread(target=_bg_startup_sync, name="ReviewApiStartupSync", daemon=True).start()


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str) -> FileResponse:
    requested = (FRONTEND_DIST / path).resolve()
    if path and requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
        return FileResponse(requested)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend is not built. Run npm run build in review_workbench.")
