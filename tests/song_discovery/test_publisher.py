"""Unit tests for NetEasePublisher."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.db import DiscoveryDB
from song_discovery.http_client import HttpClient, HttpResponse
from song_discovery.matcher import TrackMatcher
from song_discovery.publisher import NetEasePublisher


class MockNetEasePublisherTransport:
    def __init__(self, login_ok=True, search_results=None, playlist_id="PL_7788"):
        self.login_ok = login_ok
        self.search_results = search_results or []
        self.playlist_id = playlist_id
        self.calls = []

    def request(self, method, url, params=None, headers=None, **kwargs):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers})

        if "/login/status" in url:
            if self.login_ok:
                return HttpResponse(200, '{"data": {"profile": {"userId": 12345, "nickname": "Curator"}}}', url=url)
            return HttpResponse(200, '{"data": {"profile": null, "account": {"anonimous": true}}}', url=url)

        if "/cloudsearch" in url or "/search" in url:
            return HttpResponse(200, f'{{"result": {{"songs": {self.search_results}}}}}', url=url)

        if "/playlist/create" in url:
            return HttpResponse(200, f'{{"code": 200, "id": "{self.playlist_id}"}}', url=url)

        if "/playlist/tracks" in url:
            return HttpResponse(200, '{"code": 200, "status": 200}', url=url)

        if "/song/order/update" in url:
            return HttpResponse(200, '{"code": 200}', url=url)

        return HttpResponse(200, "{}", url=url)


class TestNetEasePublisher(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_pub.db")
        self.db = DiscoveryDB(db_path=self.db_path)

        # 1. Native NetEase candidate (approved)
        self.db.upsert_candidate(
            platform="netease",
            release_source_id="REL_NE_01",
            track_source_id="NE_SONG_01",
            release_title="夏夜大碟",
            track_title="夏夜风声",
            artist_names="夏日入侵企画",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=210000,
            track_number=2,
            release_url="",
            track_url="",
            selection_rule="2nd_track",
            relevance_score=85.0,
            relevance_reasons=["乐队特征"],
        )
        self.db.update_review_status(1, "approved")

        # 2. QQ candidate (approved)
        self.db.upsert_candidate(
            platform="qq",
            release_source_id="REL_QQ_02",
            track_source_id="QQ_SONG_02",
            release_title="火车大碟",
            track_title="火车驶向云外",
            artist_names="刺猬乐队",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=280000,
            track_number=2,
            release_url="",
            track_url="",
            selection_rule="2nd_track",
            relevance_score=90.0,
            relevance_reasons=["乐队特征"],
        )
        self.db.update_review_status(2, "approved")

        # Search candidates returned for QQ track
        self.mock_search_json = (
            '[{"id": "NE_MATCH_999", "name": "火车驶向云外，梦安魂于九霄", "ar": [{"id": 10, "name": "刺猬"}], "dt": 280000}]'
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_dry_run_mode(self):
        transport = MockNetEasePublisherTransport(
            login_ok=False,  # Dry-run should not require login
            search_results=self.mock_search_json,
        )
        client = HttpClient(transport=transport)
        publisher = NetEasePublisher(db=self.db, http_client=client)

        summary = publisher.publish_approved(
            playlist_name="华语新歌周刊",
            dry_run=True,
        )

        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["status"], "planned")
        self.assertEqual(summary["total_approved"], 2)
        self.assertEqual(summary["direct_netease_count"], 1)
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["ready_to_add_count"], 2)
        search_queries = [call["params"]["keywords"] for call in transport.calls if "/cloudsearch" in call["url"]]
        self.assertIn("火车驶向云外 刺猬乐队", search_queries)

    def test_dry_run_deduplicates_same_song_across_platforms(self):
        duplicate_id, _ = self.db.upsert_candidate(
            platform="qq",
            release_source_id="REL_QQ_DUP",
            track_source_id="QQ_DUP",
            release_title="夏夜大碟",
            track_title="夏夜风声",
            artist_names="夏日入侵企画",
            release_type="album",
            release_date="2026-08-01",
            duration_ms=210000,
            track_number=2,
            release_url="",
            track_url="",
            selection_rule="2nd_track",
            relevance_score=85.0,
            relevance_reasons=["乐队特征"],
        )
        self.db.update_review_status(duplicate_id, "approved")
        transport = MockNetEasePublisherTransport(search_results=self.mock_search_json)
        summary = NetEasePublisher(db=self.db, http_client=HttpClient(transport=transport)).publish_approved(
            playlist_name="华语新歌周刊", dry_run=True
        )
        self.assertEqual(summary["total_approved"], 2)
        self.assertEqual(summary["direct_netease_count"], 1)

    def test_manual_match_override_is_persistent_and_skips_search(self):
        self.db.set_manual_match_override(
            candidate_id=2,
            netease_track_id="NE_MANUAL_42",
            matched_title="火车驶向云外",
            matched_artists="刺猬乐队",
            notes="人工确认",
        )
        transport = MockNetEasePublisherTransport(search_results=[])
        summary = NetEasePublisher(db=self.db, http_client=HttpClient(transport=transport)).publish_approved(
            playlist_name="华语新歌周刊", dry_run=True
        )
        item = next(row for row in summary["resolved_items"] if row["candidate_id"] == 2)
        self.assertTrue(item["is_resolved"])
        self.assertEqual(item["netease_track_id"], "NE_MANUAL_42")
        self.assertEqual(item["match_details"]["type"], "manual_override")
        self.assertFalse([call for call in transport.calls if "/cloudsearch" in call["url"]])

    def test_live_publish_and_idempotency(self):
        transport = MockNetEasePublisherTransport(
            login_ok=True,
            search_results=self.mock_search_json,
            playlist_id="PL_123456",
        )
        client = HttpClient(transport=transport)
        publisher = NetEasePublisher(db=self.db, http_client=client)

        # 1. First Publish
        res1 = publisher.publish_approved(
            playlist_name="华语新歌周刊第01期",
            cookie_header="MUSIC_U=valid",
            dry_run=False,
        )
        self.assertEqual(res1["status"], "success")
        self.assertEqual(res1["playlist_id"], "PL_123456")
        self.assertEqual(res1["added_count"], 2)
        add_call = next(call for call in transport.calls if "/playlist/tracks" in call["url"])
        desired_ids = [item["netease_track_id"] for item in res1["resolved_items"] if item["is_resolved"]]
        self.assertEqual(add_call["params"]["tracks"], ",".join(reversed(desired_ids)))
        order_call = next(call for call in transport.calls if "/song/order/update" in call["url"])
        self.assertEqual(order_call["params"]["pid"], "PL_123456")
        self.assertEqual(json.loads(order_call["params"]["ids"]), desired_ids)

        # 2. Second Publish with same target playlist (Idempotent)
        res2 = publisher.publish_approved(
            playlist_name="华语新歌周刊第01期",
            playlist_id="PL_123456",
            cookie_header="MUSIC_U=valid",
            dry_run=False,
        )
        # Already-published tracks should not be re-added
        self.assertEqual(res2["added_count"], 0)

    def test_same_playlist_name_is_reused_without_explicit_id(self):
        transport = MockNetEasePublisherTransport(login_ok=True, playlist_id="PL_REUSED")
        publisher = NetEasePublisher(db=self.db, http_client=HttpClient(transport=transport))

        first = publisher.publish_approved(
            playlist_name="华语新歌周刊 2026-W35",
            cookie_header="MUSIC_U=valid",
        )
        second = publisher.publish_approved(
            playlist_name="华语新歌周刊 2026-W35",
            cookie_header="MUSIC_U=valid",
        )

        self.assertEqual(first["playlist_id"], second["playlist_id"])
        create_calls = [c for c in transport.calls if "/playlist/create" in c["url"]]
        self.assertEqual(len(create_calls), 1)

    def test_no_approved_candidates_does_not_require_login_or_create_playlist(self):
        empty_db = DiscoveryDB(db_path=os.path.join(self.test_dir, "empty.db"))
        transport = MockNetEasePublisherTransport(login_ok=False)
        publisher = NetEasePublisher(db=empty_db, http_client=HttpClient(transport=transport))

        result = publisher.publish_approved(cookie_header="invalid")

        self.assertEqual(result["status"], "no_approved_candidates")
        self.assertFalse(transport.calls)

    def test_add_tracks_rejects_error_payload_without_success_code(self):
        class ErrorTransport:
            def request(self, method, url, **kwargs):
                return HttpResponse(200, '{"code": 401, "message": "login required"}', url=url)

        publisher = NetEasePublisher(db=self.db, http_client=HttpClient(transport=ErrorTransport()))
        with self.assertRaises(Exception):
            publisher.add_tracks_to_playlist("PL1", ["T1"], cookie_header="invalid")

    def test_mutations_accept_wrapped_success_and_reject_wrapped_body_error(self):
        class WrappedTransport:
            def __init__(self, body_code=200):
                self.body_code = body_code

            def request(self, method, url, **kwargs):
                payload = {"status": 200, "body": {"code": self.body_code, "count": 12}}
                return HttpResponse(200, json.dumps(payload), url=url)

        publisher = NetEasePublisher(db=self.db, http_client=HttpClient(transport=WrappedTransport()))
        removed = publisher.remove_tracks_from_playlist("PL1", ["T1"], cookie_header="valid")
        reordered = publisher.update_playlist_order("PL1", ["T2"], cookie_header="valid")
        self.assertEqual(removed["status"], "success")
        self.assertEqual(reordered["status"], "success")

        failing = NetEasePublisher(db=self.db, http_client=HttpClient(transport=WrappedTransport(body_code=401)))
        with self.assertRaises(Exception):
            failing.add_tracks_to_playlist("PL1", ["T1"], cookie_header="invalid")


if __name__ == "__main__":
    unittest.main()
