"""Orchestration service connecting collectors, selection rules, scoring, and SQLite storage."""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("song_discovery.orchestrator")

from song_discovery.collectors.kkbox import KKBOXCollector
from song_discovery.collectors.netease import NetEaseCollector
from song_discovery.collectors.qq import QQMusicCollector
from song_discovery.db import DiscoveryDB
from song_discovery.exceptions import EmptyReleaseError, ServiceUnavailableError
from song_discovery.models import Platform, Release, Track
from song_discovery.scorer import RelevanceScorer
from song_discovery.selection import get_selection_rule_label, select_track_from_release


class DiscoveryOrchestrator:
    """Orchestrates multi-platform release discovery, track selection, scoring, and database persistence."""

    def __init__(self, db: Optional[DiscoveryDB] = None, db_path: str = "output/discovery.db"):
        self.db = db or DiscoveryDB(db_path=db_path)
        self.scorer = RelevanceScorer()

    def run_discovery(
        self,
        platform: str = "all",
        limit: int = 10,
        netease_url: Optional[str] = None,
        netease_pages: int = 15,
        netease_page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Execute discovery across platforms, apply selection rule, score, and persist.
        NetEase defaults to a full 15-page window (20 releases/page = up to 300 releases).
        QQ limits remain independent based on limit.
        KKBOX uses official API if credentials exist, or awaits Chrome extension bridge ingestion.
        """
        notes_str = f"limit={limit}, netease_pages={netease_pages}x{netease_page_size}"
        run_id = self.db.start_run(platform=platform, notes=notes_str)
        summary: Dict[str, Any] = {
            "run_id": run_id,
            "platform": platform,
            "total_releases": 0,
            "total_candidates": 0,
            "platforms": {},
        }

        platforms_to_run = (
            [Platform.QQ.value, Platform.NETEASE.value, Platform.KKBOX.value]
            if platform == "all"
            else [platform]
        )

        overall_status = "success"
        has_errors = False
        has_warnings = False
        all_candidates_added = 0
        all_releases_added = 0

        for p in platforms_to_run:
            p_res = self._process_platform(
                p,
                limit=limit,
                netease_url=netease_url,
                netease_pages=netease_pages,
                netease_page_size=netease_page_size,
            )
            summary["platforms"][p] = p_res
            all_releases_added += p_res.get("releases_count", 0)
            all_candidates_added += p_res.get("candidates_count", 0)
            st = p_res.get("status")
            if st == "failed":
                has_errors = True
            elif st == "success_with_warnings":
                has_warnings = True

        if has_errors and all_releases_added > 0:
            overall_status = "partial"
        elif has_errors and all_releases_added == 0:
            overall_status = "failed"
        elif has_warnings:
            overall_status = "success_with_warnings"
        else:
            overall_status = "success"

        summary["total_releases"] = all_releases_added
        summary["total_candidates"] = all_candidates_added

        self.db.finish_run(
            run_id=run_id,
            status=overall_status,
            collected_count=all_releases_added,
            candidates_count=all_candidates_added,
            notes=f"Processed platforms: {','.join(platforms_to_run)}",
        )

        return summary

    def _process_platform(
        self,
        platform_name: str,
        limit: int,
        netease_url: Optional[str],
        netease_pages: int = 15,
        netease_page_size: int = 20,
    ) -> Dict[str, Any]:
        releases: List[Release] = []
        status = "success"
        err_msg = ""
        reason_msg = ""
        base_url_info = None
        warnings: List[Dict[str, Any]] = []
        source_mode = None

        try:
            if platform_name == Platform.QQ.value:
                collector = QQMusicCollector()
                releases = collector.collect_new_releases(limit=limit)
                source_mode = "official_u_musicu"
            elif platform_name == Platform.NETEASE.value:
                collector = NetEaseCollector(base_url=netease_url)
                base_url_info = collector.base_url
                releases = collector.collect_new_releases(
                    pages=netease_pages,
                    page_size=netease_page_size,
                )
                warnings = getattr(collector, "warnings", [])
                source_mode = "local_enhanced_api"
            elif platform_name == Platform.KKBOX.value:
                collector = KKBOXCollector()
                if not collector.has_credentials:
                    status = "skipped"
                    reason_msg = "KKBOX API credentials not configured. Use Chrome extension + bridge (127.0.0.1:8765) for web ingestion."
                    source_mode = "awaiting_extension_bridge"
                else:
                    releases = collector.collect_new_releases(limit=limit)
                    source_mode = "official_open_api"
        except ServiceUnavailableError as exc:
            status = "failed"
            err_msg = str(exc)
        except Exception as exc:
            status = "failed"
            err_msg = str(exc)

        if status == "skipped":
            return {
                "platform": platform_name,
                "status": "skipped",
                "reason": reason_msg,
                "source_mode": source_mode,
                "releases_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "warning_count": 0,
                "warnings": [],
            }

        if status == "failed":
            res_dict = {
                "platform": platform_name,
                "status": status,
                "error": err_msg,
                "releases_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "warning_count": len(warnings),
                "warnings": warnings,
            }
            if source_mode:
                res_dict["source_mode"] = source_mode
            if base_url_info:
                res_dict["base_url"] = base_url_info
            return res_dict

        # If no releases were fetched but warnings exist
        if len(releases) == 0 and len(warnings) > 0:
            res_dict = {
                "platform": platform_name,
                "status": "failed",
                "error": f"Failed to retrieve items across all {len(warnings)} attempts/categories.",
                "releases_count": 0,
                "candidates_count": 0,
                "candidates": [],
                "warning_count": len(warnings),
                "warnings": warnings,
            }
            if source_mode:
                res_dict["source_mode"] = source_mode
            if base_url_info:
                res_dict["base_url"] = base_url_info
            return res_dict

        # Set status based on warnings
        if len(warnings) > 0 and len(releases) > 0:
            status = "success_with_warnings"

        # Process releases & candidates
        saved_candidates = []
        for rel in releases:
            # 1. Upsert release into SQLite
            self.db.upsert_release(rel)

            # 2. Apply unified track selection rule
            try:
                target_track = select_track_from_release(rel)
                rule_desc = get_selection_rule_label(rel)
            except EmptyReleaseError:
                continue

            # 3. Score candidate
            scoring_res = self.scorer.score_candidate(target_track, release=rel)
            if not scoring_res.is_candidate:
                continue

            # 4. Upsert candidate into SQLite. Keep the first-pass route high
            # recall; the local artist-profile pass below is the only automatic
            # artist-identity demotion, and it never searches the network.
            cid, is_new = self.db.upsert_candidate(
                platform=rel.platform,
                release_source_id=rel.source_id,
                track_source_id=target_track.source_id,
                release_title=rel.title,
                track_title=target_track.title,
                artist_names=target_track.artist_names_str,
                release_type=rel.release_type,
                release_date=rel.release_date,
                duration_ms=target_track.duration_ms,
                track_number=target_track.track_number,
                release_url=rel.source_url,
                track_url=target_track.source_url,
                selection_rule=rule_desc,
                relevance_score=scoring_res.score,
                relevance_reasons=scoring_res.reasons,
                raw_metadata=target_track.raw_metadata,
                initial_review_status="pending",
            )
            profile_filter = self.db.apply_local_profile_filter_to_candidate(cid)

            if target_track.artist_names_str and not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    from song_discovery.artist_knowledge import get_artist_collector, split_artist_names
                    collector = get_artist_collector(self.db)
                    for artist_name in split_artist_names(target_track.artist_names_str):
                        collector.enqueue_artist(
                            artist_name=artist_name,
                            song_title=target_track.title,
                            album_title=rel.title,
                        )
                except Exception as exc:
                    logger.debug(f"Failed to enqueue artist collection: {exc}")

            saved_candidates.append({
                "candidate_id": cid,
                "is_new": is_new,
                "platform": rel.platform,
                "release_title": rel.title,
                "track_title": target_track.title,
                "artist_names": target_track.artist_names_str,
                "selection_rule": rule_desc,
                "score": scoring_res.score,
                "reasons": self.db.get_candidate_by_id(cid).get("relevance_reasons", scoring_res.reasons),
                "profile_filter": profile_filter.get("decision"),
            })

        res_dict = {
            "platform": platform_name,
            "status": status,
            "releases_count": len(releases),
            "candidates_count": len(saved_candidates),
            "candidates": saved_candidates,
            "warning_count": len(warnings),
            "warnings": warnings,
        }
        if source_mode:
            res_dict["source_mode"] = source_mode
        return res_dict

    def export_candidates(
        self,
        status: Optional[str] = "approved",
        output_file: str = "output/candidates_export.json",
    ) -> Dict[str, Any]:
        """Export candidates from SQLite to a JSON file."""
        candidates = self.db.export_candidates(status=status)
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "export_status_filter": status,
                    "count": len(candidates),
                    "candidates": candidates,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        return {
            "output_file": output_file,
            "count": len(candidates),
            "status": status,
        }
