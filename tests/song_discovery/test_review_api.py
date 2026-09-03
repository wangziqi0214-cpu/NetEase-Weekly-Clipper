import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from song_discovery.db import DiscoveryDB
from song_discovery.models import Artist, Release
import song_discovery.review_api as review_api
from song_discovery.review_api import app


def seed(db: DiscoveryDB, platform: str, source_id: str, title: str = "Rock Song") -> int:
    candidate_id, _ = db.upsert_candidate(
        platform=platform,
        release_source_id=f"album-{source_id}",
        track_source_id=source_id,
        release_title="New Album",
        track_title=title,
        artist_names="Example Band",
        release_type="album",
        release_date="2026-08-29",
        duration_ms=210000,
        track_number=2,
        release_url=f"https://example.com/{platform}/album",
        track_url=f"https://example.com/{platform}/{source_id}",
        selection_rule="album_second_track",
        relevance_score=80,
        relevance_reasons=["摇滚", "专辑第二首"],
        raw_metadata={"cover_url": "https://example.com/cover.jpg"},
    )
    return candidate_id


class ReviewApiTests(unittest.TestCase):
    def test_candidate_cover_uses_track_release_and_qq_fallback_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "covers.db"
            db = DiscoveryDB(str(db_path))
            candidate_id = seed(db, "kkbox", "kk-cover-1")
            with db.get_connection() as conn:
                conn.execute("UPDATE candidates SET raw_metadata = '{}' WHERE id = ?", (candidate_id,))
                conn.commit()
            db.upsert_release(Release(
                platform="kkbox",
                source_id="album-kk-cover-1",
                title="New Album",
                artists=[Artist(name="Example Band")],
                album_title="New Album",
                release_type="album",
                release_date="2026-08-29",
                raw_metadata={"images": [
                    {"width": 160, "height": 160, "url": "https://img.example/160.jpg"},
                    {"width": 1000, "height": 1000, "url": "https://img.example/1000.jpg"},
                ]},
            ))
            self.assertIn("1000.jpg", db.get_candidate_by_id(candidate_id)["release_raw_metadata"])

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                payload = TestClient(app).get(
                    "/api/candidates",
                    params={"platform": "kkbox", "status": "pending", "dedup": "false"},
                ).json()

            self.assertEqual(payload["items"][0]["cover_url"], "https://img.example/1000.jpg")
            self.assertEqual(
                review_api._find_cover_url({"al": {"picUrl": "https://img.example/netease.jpg"}}),
                "https://img.example/netease.jpg",
            )
            self.assertIn(
                "T002R500x500M000003mN2eF1qqALB.jpg",
                review_api._candidate_cover_url({"platform": "qq", "release_source_id": "003mN2eF1qqALB"}),
            )

    def test_candidates_deduplicate_and_batch_status_records_one_learning_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "review.db"
            db = DiscoveryDB(str(db_path))
            netease_id = seed(db, "netease", "ne-1")
            qq_id = seed(db, "qq", "qq-1")
            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)
                response = client.get("/api/candidates", params={"status": "pending", "dedup": "true"})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["count"], 1)
                self.assertEqual(payload["raw_count"], 2)
                self.assertEqual(payload["hidden_incomplete"], 0)
                self.assertEqual(payload["items"][0]["raw_ids"], [netease_id, qq_id])
                self.assertEqual(payload["items"][0]["artist_names"], "Example Band")
                updated = client.post(
                    "/api/candidates/batch-status",
                    json={"candidate_ids": [netease_id, qq_id], "status": "rejected"},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["updated"], 2)
                self.assertEqual(updated.json()["learning_samples"], 1)
            self.assertEqual(db.get_candidate_by_id(netease_id)["review_status"], "rejected")
            self.assertEqual(db.get_candidate_by_id(qq_id)["review_status"], "rejected")
            self.assertEqual(db.get_feedback_stats()["history_total"], 1)

    def test_stats_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "stats.db"
            db = DiscoveryDB(str(db_path))
            seed(db, "kkbox", "kk-1")
            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                result = TestClient(app).get("/api/stats")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["total"], 1)
            self.assertEqual(result.json()["reviewable_total"], 1)
            self.assertEqual(result.json()["unique_total"], 1)
            self.assertEqual(result.json()["pending"], 1)
            self.assertEqual(result.json()["by_platform"], {"kkbox": 1})

    def test_video_workflow_artifacts_are_exposed_from_job_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "output" / "review.db"
            db = DiscoveryDB(str(db_path))
            job_id = "video_artifact_test"
            job_dir = root / "output" / "video_jobs" / job_id
            job_dir.mkdir(parents=True)
            (job_dir / "final_video.mp4").write_bytes(b"video-bytes")
            (job_dir / "release_report.md").write_text("# report", encoding="utf-8")
            db.create_video_workflow_job({
                "job_id": job_id,
                "publication_id": "pub-artifact",
                "playlist_id": "playlist-artifact",
                "playlist_url": "https://music.163.com/playlist?id=1",
                "ordered_track_ids": ["1"],
                "status": "completed",
                "log_path": str(job_dir / "workflow.log"),
            })
            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", root):
                client = TestClient(app)
                latest = client.get("/api/video-workflow/latest")
                self.assertEqual(latest.status_code, 200)
                artifacts = latest.json()["job"]["artifacts"]
                self.assertIn("video", artifacts)
                self.assertIn("release_report", artifacts)
                video = client.get(artifacts["video"])
                self.assertEqual(video.status_code, 200)
                self.assertEqual(video.content, b"video-bytes")
                self.assertEqual(client.get(f"/api/video-workflow/{job_id}/artifacts/unknown").status_code, 404)

    def test_publish_copy_endpoints_get_put_reset_and_security(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "output" / "review.db"
            db = DiscoveryDB(str(db_path))
            job_id = "video_pub_copy_test"
            job_dir = root / "output" / "video_jobs" / job_id
            job_dir.mkdir(parents=True)

            db.create_video_workflow_job({
                "job_id": job_id,
                "publication_id": "pub-copy-1",
                "playlist_id": "PL-copy-1",
                "playlist_url": "https://music.163.com/playlist?id=1",
                "ordered_track_ids": ["1"],
                "status": "completed",
                "log_path": str(job_dir / "workflow.log"),
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", root):
                client = TestClient(app)

                # 1. 404 when publish copy files do not exist yet
                res = client.get(f"/api/video-workflow/{job_id}/publish-copy")
                self.assertEqual(res.status_code, 404)

                # Write initial files
                auto_content = "# Initial Auto Copy\n\nAuto content"
                (job_dir / "publish_copy.auto.md").write_text(auto_content, encoding="utf-8")
                (job_dir / "publish_copy.md").write_text(auto_content, encoding="utf-8")

                # 2. GET returns auto and editable text
                res = client.get(f"/api/video-workflow/{job_id}/publish-copy")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data["job_id"], job_id)
                self.assertEqual(data["auto_text"], auto_content)
                self.assertEqual(data["editable_text"], auto_content)
                self.assertFalse(data["is_modified"])

                # Also verify artifact endpoint exposes publish_copy
                artifact_res = client.get(f"/api/video-workflow/{job_id}/artifacts/publish_copy")
                self.assertEqual(artifact_res.status_code, 200)
                self.assertEqual(artifact_res.text, auto_content)

                # 3. PUT updates editable text
                custom_content = "# Edited Copy\n\nUser modified text"
                put_res = client.put(
                    f"/api/video-workflow/{job_id}/publish-copy",
                    json={"text": custom_content},
                )
                self.assertEqual(put_res.status_code, 200)
                self.assertEqual(put_res.json()["text"], custom_content)
                self.assertTrue(put_res.json()["is_modified"])

                # Verify file on disk was modified
                self.assertEqual((job_dir / "publish_copy.md").read_text(encoding="utf-8"), custom_content)
                self.assertEqual((job_dir / "publish_copy.auto.md").read_text(encoding="utf-8"), auto_content)

                # GET now reflects is_modified = True
                res2 = client.get(f"/api/video-workflow/{job_id}/publish-copy")
                self.assertTrue(res2.json()["is_modified"])
                self.assertEqual(res2.json()["editable_text"], custom_content)

                # 4. POST reset restores editable text from auto text
                reset_res = client.post(f"/api/video-workflow/{job_id}/publish-copy/reset")
                self.assertEqual(reset_res.status_code, 200)
                self.assertEqual(reset_res.json()["text"], auto_content)
                self.assertFalse(reset_res.json()["is_modified"])
                self.assertEqual((job_dir / "publish_copy.md").read_text(encoding="utf-8"), auto_content)

                # 5. Security: invalid job ID formats and non-existent jobs
                self.assertEqual(client.get("/api/video-workflow/video_..escape/publish-copy").status_code, 400)
                self.assertEqual(client.get("/api/video-workflow/invalid_job_format/publish-copy").status_code, 400)
                self.assertEqual(client.get("/api/video-workflow/video_non_existent/publish-copy").status_code, 404)

                # 6. Security: text length limit
                huge_text = "x" * 100001
                huge_put = client.put(f"/api/video-workflow/{job_id}/publish-copy", json={"text": huge_text})
                self.assertEqual(huge_put.status_code, 422)

    def test_manual_match_endpoints_and_preview_integration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "output" / "review.db"
            db = DiscoveryDB(str(db_path))

            # Seed an ambiguous QQ candidate (approved)
            cid = seed(db, "qq", "qq-track-999", title="未知新歌")
            db.update_review_status(cid, "approved")

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", root):
                client = TestClient(app)

                # 1. Search NetEase songs (mocking search_netease_songs)
                mock_songs = [
                    {
                        "id": 18332095,
                        "name": "真实歌曲名",
                        "artists": [{"name": "真实艺人"}],
                        "album": {"name": "真实专辑", "publishTime": 1787673600000},
                        "duration": 210000,
                    }
                ]
                with patch("song_discovery.publisher.NetEasePublisher.search_netease_songs", return_value=mock_songs):
                    search_res = client.get("/api/netease/search", params={"keyword": "未知新歌"})
                    self.assertEqual(search_res.status_code, 200)
                    search_data = search_res.json()
                    self.assertEqual(search_data["count"], 1)
                    self.assertEqual(search_data["results"][0]["id"], "18332095")
                    self.assertEqual(search_data["results"][0]["title"], "真实歌曲名")

                # 2. Save manual match override
                match_res = client.post(
                    "/api/publication/manual-match",
                    json={
                        "candidate_id": cid,
                        "netease_track_id": "https://music.163.com/#/song?id=18332095",
                        "matched_title": "真实歌曲名",
                        "matched_artists": "真实艺人",
                        "target_release_date": "2026-08-25",
                        "notes": "人审指定匹配",
                    },
                )
                self.assertEqual(match_res.status_code, 200)
                self.assertEqual(match_res.json()["netease_track_id"], "18332095")

                # Verify preview reflects manual match override
                preview_res = client.get("/api/publication/preview")
                self.assertEqual(preview_res.status_code, 200)
                preview = preview_res.json()
                matching_in_preview = [x for x in preview["ready_items"] + preview["out_of_week_items"] if x["candidate_id"] == cid]
                self.assertTrue(len(matching_in_preview) > 0)
                self.assertTrue(matching_in_preview[0]["is_manual_override"])
                self.assertEqual(matching_in_preview[0]["netease_track_id"], "18332095")

                # 3. Delete manual match override
                del_res = client.delete(f"/api/publication/manual-match/{cid}")
                self.assertEqual(del_res.status_code, 200)
                self.assertTrue(del_res.json()["deleted"])

                # Verify override is removed in DB
                self.assertIsNone(db.get_manual_match_override(cid))

                # 4. Error cases
                err_cand = client.post("/api/publication/manual-match", json={"candidate_id": 999999, "netease_track_id": "18332095"})
                self.assertEqual(err_cand.status_code, 404)

                err_id = client.post("/api/publication/manual-match", json={"candidate_id": cid, "netease_track_id": "invalid_no_id"})
                self.assertEqual(err_id.status_code, 400)

                embedded_id = client.post("/api/publication/manual-match", json={"candidate_id": cid, "netease_track_id": "not-a-song-18332095"})
                self.assertEqual(embedded_id.status_code, 400)

                # Invalid date formats
                err_date1 = client.post("/api/publication/manual-match", json={"candidate_id": cid, "netease_track_id": "18332095", "target_release_date": "2026-99-99"})
                self.assertEqual(err_date1.status_code, 400)

                err_date2 = client.post("/api/publication/manual-match", json={"candidate_id": cid, "netease_track_id": "18332095", "target_release_date": "not-a-date"})
                self.assertEqual(err_date2.status_code, 400)

                err_date3 = client.post("/api/publication/manual-match", json={"candidate_id": cid, "netease_track_id": "18332095", "target_release_date": "2026-08-25extra"})
                self.assertEqual(err_date3.status_code, 400)

                # Unapproved candidates (pending / rejected / deferred) must be rejected with 409
                pending_cid = seed(db, "qq", "qq-track-pending-1", title="待审歌曲")
                err_pending = client.post("/api/publication/manual-match", json={"candidate_id": pending_cid, "netease_track_id": "18332095"})
                self.assertEqual(err_pending.status_code, 409)

                rejected_cid = seed(db, "qq", "qq-track-rejected-1", title="拒绝歌曲")
                db.update_review_status(rejected_cid, "rejected", reason_code="non_rock_band")
                err_rejected = client.post("/api/publication/manual-match", json={"candidate_id": rejected_cid, "netease_track_id": "18332095"})
                self.assertEqual(err_rejected.status_code, 409)

    @patch("song_discovery.video_workflow.subprocess.Popen")
    def test_video_workflow_jobs_and_safe_resume(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 99881
        mock_popen.return_value = mock_proc

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "output" / "review.db"
            db = DiscoveryDB(str(db_path))

            job_id_failed = "video_job_failed_1"
            job_dir = root / "output" / "video_jobs" / job_id_failed
            job_dir.mkdir(parents=True)
            (job_dir / "researched_playlist.json").write_text("{}", encoding="utf-8")
            (job_dir / "workflow.log").write_text("[*] starting video workflow\n[*] running radio research\n", encoding="utf-8")

            db.create_video_workflow_job({
                "job_id": job_id_failed,
                "publication_id": "pub_f1",
                "playlist_id": "PL_f1",
                "playlist_url": "https://music.163.com/playlist?id=PL_f1",
                "ordered_track_ids": ["101", "102"],
                "status": "failed",
                "log_path": str(job_dir / "workflow.log"),
                "error": "Temporary network timeout",
            })

            # Job with only raw_playlist.json (no researched_playlist.json)
            job_id_raw_only = "video_job_raw_2"
            job_raw_dir = root / "output" / "video_jobs" / job_id_raw_only
            job_raw_dir.mkdir(parents=True)
            (job_raw_dir / "raw_playlist.json").write_text("{}", encoding="utf-8")
            (job_raw_dir / "workflow.log").write_text("[*] crawl completed\n", encoding="utf-8")

            db.create_video_workflow_job({
                "job_id": job_id_raw_only,
                "publication_id": "pub_r2_raw",
                "playlist_id": "PL_raw2",
                "playlist_url": "https://music.163.com/playlist?id=PL_raw2",
                "ordered_track_ids": ["101"],
                "status": "cancelled",
                "log_path": str(job_raw_dir / "workflow.log"),
            })

            job_id_running = "video_job_running_2"
            db.create_video_workflow_job({
                "job_id": job_id_running,
                "publication_id": "pub_r2",
                "playlist_id": "PL_r2",
                "playlist_url": "https://music.163.com/playlist?id=PL_r2",
                "ordered_track_ids": ["201"],
                "status": "running",
                "log_path": str(root / "output" / "video_jobs" / job_id_running / "workflow.log"),
            })

            job_id_completed = "video_job_completed_3"
            db.create_video_workflow_job({
                "job_id": job_id_completed,
                "publication_id": "pub_c3",
                "playlist_id": "PL_c3",
                "playlist_url": "https://music.163.com/playlist?id=PL_c3",
                "ordered_track_ids": ["301"],
                "status": "completed",
                "log_path": str(root / "output" / "video_jobs" / job_id_completed / "workflow.log"),
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}), patch.object(review_api, "PROJECT_ROOT", root):
                client = TestClient(app)

                # 1. List jobs and check stage & log artifact exposure
                jobs_res = client.get("/api/video-workflow/jobs")
                self.assertEqual(jobs_res.status_code, 200)
                jobs = jobs_res.json()["jobs"]
                self.assertEqual(len(jobs), 4)

                failed_job = next(j for j in jobs if j["job_id"] == job_id_failed)
                self.assertEqual(failed_job["track_count"], 2)
                self.assertEqual(failed_job["stage"], "failed")
                self.assertIn("log", failed_job["artifacts"])

                # Verify workflow.log artifact endpoint
                log_res = client.get(f"/api/video-workflow/{job_id_failed}/artifacts/log")
                self.assertEqual(log_res.status_code, 200)
                self.assertIn("starting video workflow", log_res.text)

                # 2. Get single job detail
                single_res = client.get(f"/api/video-workflow/{job_id_failed}")
                self.assertEqual(single_res.status_code, 200)
                self.assertEqual(single_res.json()["job"]["job_id"], job_id_failed)
                self.assertEqual(single_res.json()["job"]["track_count"], 2)

                # 3. Resume failed job (succeeds with researched_playlist -> --resume-from-researched)
                resume_res = client.post(f"/api/video-workflow/{job_id_failed}/resume")
                self.assertEqual(resume_res.status_code, 200)
                resumed = resume_res.json()["job"]
                self.assertEqual(resumed["status"], "running")
                self.assertEqual(resumed["pid"], 99881)
                self.assertEqual(resumed["error"], "")
                mock_popen.assert_called_once()
                cmd = mock_popen.call_args[0][0]
                self.assertIn("--resume-from-researched", cmd)

                # 4. Resume raw-only job (succeeds with raw_playlist -> --resume-from-raw)
                mock_popen.reset_mock()
                resume_raw_res = client.post(f"/api/video-workflow/{job_id_raw_only}/resume")
                self.assertEqual(resume_raw_res.status_code, 200)
                mock_popen.assert_called_once()
                raw_cmd = mock_popen.call_args[0][0]
                self.assertIn("--resume-from-raw", raw_cmd)

                # 5. Duplicate resume on running job rejected (409 Conflict)
                conflict_res = client.post(f"/api/video-workflow/{job_id_running}/resume")
                self.assertEqual(conflict_res.status_code, 409)

                # 6. Resume on completed job rejected (400 Bad Request)
                comp_res = client.post(f"/api/video-workflow/{job_id_completed}/resume")
                self.assertEqual(comp_res.status_code, 400)

                # 7. Invalid / non-existent job ID rejected
                self.assertEqual(client.post("/api/video-workflow/invalid_job_format/resume").status_code, 400)
                self.assertEqual(client.post("/api/video-workflow/video_non_existent/resume").status_code, 404)


if __name__ == "__main__":
    unittest.main()
