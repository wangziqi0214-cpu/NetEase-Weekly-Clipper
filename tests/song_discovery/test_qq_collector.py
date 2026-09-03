"""Unit tests for QQMusicCollector."""

import json
import unittest
from song_discovery.collectors.qq import QQMusicCollector
from song_discovery.exceptions import PlatformResponseError
from song_discovery.http_client import HttpClient, HttpResponse


class MockQQTransport:
    def __init__(self, album_list_resp, album_track_resp, fail_all=False):
        self.album_list_resp = album_list_resp
        self.album_track_resp = album_track_resp
        self.fail_all = fail_all
        self.calls = []

    def request(self, method, url, params=None, data=None, json_body=None, headers=None, timeout=10.0):
        self.calls.append({"method": method, "url": url, "params": params, "json_body": json_body})

        if self.fail_all:
            return HttpResponse(500, '{"code": -1}', url=url)

        if "fcg_v8_album_info_cp.fcg" in url:
            return HttpResponse(200, json.dumps(self.album_track_resp), url=url)

        if params and "data" in params:
            try:
                data_obj = json.loads(params["data"])
                if "new_album" in data_obj or "album_list" in data_obj:
                    return HttpResponse(200, json.dumps(self.album_list_resp), url=url)
                if "album_song_list" in data_obj or "album_track" in data_obj:
                    return HttpResponse(200, json.dumps(self.album_track_resp), url=url)
            except Exception:
                pass
        return HttpResponse(200, "{}", url=url)


class TestQQMusicCollector(unittest.TestCase):
    def setUp(self):
        self.mock_album_list_data = {
            "code": 0,
            "new_album": {
                "code": 0,
                "data": {
                    "albums": [
                        {
                            "mid": "003mN2eF1qqALB",
                            "name": "星海漫游",
                            "singers": [{"name": "华语歌手A", "mid": "001ArtA"}],
                            "public_time": "2026-08-25",
                            "type_name": "专辑",
                        }
                    ]
                },
            },
        }

        # Structure using verified music.musichallAlbum.AlbumSongList
        self.mock_album_song_list_data = {
            "code": 0,
            "album_song_list": {
                "code": 0,
                "data": {
                    "songList": [
                        {
                            "songInfo": {
                                "mid": "001Track01",
                                "name": "序曲",
                                "singer": [{"name": "华语歌手A", "mid": "001ArtA"}],
                                "interval": 180,
                            }
                        },
                        {
                            "songInfo": {
                                "mid": "001Track02",
                                "name": "星海之歌",
                                "singer": [{"name": "华语歌手A", "mid": "001ArtA"}],
                                "interval": 235,
                            }
                        },
                    ]
                },
            },
        }

    def test_collect_new_releases_success(self):
        mock_transport = MockQQTransport(
            album_list_resp=self.mock_album_list_data,
            album_track_resp=self.mock_album_song_list_data,
        )
        client = HttpClient(transport=mock_transport)
        collector = QQMusicCollector(http_client=client)

        releases = collector.collect_new_releases(limit=1, areas=[1])
        self.assertEqual(len(releases), 1)

        release = releases[0]
        self.assertEqual(release.platform, "qq")
        self.assertEqual(release.source_id, "003mN2eF1qqALB")
        self.assertEqual(release.title, "星海漫游")
        self.assertEqual(release.artist_names, ["华语歌手A"])
        self.assertEqual(release.release_date, "2026-08-25")
        self.assertEqual(len(release.tracks), 2)
        self.assertEqual(release.track_count, 2)

        # Track 1
        self.assertEqual(release.tracks[0].title, "序曲")
        self.assertEqual(release.tracks[0].source_id, "001Track01")
        self.assertEqual(release.tracks[0].duration_ms, 180000)

        # Track 2
        self.assertEqual(release.tracks[1].title, "星海之歌")
        self.assertEqual(release.tracks[1].source_id, "001Track02")
        self.assertEqual(release.tracks[1].duration_ms, 235000)

    def test_qq_error_response_handling(self):
        err_transport = MockQQTransport(
            album_list_resp={"code": -1001, "message": "server error"},
            album_track_resp={},
        )
        client = HttpClient(transport=err_transport)
        collector = QQMusicCollector(http_client=client)

        with self.assertRaises(PlatformResponseError):
            collector.fetch_album_category_list(area=1)

    def test_qq_both_track_endpoints_fail_raises_error(self):
        err_transport = MockQQTransport(
            album_list_resp=self.mock_album_list_data,
            album_track_resp={"code": -1},
            fail_all=True,
        )
        client = HttpClient(transport=err_transport)
        collector = QQMusicCollector(http_client=client)

        with self.assertRaises(PlatformResponseError) as ctx:
            collector.fetch_album_tracks("003mN2eF1qqALB")
        self.assertIn("003mN2eF1qqALB", str(ctx.exception))

    def test_current_qq_release_time_and_numeric_type_are_inferred(self):
        album_list = json.loads(json.dumps(self.mock_album_list_data))
        raw_album = album_list["new_album"]["data"]["albums"][0]
        raw_album.pop("public_time")
        raw_album.pop("type_name")
        raw_album["release_time"] = "2026-08-27"
        raw_album["type"] = 0

        one_track = json.loads(json.dumps(self.mock_album_song_list_data))
        one_track["album_song_list"]["data"]["songList"] = one_track["album_song_list"]["data"]["songList"][:1]

        collector = QQMusicCollector(
            http_client=HttpClient(
                transport=MockQQTransport(
                    album_list_resp=album_list,
                    album_track_resp=one_track,
                )
            )
        )
        release = collector.collect_new_releases(limit=1, areas=[1])[0]

        self.assertEqual(release.release_date, "2026-08-27")
        self.assertEqual(release.release_type, "single")
        self.assertEqual(release.tracks[0].release_type, "single")


if __name__ == "__main__":
    unittest.main()
