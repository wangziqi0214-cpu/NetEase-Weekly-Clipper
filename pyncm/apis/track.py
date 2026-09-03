from __future__ import annotations

from ._client import request_json


def _ids_payload(ids):
    if isinstance(ids, (list, tuple, set)):
        return "[" + ",".join(str(item) for item in ids) + "]"
    return "[" + str(ids) + "]"


def GetTrackDetail(ids):
    payload = request_json("/api/song/detail", {"ids": _ids_payload(ids)})
    for song in payload.get("songs") or []:
        if "ar" not in song and "artists" in song:
            song["ar"] = song["artists"]
        if "al" not in song and "album" in song:
            album = song["album"]
            if "picUrl" not in album and album.get("blurPicUrl"):
                album["picUrl"] = album["blurPicUrl"]
            song["al"] = album
        if "dt" not in song:
            song["dt"] = song.get("duration") or song.get("mMusic", {}).get("playTime") or 0
    return payload


def GetTrackLyrics(song_id):
    return request_json("/api/song/lyric", {"id": song_id, "lv": -1, "kv": -1, "tv": -1})


def GetTrackAudio(ids):
    return request_json("/api/song/enhance/player/url", {"ids": _ids_payload(ids), "br": 320000})
