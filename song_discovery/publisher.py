"""NetEase Cloud Music Playlist Publisher with cross-platform track resolution and idempotency."""

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from song_discovery.cookie_loader import NetEaseAuth, load_netscape_cookie_header
from song_discovery.db import DiscoveryDB
from song_discovery.exceptions import LoginRequiredError, PlatformRequestError, PublishError
from song_discovery.http_client import HttpClient
from song_discovery.matcher import MatchResult, TrackMatcher, generate_search_queries
from song_discovery.review_helpers import deduplicate_candidates, is_incomplete_kkbox_candidate


class NetEasePublisher:
    """
    Resolves approved candidate tracks (direct NetEase IDs or matched QQ/KKBOX tracks),
    creates/updates NetEase playlists, and records publication history idempotently.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        db: Optional[DiscoveryDB] = None,
        db_path: str = "output/discovery.db",
        http_client: Optional[HttpClient] = None,
        matcher: Optional[TrackMatcher] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.db = db or DiscoveryDB(db_path=db_path)
        self.http_client = http_client or HttpClient()
        self.matcher = matcher or TrackMatcher()
        self.auth = NetEaseAuth(base_url=self.base_url, http_client=self.http_client)

    def _get_auth_headers_and_params(self, cookie_file: Optional[str], cookie_header: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
        cookie = cookie_header or (load_netscape_cookie_header(cookie_file) if cookie_file else "")
        headers = {}
        params = {}
        if cookie:
            headers["Cookie"] = cookie
            params["cookie"] = cookie
        return headers, params

    @staticmethod
    def _api_call_succeeded(data: Any) -> bool:
        """Accept direct and NeteaseCloudMusicApi wrapped success payloads.

        Some mutation endpoints return ``{"code": 200}``, while others wrap
        the upstream payload as ``{"status": 200, "body": {"code": 200}}``.
        When a body code exists it is authoritative; an outer HTTP status must
        not hide an operation-level error.
        """
        if not isinstance(data, dict):
            return False
        body = data.get("body")
        if isinstance(body, dict) and "code" in body:
            return body.get("code") == 200
        if "code" in data:
            return data.get("code") == 200
        return data.get("status") == 200

    @staticmethod
    def _api_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        body = data.get("body")
        return body if isinstance(body, dict) else data

    @staticmethod
    def weekly_playlist_name(today: Optional[date] = None) -> str:
        current = today or date.today()
        iso_year, iso_week, _ = current.isocalendar()
        return f"华语新歌周刊 {iso_year}-W{iso_week:02d}"

    def search_netease_songs(self, keywords: str, limit: int = 5, cookie_file: Optional[str] = None, cookie_header: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search NetEase songs via /cloudsearch or fallback to /search."""
        headers, params = self._get_auth_headers_and_params(cookie_file, cookie_header)
        params.update({"keywords": keywords, "type": 1, "limit": limit})

        url = f"{self.base_url}/cloudsearch"
        try:
            resp = self.http_client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            songs = data.get("result", {}).get("songs") or []
            if songs:
                return songs
        except Exception:
            pass

        # Fallback to standard /search
        url_std = f"{self.base_url}/search"
        try:
            resp = self.http_client.get(url_std, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("songs") or []
        except Exception as exc:
            return []

    def resolve_candidate(
        self,
        candidate: Dict[str, Any],
        cookie_file: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve candidate to a NetEase track ID:
        - Native NetEase candidate: uses native ID directly.
        - QQ / KKBOX candidate: queries NetEase catalog and applies conservative Matcher.
        """
        platform = candidate.get("platform", "")
        orig_id = candidate.get("track_source_id", "")
        track_title = candidate.get("track_title", "")
        artist_names = candidate.get("artist_names", "")
        duration_ms = candidate.get("duration_ms")
        source_release_date = str(candidate.get("release_date") or "")

        manual_override = self.db.get_manual_match_override(int(candidate["id"]))
        if manual_override:
            return {
                "candidate_id": candidate["id"],
                "platform": platform,
                "original_track_id": orig_id,
                "netease_track_id": str(manual_override["netease_track_id"]),
                "track_title": track_title,
                "artist_names": artist_names,
                "matched_title": manual_override.get("matched_title") or track_title,
                "matched_artists": manual_override.get("matched_artists") or artist_names,
                "match_status": "matched",
                "match_confidence": 1.0,
                "match_details": {
                    "type": "manual_override",
                    "notes": manual_override.get("notes") or "",
                },
                "source_release_date": source_release_date,
                "target_release_date": manual_override.get("target_release_date") or "",
                "is_resolved": True,
            }

        # 1. Native NetEase track
        if platform == "netease":
            return {
                "candidate_id": candidate["id"],
                "platform": platform,
                "original_track_id": orig_id,
                "netease_track_id": orig_id,
                "track_title": track_title,
                "artist_names": artist_names,
                "match_status": "direct",
                "match_confidence": 1.0,
                "match_details": {"type": "native_netease_id"},
                "source_release_date": source_release_date,
                "target_release_date": source_release_date,
                "is_resolved": True,
            }

        # 2. External platform (QQ, KKBOX): Conservative Search & Match
        search_results: List[Dict[str, Any]] = []
        seen_result_ids: Set[str] = set()
        attempted_queries: List[str] = []
        for query in generate_search_queries(track_title, artist_names):
            attempted_queries.append(query)
            for song in self.search_netease_songs(
                keywords=query,
                limit=8,
                cookie_file=cookie_file,
                cookie_header=cookie_header,
            ):
                song_id = str(song.get("id") or "")
                if song_id and song_id not in seen_result_ids:
                    seen_result_ids.add(song_id)
                    search_results.append(song)
            interim = self.matcher.match(
                target_title=track_title,
                target_artists=artist_names,
                target_duration_ms=duration_ms,
                search_candidates=search_results,
            )
            # Exact, version-consistent results are safe to short-circuit. Lower
            # confidence and ambiguous results continue through broader queries.
            if interim.status == "matched" and interim.confidence >= 0.95:
                break

        match_res: MatchResult = self.matcher.match(
            target_title=track_title,
            target_artists=artist_names,
            target_duration_ms=duration_ms,
            search_candidates=search_results,
        )

        is_resolved = bool(match_res.status == "matched" and match_res.netease_track_id)
        top_raw = (match_res.details.get("top_candidate") or {}).get("raw") or {}
        publish_time = top_raw.get("publishTime") or 0
        target_release_date = ""
        if publish_time:
            target_release_date = datetime.fromtimestamp(float(publish_time) / 1000.0, tz=timezone.utc).date().isoformat()

        return {
            "candidate_id": candidate["id"],
            "platform": platform,
            "original_track_id": orig_id,
            "netease_track_id": match_res.netease_track_id,
            "track_title": track_title,
            "artist_names": artist_names,
            "matched_title": match_res.matched_title,
            "matched_artists": match_res.matched_artists,
            "match_status": match_res.status,
            "match_confidence": match_res.confidence,
            "match_details": {
                "reasons": match_res.reasons,
                "details": match_res.details,
                "attempted_queries": attempted_queries,
            },
            "source_release_date": source_release_date,
            "target_release_date": target_release_date,
            "is_resolved": is_resolved,
        }

    def create_playlist(
        self,
        name: str,
        privacy: int = 0,
        cookie_file: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new playlist via /playlist/create."""
        headers, params = self._get_auth_headers_and_params(cookie_file, cookie_header)
        params.update({"name": name, "privacy": privacy})

        url = f"{self.base_url}/playlist/create"
        resp = self.http_client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if not self._api_call_succeeded(data):
            raise PublishError(f"Failed to create playlist: {data}")

        payload = self._api_payload(data)
        raw_playlist_id = payload.get("id") or payload.get("playlist", {}).get("id")
        if raw_playlist_id is None:
            raise PublishError("NetEase reported playlist creation success but returned no playlist ID.")
        playlist_id = str(raw_playlist_id)
        return {
            "playlist_id": playlist_id,
            "name": name,
            "url": f"https://music.163.com/#/playlist?id={playlist_id}",
            "raw": data,
        }

    def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        cookie_file: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add track IDs to playlist via /playlist/tracks?op=add."""
        if not track_ids:
            return {"count": 0, "status": "no_tracks"}

        headers, params = self._get_auth_headers_and_params(cookie_file, cookie_header)
        # NetEase prepends a batch in reverse order. Reverse the payload so the
        # visible playlist order matches the editor's top-to-bottom sequence.
        tracks_str = ",".join(reversed(track_ids))
        params.update({"op": "add", "pid": playlist_id, "tracks": tracks_str})

        url = f"{self.base_url}/playlist/tracks"
        resp = self.http_client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if not self._api_call_succeeded(data):
            raise PublishError(f"Failed to add tracks to playlist: {data}")

        return {
            "count": len(track_ids),
            "playlist_id": playlist_id,
            "status": "success",
            "raw": data,
        }

    def remove_tracks_from_playlist(self, playlist_id: str, track_ids: List[str], cookie_file: Optional[str] = None, cookie_header: Optional[str] = None) -> Dict[str, Any]:
        if not track_ids:
            return {"count": 0, "status": "no_tracks"}
        headers, params = self._get_auth_headers_and_params(cookie_file, cookie_header)
        params.update({"op": "del", "pid": playlist_id, "tracks": ",".join(track_ids)})
        resp = self.http_client.get(f"{self.base_url}/playlist/tracks", params=params, headers=headers)
        resp.raise_for_status(); data = resp.json()
        if not self._api_call_succeeded(data):
            raise PublishError(f"Failed to remove tracks from playlist: {data}")
        return {"count": len(track_ids), "status": "success", "raw": data}

    def update_playlist_order(
        self,
        playlist_id: str,
        track_ids: List[str],
        cookie_file: Optional[str] = None,
        cookie_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Force the final visible playlist order after NetEase batch insertion."""
        if not track_ids:
            return {"count": 0, "status": "no_tracks"}
        headers, params = self._get_auth_headers_and_params(cookie_file, cookie_header)
        params.update({"pid": playlist_id, "ids": json.dumps([str(value) for value in track_ids])})
        url = f"{self.base_url}/song/order/update"
        resp = self.http_client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if not self._api_call_succeeded(data):
            raise PublishError(f"Failed to update playlist order: {data}")
        return {"count": len(track_ids), "status": "success", "raw": data}

    def publish_approved(
        self,
        playlist_name: Optional[str] = None,
        playlist_id: Optional[str] = None,
        cookie_file: Optional[str] = None,
        cookie_header: Optional[str] = None,
        dry_run: bool = False,
        ordered_candidate_ids: Optional[List[int]] = None,
        publish_candidate_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Main publishing workflow:
        1. Query all approved candidates from SQLite.
        2. Resolve each candidate to NetEase track ID (direct or conservative match).
        3. Deduplicate against already-published tracks for the target playlist.
        4. In dry-run mode: return plan summary without mutating NetEase.
        5. In live mode: require login, create/use playlist, add tracks, persist publication records.
        """
        playlist_name = playlist_name or self.weekly_playlist_name()

        # Fetch approved candidates before authentication so an empty review
        # batch becomes a harmless no-op instead of prompting for login.
        approved_raw = [
            candidate for candidate in self.db.get_candidates(status="approved", limit=5000)
            if not is_incomplete_kkbox_candidate(candidate)
        ]
        approved_candidates = deduplicate_candidates(approved_raw, all_candidates=approved_raw)
        if publish_candidate_ids is not None:
            allowed = {int(value) for value in publish_candidate_ids}
            approved_candidates = [row for row in approved_candidates if int(row["id"]) in allowed]
        if ordered_candidate_ids:
            positions = {int(candidate_id): index for index, candidate_id in enumerate(ordered_candidate_ids)}
            approved_candidates.sort(key=lambda row: (positions.get(int(row["id"]), len(positions)), int(row["id"])))
        if not approved_candidates:
            return {
                "dry_run": dry_run,
                "status": "no_approved_candidates",
                "playlist_name": playlist_name,
                "playlist_id": playlist_id,
                "total_approved": 0,
                "direct_netease_count": 0,
                "matched_count": 0,
                "ambiguous_count": 0,
                "unmatched_count": 0,
                "ready_to_add_count": 0,
                "added_count": 0,
                "resolved_items": [],
            }

        # Validate login session if live publishing
        login_profile = None
        if not dry_run:
            login_profile = self.auth.require_login(cookie_file=cookie_file, cookie_header=cookie_header)

        # 2. Resolve candidates
        resolved_items = []
        direct_cnt = 0
        matched_cnt = 0
        ambiguous_cnt = 0
        unmatched_cnt = 0

        for cand in approved_candidates:
            res = self.resolve_candidate(cand, cookie_file=cookie_file, cookie_header=cookie_header)
            resolved_items.append(res)
            st = res["match_status"]
            if st == "direct":
                direct_cnt += 1
            elif st == "matched":
                matched_cnt += 1
            elif st == "ambiguous":
                ambiguous_cnt += 1
            elif st == "unmatched":
                unmatched_cnt += 1

        # Tracks with resolved NetEase track ID
        resolvable_tracks = [item for item in resolved_items if item["is_resolved"] and item.get("netease_track_id")]

        # Reuse a prior successful publication with the same weekly name.
        if not playlist_id:
            prior_publication = self.db.get_latest_publication_by_name(playlist_name)
            if prior_publication:
                playlist_id = prior_publication["playlist_id"]

        # Check existing tracks in target playlist to ensure idempotency
        already_published_ids: Set[str] = set()
        if playlist_id:
            already_published_ids = self.db.get_already_published_track_ids(playlist_id)

        tracks_to_add = []
        planned_ids: Set[str] = set()
        for item in resolvable_tracks:
            track_id = item["netease_track_id"]
            if track_id not in already_published_ids and track_id not in planned_ids:
                tracks_to_add.append(track_id)
                planned_ids.add(track_id)

        # 3. Dry-run summary
        if dry_run:
            return {
                "dry_run": True,
                "status": "planned",
                "playlist_name": playlist_name,
                "playlist_id": playlist_id or "[WILL_CREATE_NEW]",
                "total_approved": len(approved_candidates),
                "direct_netease_count": direct_cnt,
                "matched_count": matched_cnt,
                "ambiguous_count": ambiguous_cnt,
                "unmatched_count": unmatched_cnt,
                "ready_to_add_count": len(tracks_to_add),
                "already_in_playlist_count": len(resolvable_tracks) - len(tracks_to_add),
                "resolved_items": resolved_items,
            }

        # 4. Live publishing
        final_pid = playlist_id
        playlist_url = ""
        if not final_pid:
            pl_info = self.create_playlist(
                name=playlist_name,
                cookie_file=cookie_file,
                cookie_header=cookie_header,
            )
            final_pid = pl_info["playlist_id"]
            playlist_url = pl_info["url"]
        else:
            playlist_url = f"https://music.163.com/#/playlist?id={final_pid}"

        # Add tracks to playlist
        if tracks_to_add:
            self.add_tracks_to_playlist(
                playlist_id=final_pid,
                track_ids=tracks_to_add,
                cookie_file=cookie_file,
                cookie_header=cookie_header,
            )

        desired_track_ids = []
        desired_seen: Set[str] = set()
        for item in resolvable_tracks:
            track_id = str(item["netease_track_id"])
            if track_id not in desired_seen:
                desired_seen.add(track_id)
                desired_track_ids.append(track_id)
        tracks_to_remove = sorted(already_published_ids - set(desired_track_ids))
        if tracks_to_remove:
            self.remove_tracks_from_playlist(final_pid, tracks_to_remove, cookie_file, cookie_header)
        self.update_playlist_order(
            playlist_id=final_pid,
            track_ids=desired_track_ids,
            cookie_file=cookie_file,
            cookie_header=cookie_header,
        )

        # Record publication in SQLite
        pub_id = self.db.create_publication(
            playlist_id=final_pid,
            playlist_name=playlist_name,
            status="success",
            total_tracks=len(tracks_to_add),
            notes=f"Approved: {len(approved_candidates)}, Added: {len(tracks_to_add)}, Direct: {direct_cnt}, Matched: {matched_cnt}",
        )

        # Record individual published track entries
        pub_track_records = []
        for item in resolved_items:
            nid = item.get("netease_track_id")
            was_added = bool(nid and nid in tracks_to_add)
            pub_track_records.append({
                "candidate_id": item["candidate_id"],
                "platform": item["platform"],
                "original_track_id": item["original_track_id"],
                "netease_track_id": nid,
                "match_status": item["match_status"],
                "match_confidence": item.get("match_confidence", 0.0),
                "match_details": item.get("match_details"),
                "added_to_playlist": was_added,
            })
        self.db.record_published_tracks(pub_id, pub_track_records)

        return {
            "dry_run": False,
            "status": "success",
            "publication_id": pub_id,
            "playlist_id": final_pid,
            "playlist_name": playlist_name,
            "playlist_url": playlist_url,
            "publisher_user": login_profile.get("nickname") if login_profile else None,
            "total_approved": len(approved_candidates),
            "direct_netease_count": direct_cnt,
            "matched_count": matched_cnt,
            "ambiguous_count": ambiguous_cnt,
            "unmatched_count": unmatched_cnt,
            "added_count": len(tracks_to_add),
            "resolved_items": resolved_items,
        }
