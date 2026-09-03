"""Unit tests for KKBOXCollector interfacing with official Open API."""

import json
import os
import unittest
from unittest.mock import patch
from song_discovery.collectors.kkbox import KKBOXCollector
from song_discovery.http_client import HttpClient, HttpResponse


class MockKKBOXTransport:
    def __init__(self, oauth_token="mock_token_123", categories_data=None, albums_data=None, tracks_data=None):
        self.requested_urls = []
        self.oauth_token = oauth_token
        self.categories_data = categories_data or {"data": [{"id": "cat_mandarin_01", "title": "华语最新单曲/专辑"}]}
        self.albums_data = albums_data or {
            "data": [
                {
                    "id": "alb_kk_01",
                    "name": "夏夜风声",
                    "artist": {"name": "刺猬乐队", "id": "art_01"},
                    "release_date": "2026-08-01",
                    "url": "https://www.kkbox.com/tw/album/alb_kk_01",
                }
            ]
        }
        self.tracks_data = tracks_data or {
            "data": [
                {
                    "id": "trk_kk_01",
                    "name": "夏夜",
                    "duration": 210000,
                    "track_number": 1,
                    "url": "https://www.kkbox.com/tw/song/trk_kk_01",
                },
                {
                    "id": "trk_kk_02",
                    "name": "风声",
                    "duration": 195000,
                    "track_number": 2,
                    "url": "https://www.kkbox.com/tw/song/trk_kk_02",
                },
            ]
        }

    def request(self, method, url, params=None, data=None, json_body=None, headers=None, timeout=10.0):
        self.requested_urls.append(url)
        if "oauth2/token" in url:
            return HttpResponse(200, json.dumps({"access_token": self.oauth_token, "token_type": "Bearer"}), url=url)
        elif "/new-release-categories" in url and "/albums" not in url:
            return HttpResponse(200, json.dumps(self.categories_data), url=url)
        elif "/albums" in url and "/tracks" not in url:
            return HttpResponse(200, json.dumps(self.albums_data), url=url)
        elif "/tracks" in url:
            return HttpResponse(200, json.dumps(self.tracks_data), url=url)
        return HttpResponse(200, "{}", url=url)


class TestKKBOXCollector(unittest.TestCase):
    def test_api_base_v1_1(self):
        collector = KKBOXCollector(client_id="dummy", client_secret="dummy")
        self.assertEqual(collector.API_BASE, "https://api.kkbox.com/v1.1")

    def test_collect_with_mocked_oauth(self):
        transport = MockKKBOXTransport()
        client = HttpClient(transport=transport)
        collector = KKBOXCollector(client_id="test_id", client_secret="test_secret", http_client=client)

        self.assertTrue(collector.has_credentials)
        releases = collector.collect_new_releases(limit=1, territories=["TW"])
        self.assertEqual(len(releases), 1)

        release = releases[0]
        self.assertEqual(release.platform, "kkbox")
        self.assertEqual(release.source_id, "alb_kk_01")
        self.assertEqual(release.title, "夏夜风声")
        self.assertEqual(release.artist_names, ["刺猬乐队"])
        self.assertEqual(len(release.tracks), 2)
        self.assertEqual(release.tracks[0].title, "夏夜")
        self.assertEqual(release.tracks[1].title, "风声")
        self.assertEqual(collector.source_mode, "official_open_api")
        category_album_urls = [
            url for url in transport.requested_urls
            if "/new-release-categories/" in url and url.endswith("/albums")
        ]
        self.assertEqual(category_album_urls, [
            f"https://api.kkbox.com/v1.1/new-release-categories/{category_id}/albums"
            for category_id in collector.TARGET_NEW_RELEASE_CATEGORY_IDS
        ])

    def test_missing_credentials_skips(self):
        with patch.dict(os.environ, {"KKBOX_CLIENT_ID": "", "CLIENT_ID": "", "KKBOX_CLIENT_SECRET": "", "CLIENT_SECRET": ""}):
            collector = KKBOXCollector(client_id=None, client_secret=None)
            self.assertFalse(collector.has_credentials)
            releases = collector.collect_new_releases()
            self.assertEqual(releases, [])


if __name__ == "__main__":
    unittest.main()
