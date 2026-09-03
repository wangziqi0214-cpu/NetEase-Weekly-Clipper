"""QQ Music Collector for new releases and track extraction."""

import json
from typing import Any, Dict, List, Optional
from song_discovery.collectors.base import BaseCollector
from song_discovery.exceptions import PlatformRequestError, PlatformResponseError
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track


class QQMusicCollector(BaseCollector):
    """
    QQ Music collector implementing direct interaction with
    https://u.y.qq.com/cgi-bin/musicu.fcg and official album song APIs.
    """

    platform_name = Platform.QQ.value
    MUSICU_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    LEGACY_ALBUM_ENDPOINT = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg"

    def fetch_album_category_list(self, area: int = 1, limit: int = 10, start: int = 0) -> List[Dict[str, Any]]:
        """
        Fetch new album list for given area.
        area: 1 for Mainland China, 2 for Hong Kong & Taiwan.
        """
        payload = {
            "comm": {"ct": 24, "cv": 0},
            "new_album": {
                "module": "newalbum.NewAlbumServer",
                "method": "get_new_album_info",
                "param": {
                    "area": area,
                    "sin": start,
                    "num": limit,
                },
            },
        }

        headers = {"Referer": "https://y.qq.com/"}
        resp = self.http_client.get(
            self.MUSICU_ENDPOINT,
            params={"data": json.dumps(payload, ensure_ascii=False)},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, dict) or data.get("code", -1) != 0:
            code = data.get("code") if isinstance(data, dict) else "unknown"
            raise PlatformResponseError(
                f"QQ Music returned error code: {code}",
                platform=self.platform_name,
                raw_response=data if isinstance(data, dict) else None,
            )

        album_module = data.get("new_album") or data.get("album_list") or {}
        if album_module.get("code", 0) != 0:
            raise PlatformResponseError(
                f"QQ Music new_album module error: {album_module.get('code')}",
                platform=self.platform_name,
                raw_response=data,
            )

        mod_data = album_module.get("data", {})
        raw_albums = mod_data.get("albums") or mod_data.get("albumList") or mod_data.get("list") or []
        return raw_albums

    def fetch_album_tracks(self, album_mid: str) -> List[Dict[str, Any]]:
        """
        Fetch album track details using verified music.musichallAlbum.AlbumSongList module,
        with fallback to legacy album endpoint.
        Raises PlatformResponseError if both attempts fail.
        """
        errors = []

        # 1. Primary: Verified music.musichallAlbum.AlbumSongList module via musicu.fcg
        payload = {
            "comm": {"ct": 24, "cv": 0},
            "album_song_list": {
                "module": "music.musichallAlbum.AlbumSongList",
                "method": "GetAlbumSongList",
                "param": {
                    "albumMid": album_mid,
                    "begin": 0,
                    "num": 50,
                },
            },
        }
        headers = {"Referer": "https://y.qq.com/"}
        try:
            resp = self.http_client.get(
                self.MUSICU_ENDPOINT,
                params={"data": json.dumps(payload, ensure_ascii=False)},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get("code", -1) == 0:
                mod_data = data.get("album_song_list", {})
                if mod_data.get("code", -1) == 0:
                    song_list = mod_data.get("data", {}).get("songList") or []
                    if song_list:
                        return song_list
                else:
                    errors.append(f"musicu module returned code {mod_data.get('code')}")
            else:
                errors.append(f"musicu returned top-level code {data.get('code') if isinstance(data, dict) else 'invalid'}")
        except Exception as exc:
            errors.append(f"musicu request error: {exc}")

        # 2. Secondary fallback: Legacy fcg_v8_album_info_cp.fcg endpoint
        try:
            resp_legacy = self.http_client.get(
                self.LEGACY_ALBUM_ENDPOINT,
                params={"albummid": album_mid, "format": "json"},
                headers=headers,
            )
            resp_legacy.raise_for_status()
            data_legacy = resp_legacy.json()
            if isinstance(data_legacy, dict) and data_legacy.get("code", -1) == 0:
                legacy_list = data_legacy.get("data", {}).get("list") or []
                if legacy_list:
                    return legacy_list
                errors.append("legacy endpoint returned empty track list")
            else:
                errors.append(f"legacy endpoint returned code {data_legacy.get('code') if isinstance(data_legacy, dict) else 'invalid'}")
        except Exception as exc:
            errors.append(f"legacy endpoint error: {exc}")

        raise PlatformResponseError(
            f"Failed to fetch tracks for QQ albumMid '{album_mid}'. Errors: {'; '.join(errors)}",
            platform=self.platform_name,
        )

    def _parse_artists(self, raw_singers: Any) -> List[Artist]:
        artists = []
        if isinstance(raw_singers, list):
            for s in raw_singers:
                if isinstance(s, dict):
                    name = s.get("singer_name") or s.get("name") or s.get("singerName") or ""
                    mid = s.get("singer_mid") or s.get("mid") or s.get("singerMid") or str(s.get("id", ""))
                    if name:
                        artists.append(Artist(name=name.strip(), id=str(mid) if mid else None, raw_metadata=s))
                elif isinstance(s, str) and s.strip():
                    artists.append(Artist(name=s.strip()))
        elif isinstance(raw_singers, dict):
            name = raw_singers.get("singer_name") or raw_singers.get("name") or raw_singers.get("singerName") or ""
            mid = raw_singers.get("singer_mid") or raw_singers.get("mid") or raw_singers.get("singerMid") or str(raw_singers.get("id", ""))
            if name:
                artists.append(Artist(name=name.strip(), id=str(mid) if mid else None, raw_metadata=raw_singers))
        return artists

    def _parse_track(self, raw_song: Dict[str, Any], album: Release, index: int) -> Track:
        # Check if song data is nested under songInfo (from AlbumSongList)
        song_info = raw_song.get("songInfo") if "songInfo" in raw_song else raw_song

        mid = (
            song_info.get("mid")
            or song_info.get("songmid")
            or song_info.get("songMid")
            or song_info.get("song_mid")
            or str(song_info.get("id") or song_info.get("songId") or f"{album.source_id}_{index}")
        )
        title = (
            song_info.get("name")
            or song_info.get("songname")
            or song_info.get("songName")
            or song_info.get("song_name")
            or song_info.get("title")
            or ""
        )
        singers = song_info.get("singer") or song_info.get("singers") or song_info.get("singer_list") or []
        artists = self._parse_artists(singers) or album.artists

        interval = song_info.get("interval") or song_info.get("duration") or 0
        duration_ms = interval * 1000 if 0 < interval < 10000 else interval

        return Track(
            platform=self.platform_name,
            source_id=str(mid),
            title=title.strip(),
            artists=artists,
            album_title=album.title,
            release_type=album.release_type,
            release_date=album.release_date,
            duration_ms=int(duration_ms) if duration_ms else None,
            track_number=index,
            source_url=f"https://y.qq.com/n/ryqq/songDetail/{mid}" if mid else "",
            raw_metadata=raw_song,
        )

    def _parse_release(self, raw_album: Dict[str, Any], area_name: str) -> Release:
        album_mid = (
            raw_album.get("mid")
            or raw_album.get("album_mid")
            or raw_album.get("albumMid")
            or str(raw_album.get("album_id") or raw_album.get("id") or "")
        )
        album_name = (
            raw_album.get("name")
            or raw_album.get("album_name")
            or raw_album.get("albumName")
            or raw_album.get("title")
            or ""
        )
        raw_singers = raw_album.get("singers") or raw_album.get("singer_list") or raw_album.get("singer") or []
        artists = self._parse_artists(raw_singers)

        pub_date = (
            raw_album.get("release_time")
            or raw_album.get("public_time")
            or raw_album.get("pub_time")
            or raw_album.get("publish_date")
            or raw_album.get("pubTime")
            or raw_album.get("aDate")
            or ""
        )
        type_name = str(raw_album.get("type_name") or raw_album.get("type") or "").lower()
        if "单曲" in type_name or "single" in type_name:
            rel_type = ReleaseType.SINGLE.value
        elif "ep" in type_name:
            rel_type = ReleaseType.EP.value
        elif "album" in type_name or "专辑" in type_name:
            rel_type = ReleaseType.ALBUM.value
        else:
            # QQ's current new-album response commonly exposes only a numeric
            # `type`, which does not reliably distinguish singles from albums.
            # Infer it after the authoritative album track list is fetched.
            rel_type = ReleaseType.OTHER.value

        return Release(
            platform=self.platform_name,
            source_id=str(album_mid),
            title=album_name.strip(),
            artists=artists,
            album_title=album_name.strip(),
            release_type=rel_type,
            release_date=str(pub_date).strip(),
            source_url=f"https://y.qq.com/n/ryqq/albumDetail/{album_mid}" if album_mid else "",
            raw_metadata=raw_album,
        )

    def collect_new_releases(self, limit: int = 10, areas: Optional[List[int]] = None, **kwargs) -> List[Release]:
        """
        Collect new releases from Mainland (area=1) and HK/Taiwan (area=2).

        Args:
            limit: Maximum releases per area.
            areas: List of area IDs (default: [1, 2]).

        Returns:
            List of parsed Release objects with populated tracks.
        """
        if areas is None:
            areas = [1, 2]

        area_labels = {1: "Mainland China (area=1)", 2: "HK/Taiwan (area=2)"}
        all_releases: List[Release] = []
        seen_ids = set()

        for area in areas:
            label = area_labels.get(area, f"Area {area}")
            raw_albums = self.fetch_album_category_list(area=area, limit=limit)
            for raw_alb in raw_albums:
                release = self._parse_release(raw_alb, area_name=label)
                if not release.source_id or release.source_id in seen_ids:
                    continue
                seen_ids.add(release.source_id)

                raw_songs = self.fetch_album_tracks(release.source_id)
                tracks: List[Track] = []
                for idx, song_data in enumerate(raw_songs, start=1):
                    track = self._parse_track(song_data, album=release, index=idx)
                    tracks.append(track)

                release.tracks = tracks
                release.track_count = len(tracks)
                if release.release_type == ReleaseType.OTHER.value:
                    release.release_type = (
                        ReleaseType.SINGLE.value if len(tracks) == 1 else ReleaseType.ALBUM.value
                    )
                    for track in tracks:
                        track.release_type = release.release_type
                all_releases.append(release)

        return all_releases
