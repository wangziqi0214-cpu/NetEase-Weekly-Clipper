"""NetEase Cloud Music Collector via local NeteaseCloudMusicApi / NeteaseCloudMusicApiEnhanced."""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from song_discovery.collectors.base import BaseCollector
from song_discovery.exceptions import PlatformRequestError, PlatformResponseError, ServiceUnavailableError
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track


class NetEaseCollector(BaseCollector):
    """
    NetEase Cloud Music collector interfacing with a local
    NeteaseCloudMusicApi / NeteaseCloudMusicApiEnhanced instance.
    Supports multi-page window collection (e.g. 15 pages * 20 releases) with deduplication,
    bounded retries with cache-busting on transient failures, and resilient error logging.
    """

    platform_name = Platform.NETEASE.value
    DEFAULT_BASE_URL = "http://localhost:3000"

    def __init__(self, base_url: Optional[str] = None, http_client: Optional[Any] = None):
        super().__init__(http_client=http_client)
        self.base_url = (
            base_url
            or os.environ.get("NETEASE_API_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.warnings: List[Dict[str, Any]] = []

    def fetch_new_album_page(
        self,
        area: str = "ZH",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """
        Call /album/new?area={area}&limit={limit}&offset={offset}.
        Returns (albums_list, total_reported).
        Does NOT swallow failures.
        """
        url = f"{self.base_url}/album/new"
        try:
            resp = self.http_client.get(url, params={"area": area, "limit": limit, "offset": offset})
        except ServiceUnavailableError as exc:
            raise ServiceUnavailableError(
                f"NetEase Cloud Music API service is unavailable at {self.base_url}. "
                f"Please ensure NeteaseCloudMusicApi/NeteaseCloudMusicApiEnhanced is running locally.",
                platform=self.platform_name,
                endpoint=url,
            ) from exc

        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict):
            raise PlatformResponseError(
                f"Invalid JSON response format from NetEase API at {url}",
                platform=self.platform_name,
            )

        if data.get("code") != 200:
            raise PlatformResponseError(
                f"NetEase API returned non-200 code: {data.get('code')}",
                platform=self.platform_name,
                raw_response=data,
            )

        albums = data.get("albums") or []
        total = data.get("total")
        return albums, int(total) if total is not None else None

    def fetch_new_albums(self, area: str = "ZH", limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Call /album/new?area=ZH&limit={limit}&offset={offset}.
        """
        albums, _ = self.fetch_new_album_page(area=area, limit=limit, offset=offset)
        return albums

    def fetch_album_detail(self, album_id: str, max_retries: int = 2) -> Dict[str, Any]:
        """
        Call /album?id={album_id} with up to 2 retries (total 3 attempts) for transient failures (e.g. 405, 5xx, timeouts).
        Applies a cache-busting timestamp parameter (_t) and backoff on retries.
        """
        url = f"{self.base_url}/album"
        last_exc: Optional[Exception] = None

        for attempt in range(1 + max_retries):
            params: Dict[str, Any] = {"id": album_id}
            if attempt > 0:
                params["_t"] = int(time.time() * 1000)
                time.sleep(0.05 * (2 ** (attempt - 1)))

            try:
                resp = self.http_client.get(url, params=params)
                # Retry on HTTP 405 (method not allowed / transient proxy glitch) or other 4xx/5xx
                if resp.status_code == 405:
                    raise PlatformRequestError(
                        f"Transient HTTP 405 on NetEase album detail for id {album_id}",
                        status_code=405,
                        platform=self.platform_name,
                    )

                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, dict) or data.get("code") != 200:
                    raise PlatformResponseError(
                        f"Failed to fetch NetEase album detail for id {album_id} (code={data.get('code') if isinstance(data, dict) else 'non-dict'})",
                        platform=self.platform_name,
                        raw_response=data if isinstance(data, dict) else None,
                    )

                return data

            except ServiceUnavailableError as exc:
                # Service level down
                last_exc = exc
                if attempt >= max_retries:
                    raise
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries:
                    break

        if last_exc:
            raise last_exc
        raise PlatformRequestError(f"Failed to fetch NetEase album detail for id {album_id} after {1 + max_retries} attempts.")

    def _parse_artists(self, raw_artists: Any) -> List[Artist]:
        artists = []
        if isinstance(raw_artists, list):
            for a in raw_artists:
                if isinstance(a, dict):
                    name = str(a.get("name", "")).strip()
                    aid = str(a.get("id", "")) if a.get("id") else None
                    if name:
                        artists.append(Artist(name=name, id=aid, raw_metadata=a))
                elif isinstance(a, str) and a.strip():
                    artists.append(Artist(name=a.strip()))
        elif isinstance(raw_artists, dict):
            name = str(raw_artists.get("name", "")).strip()
            aid = str(raw_artists.get("id", "")) if raw_artists.get("id") else None
            if name:
                artists.append(Artist(name=name, id=aid, raw_metadata=raw_artists))
        return artists

    def _format_date(self, timestamp_or_str: Any) -> str:
        if isinstance(timestamp_or_str, (int, float)) and timestamp_or_str > 0:
            ts = timestamp_or_str / 1000.0 if timestamp_or_str > 1e11 else float(timestamp_or_str)
            try:
                return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception:
                return str(timestamp_or_str)
        return str(timestamp_or_str or "")

    def _parse_release(self, raw_album: Dict[str, Any]) -> Release:
        album_id = str(raw_album.get("id", ""))
        name = str(raw_album.get("name", "")).strip()
        raw_art = raw_album.get("artists") or raw_album.get("artist") or []
        artists = self._parse_artists(raw_art)

        raw_type = str(raw_album.get("type", "")).lower()
        if "single" in raw_type or "单曲" in raw_type:
            rel_type = ReleaseType.SINGLE.value
        elif "ep" in raw_type:
            rel_type = ReleaseType.EP.value
        else:
            rel_type = ReleaseType.ALBUM.value

        pub_date = self._format_date(raw_album.get("publishTime") or raw_album.get("publish_time"))

        return Release(
            platform=self.platform_name,
            source_id=album_id,
            title=name,
            artists=artists,
            album_title=name,
            release_type=rel_type,
            release_date=pub_date,
            track_count=raw_album.get("size", 0),
            source_url=f"https://music.163.com/#/album?id={album_id}" if album_id else "",
            raw_metadata=raw_album,
        )

    def _parse_track(self, raw_song: Dict[str, Any], album: Release, index: int) -> Track:
        song_id = str(raw_song.get("id", ""))
        title = str(raw_song.get("name", "")).strip()
        raw_art = raw_song.get("ar") or raw_song.get("artists") or raw_song.get("artist") or []
        artists = self._parse_artists(raw_art) or album.artists
        duration_ms = raw_song.get("dt") or raw_song.get("duration")

        return Track(
            platform=self.platform_name,
            source_id=song_id,
            title=title,
            artists=artists,
            album_title=album.title,
            release_type=album.release_type,
            release_date=album.release_date,
            duration_ms=int(duration_ms) if duration_ms else None,
            track_number=index,
            source_url=f"https://music.163.com/#/song?id={song_id}" if song_id else "",
            raw_metadata=raw_song,
        )

    def collect_new_releases(
        self,
        limit: Optional[int] = None,
        area: str = "ZH",
        pages: Optional[int] = None,
        page_size: int = 20,
        **kwargs,
    ) -> List[Release]:
        """
        Collect new releases across the NetEase new-album window (default: 15 pages of 20 = 300 albums).
        Deduplicates album IDs across pages, retries transient album-detail failures, records structured warnings
        when single album details fail, and stops early on empty/short page or total exhaustion.

        Args:
            limit: Optional explicit item limit for backward compatibility.
            area: Release area (default: "ZH" for Chinese/Mandarin).
            pages: Number of pages to fetch (default: 15 when limit is None).
            page_size: Number of releases per page (default: 20).

        Returns:
            List of populated Release objects.
        """
        self.warnings = []

        if pages is not None:
            max_pages = pages
            cur_page_size = page_size
        elif limit is not None:
            if limit <= page_size:
                max_pages = 1
                cur_page_size = limit
            else:
                max_pages = (limit + page_size - 1) // page_size
                cur_page_size = page_size
        else:
            max_pages = 15
            cur_page_size = 20

        seen_album_ids: Set[str] = set()
        releases: List[Release] = []

        for page_idx in range(max_pages):
            offset = page_idx * cur_page_size
            raw_albums, total = self.fetch_new_album_page(area=area, limit=cur_page_size, offset=offset)

            if not raw_albums:
                break

            for raw_alb in raw_albums:
                alb_id = str(raw_alb.get("id", ""))
                if not alb_id or alb_id in seen_album_ids:
                    continue
                seen_album_ids.add(alb_id)

                release = self._parse_release(raw_alb)
                if not release.source_id:
                    continue

                try:
                    detail = self.fetch_album_detail(release.source_id)
                except Exception as exc:
                    self.warnings.append({
                        "album_id": release.source_id,
                        "album_title": release.title,
                        "error": str(exc),
                        "stage": "album_detail",
                    })
                    continue

                raw_songs = detail.get("songs") or []
                tracks: List[Track] = []
                for idx, song_data in enumerate(raw_songs, start=1):
                    track = self._parse_track(song_data, album=release, index=idx)
                    tracks.append(track)

                release.tracks = tracks
                release.track_count = len(tracks)
                releases.append(release)

                if limit is not None and len(releases) >= limit and pages is None:
                    return releases

            # Early stop condition 1: Short page
            if len(raw_albums) < cur_page_size:
                break

            # Early stop condition 2: Total reported exhaustion
            if total is not None and (offset + len(raw_albums)) >= total:
                break

        return releases
