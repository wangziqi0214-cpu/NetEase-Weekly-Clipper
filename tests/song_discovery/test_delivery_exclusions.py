from datetime import date
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from song_discovery.db import DiscoveryDB
from song_discovery.http_client import HttpClient, HttpResponse
from song_discovery.publisher import NetEasePublisher
import song_discovery.review_api as review_api
from song_discovery.review_api import app


def seed_candidate(
    db: DiscoveryDB,
    platform: str,
    source_id: str,
    title: str = "Test Track",
    artist: str = "Test Artist",
    review_status: str = "approved",
    release_date: str = None,
) -> int:
    if not release_date:
        release_date = date.today().isoformat()
    cid, _ = db.upsert_candidate(
        platform=platform,
        release_source_id=f"album-{source_id}",
        track_source_id=source_id,
        release_title=f"{title} Album",
        track_title=title,
        artist_names=artist,
        release_type="album",
        release_date=release_date,
        duration_ms=210000,
        track_number=1,
        release_url=f"https://example.com/{platform}/album",
        track_url=f"https://example.com/{platform}/{source_id}",
        selection_rule="single_rule",
        relevance_score=85,
        relevance_reasons=["Rock", "Featured"],
        raw_metadata={"cover_url": "https://example.com/cover.jpg"},
        initial_review_status="pending",
    )
    if review_status != "pending":
        db.update_review_status(cid, review_status)
    return cid


class MockPublisherTransport:
    def __init__(self, playlist_id: str = "PL_TEST_123"):
        self.playlist_id = playlist_id

    def request(self, method, url, params=None, headers=None, **kwargs):
        if "/login/status" in url:
            return HttpResponse(200, '{"data": {"profile": {"userId": 12345, "nickname": "Tester"}}}', url=url)
        if "/playlist/create" in url:
            return HttpResponse(200, f'{{"code": 200, "id": "{self.playlist_id}"}}', url=url)
        if "/playlist/tracks" in url:
            return HttpResponse(200, '{"code": 200, "status": 200}', url=url)
        if "/song/order/update" in url:
            return HttpResponse(200, '{"code": 200}', url=url)
        return HttpResponse(200, "{}", url=url)


class DeliveryExclusionsTests(unittest.TestCase):
    def test_db_delivery_exclusions_crud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "test.db"))
            cid1 = seed_candidate(db, "netease", "track-1")
            cid2 = seed_candidate(db, "qq", "track-2")
            playlist = "Weekly_Playlist_20260829"

            # Initially no exclusions
            self.assertEqual(set(db.get_delivery_exclusions(playlist)), set())

            # Exclude cid1
            db.exclude_delivery_candidate(playlist, cid1)
            self.assertEqual(set(db.get_delivery_exclusions(playlist)), {cid1})

            # Exclude cid2
            db.exclude_delivery_candidate(playlist, cid2)
            self.assertEqual(set(db.get_delivery_exclusions(playlist)), {cid1, cid2})

            # Exclusions are scoped by playlist_name
            self.assertEqual(set(db.get_delivery_exclusions("Other_Playlist")), set())

            # Restore cid1
            db.include_delivery_candidate(playlist, cid1)
            self.assertEqual(set(db.get_delivery_exclusions(playlist)), {cid2})

            # Re-including already included does not error
            db.include_delivery_candidate(playlist, cid1)
            self.assertEqual(set(db.get_delivery_exclusions(playlist)), {cid2})

    def test_db_is_candidate_published_for_playlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "test.db"))
            cid = seed_candidate(db, "netease", "track-pub")
            playlist = "Weekly_Playlist_20260829"

            self.assertFalse(db.is_candidate_published_for_playlist(playlist, cid))
            self.assertEqual(db.get_already_published_candidate_ids(playlist), set())

            # Record a publication
            pub_id = db.create_publication(playlist_id="ne-pl-123", playlist_name=playlist)
            db.record_published_tracks(pub_id, [{
                "candidate_id": cid,
                "platform": "netease",
                "original_track_id": "track-pub",
                "netease_track_id": "track-pub",
                "match_status": "direct_netease",
                "match_confidence": 1.0,
                "added_to_playlist": 1,
            }])

            self.assertTrue(db.is_candidate_published_for_playlist(playlist, cid))
            self.assertEqual(db.get_already_published_candidate_ids(playlist), {cid})
            self.assertFalse(db.is_candidate_published_for_playlist("Other_Playlist", cid))

    def test_api_exclude_unpublished_delivery_song(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_test.db"
            db = DiscoveryDB(str(db_path))
            cid = seed_candidate(db, "netease", "ne-100", title="Unpublished Song")

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)

                # Fetch preview to verify song is present and get current playlist_name
                preview_resp = client.get("/api/publication/preview")
                self.assertEqual(preview_resp.status_code, 200)
                preview = preview_resp.json()
                playlist_name = preview["playlist_name"]

                ready_ids = [item["candidate_id"] for item in preview["ready_items"]]
                self.assertIn(cid, ready_ids)
                self.assertEqual(preview.get("excluded_count", 0), 0)

                # Delete (exclude) the unpublished song
                del_resp = client.delete(
                    f"/api/publication/delivery/items/{cid}",
                    params={"playlist_name": playlist_name},
                )
                self.assertEqual(del_resp.status_code, 200)
                del_data = del_resp.json()
                self.assertEqual(del_data["candidate_id"], cid)

                # Candidate data and review status MUST be preserved
                candidate_record = db.get_candidate_by_id(cid)
                self.assertIsNotNone(candidate_record)
                self.assertEqual(candidate_record["review_status"], "approved")
                self.assertEqual(candidate_record["track_title"], "Unpublished Song")

                # Re-fetch preview: excluded song must NOT be in ready_items, and must appear in excluded_items
                preview_after = client.get("/api/publication/preview").json()
                after_ready_ids = [item["candidate_id"] for item in preview_after["ready_items"]]
                self.assertNotIn(cid, after_ready_ids)
                self.assertEqual(preview_after["excluded_count"], 1)
                excluded_cids = [item["candidate_id"] for item in preview_after["excluded_items"]]
                self.assertIn(cid, excluded_cids)

                # Restore the song
                restore_resp = client.post(
                    f"/api/publication/delivery/items/{cid}/restore",
                    params={"playlist_name": playlist_name},
                )
                self.assertEqual(restore_resp.status_code, 200)
                self.assertTrue(restore_resp.json()["success"])

                # Re-fetch preview: song is back in ready_items
                preview_restored = client.get("/api/publication/preview").json()
                restored_ready_ids = [item["candidate_id"] for item in preview_restored["ready_items"]]
                self.assertIn(cid, restored_ready_ids)
                self.assertEqual(preview_restored["excluded_count"], 0)

    def test_api_cannot_exclude_already_published_song(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_test2.db"
            db = DiscoveryDB(str(db_path))
            cid = seed_candidate(db, "netease", "ne-200", title="Already Published Song")

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)
                preview = client.get("/api/publication/preview").json()
                playlist_name = preview["playlist_name"]

                # Mark as published
                pub_id = db.create_publication(playlist_id="pl-published-1", playlist_name=playlist_name)
                db.record_published_tracks(pub_id, [{
                    "candidate_id": cid,
                    "platform": "netease",
                    "original_track_id": "ne-200",
                    "netease_track_id": "ne-200",
                    "match_status": "direct_netease",
                    "match_confidence": 1.0,
                    "added_to_playlist": 1,
                }])

                # Attempting to delete (exclude) should return 400
                resp = client.delete(
                    f"/api/publication/delivery/items/{cid}",
                    params={"playlist_name": playlist_name},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertIn("already published", resp.json()["detail"].lower())

    def test_publisher_skips_excluded_delivery_songs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "pub_test.db"))
            cid1 = seed_candidate(db, "netease", "ne-101", title="Song To Keep")
            cid2 = seed_candidate(db, "netease", "ne-102", title="Song To Exclude")
            playlist = "Weekly_Delivery_Exclusion_Test"

            # Exclude cid2
            db.exclude_delivery_candidate(playlist, cid2)

            transport = MockPublisherTransport(playlist_id="netease-playlist-id-1")
            http_client = HttpClient(transport=transport)
            publisher = NetEasePublisher(db=db, http_client=http_client)
            result = publisher.publish_approved(
                playlist_name=playlist,
            )

            self.assertEqual(result["playlist_id"], "netease-playlist-id-1")
            self.assertEqual(result["added_count"], 1)
            # Only cid1 was published
            published_ids = db.get_already_published_candidate_ids(playlist)
            self.assertIn(cid1, published_ids)
            self.assertNotIn(cid2, published_ids)
