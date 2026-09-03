from __future__ import annotations

from ._client import request_json


def GetPlaylistInfo(playlist_id):
    payload = request_json("/api/playlist/detail", {"id": playlist_id})
    if "playlist" not in payload and isinstance(payload.get("result"), dict):
        playlist = payload["result"]
        tracks = playlist.get("tracks") or []
        if "trackIds" not in playlist:
            playlist["trackIds"] = [{"id": track.get("id")} for track in tracks if track.get("id")]
        payload = {"code": payload.get("code", 200), "playlist": playlist}
    return payload
