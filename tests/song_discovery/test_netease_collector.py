"""Unit tests for NetEaseCollector including pagination, deduplication, retry resilience, and early stop."""

import json
import unittest
from song_discovery.collectors.netease import NetEaseCollector
from song_discovery.exceptions import PlatformRequestError, PlatformResponseError, ServiceUnavailableError
from song_discovery.http_client import HttpClient, HttpResponse


class MockNetEaseTransport:
    def __init__(
        self,
        pages_data=None,
        album_detail_resp=None,
        album_detail_handlers=None,
        should_fail_connection=False,
    ):
        self.pages_data = pages_data or {}
        self.album_detail_resp = album_detail_resp or {
            "code": 200,
            "album": {"id": 1, "name": "专辑"},
            "songs": [{"id": 101, "name": "曲目1", "ar": [{"id": 1, "name": "歌手"}], "dt": 200000}],
        }
        self.album_detail_handlers = album_detail_handlers or {}
        self.should_fail_connection = should_fail_connection
        self.calls = []

    def request(self, method, url, params=None, data=None, json_body=None, headers=None, timeout=10.0):
        self.calls.append({"method": method, "url": url, "params": params or {}})
        if self.should_fail_connection:
            raise ServiceUnavailableError("Connection refused to localhost:3000", endpoint=url)

        if "/album/new" in url:
            offset = (params or {}).get("offset", 0)
            page_info = self.pages_data.get(offset, {"albums": [], "total": 0})
            payload = {
                "code": 200,
                "albums": page_info.get("albums", []),
                "total": page_info.get("total", 0),
            }
            return HttpResponse(200, json.dumps(payload), url=url)
        elif "/album" in url:
            alb_id = str((params or {}).get("id", ""))
            if alb_id in self.album_detail_handlers:
                handler = self.album_detail_handlers[alb_id]
                return handler(params)

            return HttpResponse(200, json.dumps(self.album_detail_resp), url=url)
        return HttpResponse(200, "{}", url=url)


class TestNetEaseCollector(unittest.TestCase):
    def setUp(self):
        self.mock_albums_data = {
            0: {
                "albums": [
                    {
                        "id": 19001,
                        "name": "夏夜风声",
                        "artists": [{"id": 501, "name": "网易原创人"}],
                        "publishTime": 1724000000000,
                        "type": "EP/Single",
                        "size": 2,
                    }
                ],
                "total": 500,
            }
        }

        self.mock_detail_data = {
            "code": 200,
            "album": {
                "id": 19001,
                "name": "夏夜风声",
            },
            "songs": [
                {
                    "id": 88001,
                    "name": "夏夜",
                    "ar": [{"id": 501, "name": "网易原创人"}],
                    "dt": 204000,
                },
                {
                    "id": 88002,
                    "name": "风声",
                    "ar": [{"id": 501, "name": "网易原创人"}],
                    "dt": 198000,
                },
            ],
        }

    def test_collect_new_releases_single_limit_compat(self):
        """Verify backward compatibility when limit=1 is passed directly."""
        transport = MockNetEaseTransport(
            pages_data=self.mock_albums_data,
            album_detail_resp=self.mock_detail_data,
        )
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        releases = collector.collect_new_releases(limit=1, area="ZH")
        self.assertEqual(len(releases), 1)

        release = releases[0]
        self.assertEqual(release.platform, "netease")
        self.assertEqual(release.source_id, "19001")
        self.assertEqual(release.title, "夏夜风声")
        self.assertEqual(release.artist_names, ["网易原创人"])
        self.assertEqual(len(release.tracks), 2)
        self.assertEqual(release.tracks[0].title, "夏夜")
        self.assertEqual(release.tracks[1].title, "风声")
        self.assertEqual(release.tracks[1].duration_ms, 198000)

    def test_transient_405_retries_and_succeeds(self):
        """Verify that a transient HTTP 405 on album detail is retried with _t timestamp and succeeds."""
        attempts = 0

        def alb_handler(params):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # First attempt returns transient 405
                return HttpResponse(405, '{"code": 405, "message": "Method Not Allowed"}')
            # Second attempt includes cache-busting timestamp _t and succeeds
            self.assertIn("_t", params)
            return HttpResponse(200, json.dumps(self.mock_detail_data))

        transport = MockNetEaseTransport(album_detail_handlers={"19001": alb_handler})
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        detail = collector.fetch_album_detail("19001")
        self.assertEqual(detail["code"], 200)
        self.assertEqual(attempts, 2)

    def test_permanent_single_album_failure_continues_next_album(self):
        """Verify that when one album fails after retries, it is logged as a warning, skipped, and other albums continue."""
        pages = {
            0: {
                "albums": [
                    {"id": "BAD_ALB_01", "name": "故障专辑", "artists": [{"id": 1, "name": "歌手A"}]},
                    {"id": "GOOD_ALB_02", "name": "正常专辑", "artists": [{"id": 2, "name": "歌手B"}]},
                ],
                "total": 2,
            }
        }

        def bad_handler(params):
            return HttpResponse(405, '{"code": 405, "message": "Persistent 405"}')

        def good_handler(params):
            return HttpResponse(
                200,
                json.dumps({
                    "code": 200,
                    "album": {"id": "GOOD_ALB_02", "name": "正常专辑"},
                    "songs": [{"id": 201, "name": "正常曲目", "ar": [{"id": 2, "name": "歌手B"}], "dt": 180000}],
                }),
            )

        transport = MockNetEaseTransport(
            pages_data=pages,
            album_detail_handlers={"BAD_ALB_01": bad_handler, "GOOD_ALB_02": good_handler},
        )
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        releases = collector.collect_new_releases(pages=1, page_size=2)
        # Only GOOD_ALB_02 should be returned
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].source_id, "GOOD_ALB_02")

        # Warnings should record the failure for BAD_ALB_01
        self.assertEqual(len(collector.warnings), 1)
        warn = collector.warnings[0]
        self.assertEqual(warn["album_id"], "BAD_ALB_01")
        self.assertEqual(warn["album_title"], "故障专辑")
        self.assertIn("405", warn["error"])

    def test_multi_page_offsets_and_deduplication(self):
        """Verify that pages are requested at offset 0, 20, 40... and duplicate IDs are deduplicated."""
        pages = {
            0: {
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(1, 21)],
                "total": 500,
            },
            20: {
                # Albums 1-5 duplicate page 0, albums 21-35 are new
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(1, 6)]
                + [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(21, 36)],
                "total": 500,
            },
        }

        transport = MockNetEaseTransport(pages_data=pages)
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        releases = collector.collect_new_releases(pages=2, page_size=20)
        # 20 from page 0 + 15 unique from page 20 = 35 total releases
        self.assertEqual(len(releases), 35)

        # Verify requested offsets
        album_calls = [c for c in transport.calls if "/album/new" in c["url"]]
        self.assertEqual(len(album_calls), 2)
        self.assertEqual(album_calls[0]["params"]["offset"], 0)
        self.assertEqual(album_calls[1]["params"]["offset"], 20)

    def test_early_stop_on_short_page(self):
        """Verify early stop when a page has fewer items than page_size."""
        pages = {
            0: {
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(1, 21)],
                "total": 500,
            },
            20: {
                # Short page: only 5 items instead of 20
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(21, 26)],
                "total": 500,
            },
            40: {
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(26, 46)],
                "total": 500,
            },
        }

        transport = MockNetEaseTransport(pages_data=pages)
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        # Requested 15 pages, but page 20 is short, so it stops after page 20 (2 calls total)
        releases = collector.collect_new_releases(pages=15, page_size=20)
        self.assertEqual(len(releases), 25)

        album_calls = [c for c in transport.calls if "/album/new" in c["url"]]
        self.assertEqual(len(album_calls), 2)

    def test_early_stop_on_total_exhaustion(self):
        """Verify early stop when total reported items are reached."""
        pages = {
            0: {
                "albums": [{"id": i, "name": f"专辑_{i}", "artists": [{"id": 1, "name": "歌手"}]} for i in range(1, 21)],
                "total": 20,  # Total equals page 0 items
            },
        }

        transport = MockNetEaseTransport(pages_data=pages)
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        releases = collector.collect_new_releases(pages=15, page_size=20)
        self.assertEqual(len(releases), 20)

        album_calls = [c for c in transport.calls if "/album/new" in c["url"]]
        self.assertEqual(len(album_calls), 1)

    def test_service_unavailable_raises_clear_error(self):
        transport = MockNetEaseTransport(
            pages_data={},
            album_detail_resp={},
            should_fail_connection=True,
        )
        client = HttpClient(transport=transport)
        collector = NetEaseCollector(base_url="http://localhost:3000", http_client=client)

        with self.assertRaises(ServiceUnavailableError) as ctx:
            collector.collect_new_releases(limit=1)

        self.assertIn("unavailable", str(ctx.exception).lower())
        self.assertIn("localhost:3000", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
