"""KKBOX Collector interfacing with official KKBOX Open API v1.1."""

import os
from typing import Any, Dict, List, Optional
from song_discovery.collectors.base import BaseCollector
from song_discovery.exceptions import AuthenticationError, PlatformRequestError, PlatformResponseError
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track


class KKBOXCollector(BaseCollector):
    """
    KKBOX collector interfacing with KKBOX Open API v1.1 via OAuth client_credentials flow.
    Supports territory=TW and territory=HK.
    When API credentials are not configured, collection is skipped in favor of the local Chrome extension bridge.
    """

    platform_name = Platform.KKBOX.value
    TOKEN_ENDPOINT = "https://account.kkbox.com/oauth2/token"
    API_BASE = "https://api.kkbox.com/v1.1"
    TARGET_NEW_RELEASE_CATEGORY_IDS = (
        "GtN6qomUYBvqtWeRP8",
        "1ZQwmFTaLE4p7BG-Ua",
        "Okrqvkq-3_sNf4ForP",
    )

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        http_client: Optional[Any] = None,
    ):
        super().__init__(http_client=http_client)
        self.client_id = client_id or os.environ.get("KKBOX_CLIENT_ID") or os.environ.get("CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("KKBOX_CLIENT_SECRET") or os.environ.get("CLIENT_SECRET")
        self._access_token: Optional[str] = None
        self.source_mode: str = "official_open_api"

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authenticate(self) -> str:
        """Authenticate with KKBOX OAuth2 server using client_credentials."""
        if not self.has_credentials:
            raise AuthenticationError(
                "KKBOX CLIENT_ID or CLIENT_SECRET is not configured.",
                platform=self.platform_name,
            )

        if self._access_token:
            return self._access_token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        resp = self.http_client.post(self.TOKEN_ENDPOINT, data=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        token = data.get("access_token")
        if not token:
            raise AuthenticationError(
                f"Failed to obtain access token from KKBOX: {data}",
                platform=self.platform_name,
            )
        self._access_token = token
        return token

    def _get_auth_headers(self) -> Dict[str, str]:
        token = self.authenticate()
        return {"Authorization": f"Bearer {token}"}

    def fetch_new_release_categories(self, territory: str = "TW") -> List[Dict[str, Any]]:
        """
        Fetch new release categories for the given territory (e.g., TW or HK).
        Outputs discovered category names dynamically at runtime.
        """
        url = f"{self.API_BASE}/new-release-categories"
        headers = self._get_auth_headers()
        resp = self.http_client.get(url, params={"territory": territory}, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        categories = data.get("data") or []
        for cat in categories:
            cat_id = cat.get("id")
            title = cat.get("title")
            print(f"[KKBOX] Discovered Category: '{title}' (id={cat_id}, territory={territory})")

        return categories

    def fetch_category_albums(self, category_id: str, territory: str = "TW", limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch albums under a specific new-release category."""
        url = f"{self.API_BASE}/new-release-categories/{category_id}/albums"
        headers = self._get_auth_headers()
        resp = self.http_client.get(
            url,
            params={"territory": territory, "limit": limit},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or []

    def fetch_album_tracks(self, album_id: str, territory: str = "TW") -> List[Dict[str, Any]]:
        """Fetch all tracks for a specific album."""
        url = f"{self.API_BASE}/albums/{album_id}/tracks"
        headers = self._get_auth_headers()
        resp = self.http_client.get(
            url,
            params={"territory": territory, "limit": 50},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data") or []

    def _parse_artists(self, raw_artist: Any) -> List[Artist]:
        artists = []
        if isinstance(raw_artist, dict):
            name = str(raw_artist.get("name", "")).strip()
            aid = str(raw_artist.get("id", "")) if raw_artist.get("id") else None
            if name:
                artists.append(Artist(name=name, id=aid, raw_metadata=raw_artist))
        elif isinstance(raw_artist, list):
            for a in raw_artist:
                artists.extend(self._parse_artists(a))
        elif isinstance(raw_artist, str) and raw_artist.strip():
            artists.append(Artist(name=raw_artist.strip()))
        return artists

    def _parse_release(self, raw_album: Dict[str, Any], territory: str) -> Release:
        album_id = str(raw_album.get("id", ""))
        name = str(raw_album.get("name", "")).strip()
        artists = self._parse_artists(raw_album.get("artist"))
        pub_date = str(raw_album.get("release_date", "")).strip()
        url = raw_album.get("url") or f"https://www.kkbox.com/{territory.lower()}/album/{album_id}"

        return Release(
            platform=self.platform_name,
            source_id=album_id,
            title=name,
            artists=artists,
            album_title=name,
            release_type=ReleaseType.ALBUM.value,
            release_date=pub_date,
            source_url=url,
            raw_metadata=raw_album,
        )

    def _parse_track(self, raw_track: Dict[str, Any], album: Release, index: int, territory: str) -> Track:
        track_id = str(raw_track.get("id", ""))
        name = str(raw_track.get("name", "")).strip()
        artists = self._parse_artists(raw_track.get("artist")) or album.artists
        duration_ms = raw_track.get("duration")
        track_number = raw_track.get("track_number") or index
        url = raw_track.get("url") or f"https://www.kkbox.com/{territory.lower()}/song/{track_id}"

        return Track(
            platform=self.platform_name,
            source_id=track_id,
            title=name,
            artists=artists,
            album_title=album.title,
            release_type=album.release_type,
            release_date=album.release_date,
            duration_ms=int(duration_ms) if duration_ms else None,
            track_number=int(track_number) if track_number else index,
            source_url=url,
            raw_metadata=raw_track,
        )

    def collect_new_releases(
        self,
        limit: int = 10,
        territories: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Release]:
        """
        Collect new releases from the three user-approved categories via the
        Official Open API. The Chrome sidecar covers the same web categories as
        a fallback and data-difference safety net.
        If credentials are not configured, returns an empty list.
        """
        if not self.has_credentials:
            print("[KKBOX] SKIP: CLIENT_ID / CLIENT_SECRET not configured. Awaiting Chrome extension bridge ingestion.")
            return []

        self.source_mode = "official_open_api"
        if territories is None:
            territories = ["TW", "HK"]

        all_releases: List[Release] = []
        seen_ids = set()

        for territory in territories:
            for cat_id in self.TARGET_NEW_RELEASE_CATEGORY_IDS:
                raw_albums = self.fetch_category_albums(category_id=cat_id, territory=territory, limit=limit)
                for raw_alb in raw_albums:
                    release = self._parse_release(raw_alb, territory=territory)
                    if not release.source_id or release.source_id in seen_ids:
                        continue
                    seen_ids.add(release.source_id)

                    raw_tracks = self.fetch_album_tracks(release.source_id, territory=territory)
                    tracks: List[Track] = []
                    for idx, t_data in enumerate(raw_tracks, start=1):
                        track = self._parse_track(t_data, album=release, index=idx, territory=territory)
                        tracks.append(track)

                    release.tracks = tracks
                    release.track_count = len(tracks)
                    all_releases.append(release)

        return all_releases
