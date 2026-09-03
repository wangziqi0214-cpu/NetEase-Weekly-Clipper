from __future__ import annotations

from ._client import request_json


def GetAlbumInfo(album_id):
    return request_json("/api/album/" + str(album_id))
