import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import argparse
from song_discovery.artist_knowledge import (
    ArtistKnowledgeCollector,
    _ascii_log_value,
    default_agy_runner,
    extract_json_payload,
    generate_artist_name_variants,
    run_artist_research,
    sanitize_sources,
    split_artist_names,
)
from song_discovery.cli import cmd_backfill_artist_knowledge
from song_discovery.db import DiscoveryDB
import song_discovery.review_api as review_api
from song_discovery.review_api import app


class ArtistKnowledgeTests(unittest.TestCase):
    def test_sanitize_sources(self):
        # Valid URLs preserved
        valid = ["https://music.163.com/artist?id=123", "https://baike.baidu.com/item/test"]
        self.assertEqual(sanitize_sources(valid), valid)

        # Invalid strings, local paths, fake protocols, example.com placeholders stripped
        mixed = [
            "https://en.wikipedia.org/wiki/Artist",
            "http://example.com/placeholder",
            "https://fakeurl.com",
            "not a url",
            "ftp://files.org",
            "file:///etc/passwd",
            "",
            None,
        ]
        self.assertEqual(sanitize_sources(mixed), ["https://en.wikipedia.org/wiki/Artist"])

        # Non-list input returns empty list
        self.assertEqual(sanitize_sources(None), [])
        self.assertEqual(sanitize_sources("https://example.com"), [])

    def test_extract_json_payload(self):
        # Direct JSON object
        raw_direct = '{"artist_name": "Test", "uncertainty": "low"}'
        self.assertEqual(extract_json_payload(raw_direct)["artist_name"], "Test")

        # Markdown fenced JSON
        raw_fenced = '```json\n{"artist_name": "Test Fenced", "uncertainty": "medium"}\n```'
        self.assertEqual(extract_json_payload(raw_fenced)["artist_name"], "Test Fenced")

        # Nested agy output {"response": "```json ...```"}
        nested = json.dumps({"response": '```json\n{"artist_name": "Test Agy", "uncertainty": "low"}\n```'})
        self.assertEqual(extract_json_payload(nested)["artist_name"], "Test Agy")

        # Invalid JSON returns None
        self.assertIsNone(extract_json_payload("Just some freeform text"))

    def test_db_artist_knowledge_crud_and_backfill_query(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "test.db"))

            # Initially empty
            self.assertIsNone(db.get_artist_knowledge("New Pants"))
            self.assertEqual(db.get_artist_knowledge_batch(["New Pants", "Hedgehog"]), {})

            # Upsert
            record1 = {
                "artist_name": "新裤子",
                "display_name": "新裤子 (New Pants)",
                "factual_summary": "中国摇滚乐队，成立于1996年。",
                "sources": ["https://baike.baidu.com/item/新裤子乐队"],
                "uncertainty": "low",
                "identity_context": {"genre": ["摇滚", "朋克", "新浪潮"], "origin": "北京"},
                "status": "completed",
                "search_queries": ["新裤子"],
            }
            db.upsert_artist_knowledge(record1)

            fetched = db.get_artist_knowledge("新裤子")
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["display_name"], "新裤子 (New Pants)")
            self.assertEqual(fetched["uncertainty"], "low")
            self.assertEqual(fetched["sources"], ["https://baike.baidu.com/item/新裤子乐队"])
            self.assertEqual(fetched["identity_context"]["origin"], "北京")

            # Batch lookup
            batch = db.get_artist_knowledge_batch(["新裤子", "刺猬"])
            self.assertIn("新裤子", batch)
            self.assertNotIn("刺猬", batch)

            # Insert candidate songs to test get_artists_needing_backfill
            db.upsert_candidate(
                platform="netease",
                release_source_id="rel-1",
                track_source_id="trk-1",
                release_title="Album 1",
                track_title="Track 1",
                artist_names="新裤子",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/1",
                track_url="https://example.com/trk/1",
                selection_rule="rule",
                relevance_score=80,
                relevance_reasons=["Hot"],
                raw_metadata={},
            )
            db.upsert_candidate(
                platform="qq",
                release_source_id="rel-2",
                track_source_id="trk-2",
                release_title="Album 2",
                track_title="Track 2",
                artist_names="刺猬乐队",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/2",
                track_url="https://example.com/trk/2",
                selection_rule="rule",
                relevance_score=80,
                relevance_reasons=["Hot"],
                raw_metadata={},
            )

            # "新裤子" is already completed in artist_knowledge; "刺猬乐队" is not
            needing = db.get_artists_needing_backfill(limit=10)
            artist_names = [item["artist_name"] for item in needing]
            self.assertIn("刺猬乐队", artist_names)
            self.assertNotIn("新裤子", artist_names)

    def test_run_artist_research_two_step_logic(self):
        # Case 1: Step 1 returns rich info -> Step 2 is not called
        def mock_agy_step1_rich(cmd, timeout):
            return json.dumps({
                "display_name": "万能青年旅店",
                "factual_summary": "中国石家庄的独立摇滚乐队，于1996年成立，以管乐与诗意歌词著称。",
                "uncertainty": "low",
                "identity_context": {"genre": ["独立摇滚", "民谣摇滚"], "origin": "石家庄"},
                "sources": ["https://baike.baidu.com/item/万能青年旅店"],
            })

        mock_runner = MagicMock(side_effect=mock_agy_step1_rich)
        res = run_artist_research("万能青年旅店", agy_runner=mock_runner)
        self.assertEqual(res["uncertainty"], "low")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(mock_runner.call_count, 1)

        # Case 2: Step 1 returns sparse -> Step 2 runs with song/album context
        calls = []

        def mock_agy_two_step(cmd, timeout):
            calls.append(cmd)
            if len(calls) == 1:
                # Step 1: Sparse result
                return json.dumps({
                    "display_name": "未知小众艺人",
                    "factual_summary": "资料极少",
                    "uncertainty": "high",
                    "identity_context": {},
                    "sources": [],
                })
            else:
                # Step 2: Enriched result with song/album
                return json.dumps({
                    "display_name": "未知小众艺人 (Indie)",
                    "factual_summary": "根据单曲《夜行列车》确认其为卧室流行音乐人。",
                    "uncertainty": "medium",
                    "identity_context": {"genre": ["Bedroom Pop"]},
                    "sources": ["https://example.com/indie-review"],
                })

        mock_runner2 = MagicMock(side_effect=mock_agy_two_step)
        res2 = run_artist_research(
            artist_name="未知小众艺人",
            song_title="夜行列车",
            album_title="首张同名",
            agy_runner=mock_runner2,
        )
        self.assertEqual(mock_runner2.call_count, 2)
        self.assertEqual(res2["uncertainty"], "medium")
        self.assertEqual(res2["status"], "completed")
        self.assertIn("夜行列车", res2["factual_summary"])

    def test_artist_knowledge_collector_dedup_and_caching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "collector_test.db"))

            call_count = 0

            def counting_runner(cmd, timeout):
                nonlocal call_count
                call_count += 1
                return json.dumps({
                    "display_name": "声音玩具",
                    "factual_summary": "中国四川成都的独立摇滚乐队，由欧珈源于1999年创立，音乐风格兼具诗意美感与旋律性。",
                    "uncertainty": "low",
                    "identity_context": {"origin": "成都", "genre": ["独立摇滚"]},
                    "sources": ["https://baike.baidu.com/item/声音玩具"],
                })

            collector = ArtistKnowledgeCollector(
                db=db,
                max_workers=2,
                timeout=10,
                agy_runner=counting_runner,
            )

            # First run: collects and persists
            res1 = collector.collect_now("声音玩具")
            self.assertEqual(res1["status"], "completed")
            self.assertEqual(call_count, 1)

            # Second run: cached in DB, should NOT re-run
            res2 = collector.collect_now("声音玩具")
            self.assertEqual(res2["status"], "completed")
            self.assertEqual(call_count, 1)

            # Force run: bypasses DB cache
            res3 = collector.collect_now("声音玩具", force=True)
            self.assertEqual(call_count, 2)

            collector.shutdown(wait=True)

    def test_api_artist_knowledge_endpoints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_artist.db"
            db = DiscoveryDB(str(db_path))

            # Pre-seed an artist record
            db.upsert_artist_knowledge({
                "artist_name": "重塑雕像的权利",
                "display_name": "重塑雕像的权利 (Re-TROS)",
                "factual_summary": "后朋克、后工业摇滚乐队。",
                "sources": ["https://example.com/retros"],
                "uncertainty": "low",
                "identity_context": {"genre": ["后朋克", "电气后朋"]},
                "status": "completed",
                "search_queries": ["重塑雕像的权利"],
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)

                # GET single artist (found)
                resp = client.get("/api/artist-knowledge", params={"artist_name": "重塑雕像的权利"})
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data["found"])
                self.assertEqual(data["knowledge"]["display_name"], "重塑雕像的权利 (Re-TROS)")

                # GET single artist (not found, with auto_collect=false)
                resp_not_found = client.get(
                    "/api/artist-knowledge",
                    params={"artist_name": "不存在的乐队", "auto_collect": "false"},
                )
                self.assertEqual(resp_not_found.status_code, 200)
                self.assertFalse(resp_not_found.json()["found"])

                # POST batch
                resp_batch = client.post(
                    "/api/artist-knowledge/batch",
                    json={"artist_names": ["重塑雕像的权利", "其他艺人"], "auto_collect_missing": False},
                )
                self.assertEqual(resp_batch.status_code, 200)
                batch_data = resp_batch.json()["items"]
                self.assertIn("重塑雕像的权利", batch_data)
                self.assertNotIn("其他艺人", batch_data)

    def test_cli_backfill_artist_knowledge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cli_artist.db"
            db = DiscoveryDB(str(db_path))
            db.upsert_candidate(
                platform="netease",
                release_source_id="rel-cli",
                track_source_id="trk-cli",
                release_title="CLI Album",
                track_title="CLI Track",
                artist_names="达达乐队",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/cli",
                track_url="https://example.com/trk/cli",
                selection_rule="rule",
                relevance_score=80,
                relevance_reasons=["Hot"],
                raw_metadata={},
            )

            mock_record = {
                "artist_name": "达达乐队",
                "display_name": "达达乐队",
                "factual_summary": "中国著名流行摇滚乐队。",
                "sources": ["https://example.com/dada"],
                "uncertainty": "low",
                "identity_context": {"genre": ["流行摇滚"]},
                "status": "completed",
                "search_queries": ["达达乐队"],
            }

            args = argparse.Namespace(
                db_path=str(db_path),
                limit=5,
                concurrency=1,
                artist=None,
                force=False,
            )
            def mock_collect(artist_name, *c_args, **c_kwargs):
                db.upsert_artist_knowledge(mock_record)
                return mock_record

            with patch(
                "song_discovery.artist_knowledge.ArtistKnowledgeCollector.collect_sync",
                side_effect=mock_collect,
            ):
                cmd_backfill_artist_knowledge(args)
            record = db.get_artist_knowledge("达达乐队")
            self.assertIsNotNone(record)
            self.assertEqual(record["factual_summary"], "中国著名流行摇滚乐队。")

    def test_schoolgirl_byebye_multi_source_recall_regression(self):
        """Benchmark artist 'schoolgirl byebye': regression test ensuring multi-source recall and correct agy invocation."""
        executed_cmds = []

        def mock_agy_runner(cmd, timeout):
            executed_cmds.append(cmd)
            return json.dumps({
                "display_name": "Schoolgirl Byebye",
                "factual_summary": "Schoolgirl Byebye 是一支2015年在中国南京成立的独立摇滚乐队，由杨越与更生仔创立。风格涵盖独立流行、梦幻流行与自赏摇滚，曾获豆瓣阿比鹿音乐奖年度新人奖，代表作品包括《爱是》、《软弱》、《No Romantics in China》等。",
                "sources": [
                    "https://en.wikipedia.org/wiki/Schoolgirl_Byebye",
                    "https://open.spotify.com/artist/4X8YqI8z2cQ4n3m8",
                    "https://www.instagram.com/schoolgirlbyebye/",
                    "https://streetvoice.cn/schoolgirlbyebye/",
                    "https://music.douban.com/musician/123456/",
                    "https://schoolgirlbyebye.bandcamp.com/",
                    "https://www.youtube.com/channel/UCschoolgirlbyebye",
                ],
                "uncertainty": "low",
                "identity_context": {
                    "genre": ["Indie Pop", "Dream Pop", "Shoegaze", "Indie Rock"],
                    "origin": "Nanjing, China",
                    "type": "Band",
                    "members": ["Yang Yue (杨越)", "Geng Shengzai (更生仔)"],
                    "active_years": "2015-present",
                    "notable_works": ["No Romantics in China", "爱是", "软弱"],
                },
                "is_sparse": False,
            })

        res = run_artist_research("schoolgirl byebye", agy_runner=mock_agy_runner)

        # 1. agy flags check: must have --dangerously-skip-permissions to avoid headless permission denial
        self.assertEqual(len(executed_cmds), 1)
        self.assertIn("--dangerously-skip-permissions", executed_cmds[0])
        # Prompt must include multi-source instructions and variants
        prompt_arg = executed_cmds[0][executed_cmds[0].index("-p") + 1]
        self.assertIn("Spotify", prompt_arg)
        self.assertIn("StreetVoice", prompt_arg)
        self.assertIn("Instagram", prompt_arg)
        self.assertIn("Wikipedia", prompt_arg)

        # 2. Result check: high recall, low uncertainty, completed
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["uncertainty"], "low")
        self.assertEqual(res["display_name"], "Schoolgirl Byebye")
        self.assertIn("杨越", res["factual_summary"])
        self.assertIn("更生仔", res["factual_summary"])
        self.assertEqual(len(res["sources"]), 7)
        self.assertTrue(any("spotify.com" in s for s in res["sources"]))
        self.assertTrue(any("instagram.com" in s for s in res["sources"]))
        self.assertTrue(any("streetvoice.cn" in s for s in res["sources"]))
        self.assertTrue(any("wikipedia.org" in s for s in res["sources"]))
        self.assertTrue(any("bandcamp.com" in s for s in res["sources"]))
        self.assertIn("Dream Pop", res["identity_context"]["genre"])

    def test_schoolgirl_byebye_name_variants_and_case_insensitive_db_lookup(self):
        """Verify name variants generation and DB case-insensitive & space/hyphen variant resolution."""
        variants = generate_artist_name_variants("schoolgirl byebye")
        self.assertIn("schoolgirl byebye", variants)
        self.assertIn("Schoolgirl Byebye", variants)
        self.assertTrue(any("Bye Bye" in v or "bye-bye" in v or "byebye" in v for v in variants))

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "variants_test.db"))

            # Stored under title case 'Schoolgirl Byebye'
            db.upsert_artist_knowledge({
                "artist_name": "Schoolgirl Byebye",
                "display_name": "Schoolgirl Byebye",
                "factual_summary": "南京独立摇滚乐队。",
                "sources": ["https://en.wikipedia.org/wiki/Schoolgirl_Byebye"],
                "uncertainty": "low",
                "identity_context": {"genre": ["Dream Pop"]},
                "status": "completed",
                "search_queries": ["Schoolgirl Byebye"],
            })

            # Exact lowercase query
            rec1 = db.get_artist_knowledge("schoolgirl byebye")
            self.assertIsNotNone(rec1)
            self.assertEqual(rec1["artist_name"], "Schoolgirl Byebye")

            # Mixed case query
            rec2 = db.get_artist_knowledge("Schoolgirl byebye")
            self.assertIsNotNone(rec2)
            self.assertEqual(rec2["display_name"], "Schoolgirl Byebye")

            # All uppercase query
            rec3 = db.get_artist_knowledge("SCHOOLGIRL BYEBYE")
            self.assertIsNotNone(rec3)

            # Spacing / hyphen variation query
            rec4 = db.get_artist_knowledge("schoolgirl-byebye")
            self.assertIsNotNone(rec4)

            # Batch lookup case-insensitivity
            batch = db.get_artist_knowledge_batch(["schoolgirl byebye", "Other Artist"])
            self.assertIn("schoolgirl byebye", batch)
            self.assertEqual(batch["schoolgirl byebye"]["artist_name"], "Schoolgirl Byebye")

    def test_search_failure_never_disguised_as_sparse_or_no_info(self):
        """Crucial requirement: search failures must NOT be treated as 'sparse' or 'no public info'."""
        def failing_agy_runner(cmd, timeout):
            # Simulates headless permission denial or empty tool response
            return json.dumps({
                "conversation_id": "test-err",
                "status": "SUCCESS",
                "response": "",
                "denied_actions": [{"action": "read_url"}],
            })

        # Direct research must raise RuntimeError on failure
        with self.assertRaises(RuntimeError):
            run_artist_research("schoolgirl byebye", agy_runner=failing_agy_runner)

        # Collector must record status 'failed' with error, NOT 'sparse' with fake summary
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "failure_test.db"))
            collector = ArtistKnowledgeCollector(
                db=db,
                max_workers=1,
                timeout=10,
                agy_runner=failing_agy_runner,
            )
            rec = collector.collect_sync("schoolgirl byebye")
            self.assertEqual(rec["status"], "failed")
            self.assertTrue(len(rec["error"]) > 0)
            # Must NOT claim the artist has sparse records
            self.assertNotIn("公开网络资料较少", rec["factual_summary"])
            self.assertEqual(rec["factual_summary"], "")
            collector.shutdown(wait=True)

    def test_api_failed_artist_recollection(self):
        """Verify API re-enqueues failed records when auto_collect=True, and reports failed status properly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "api_fail.db"
            db = DiscoveryDB(str(db_path))

            # Pre-seed a failed record
            db.upsert_artist_knowledge({
                "artist_name": "schoolgirl byebye",
                "display_name": "schoolgirl byebye",
                "factual_summary": "",
                "sources": [],
                "uncertainty": "high",
                "identity_context": {},
                "status": "failed",
                "search_queries": ["schoolgirl byebye"],
                "error": "Timeout connecting to search engine",
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)

                # With auto_collect=False, returns found: False, status: 'failed'
                resp1 = client.get("/api/artist-knowledge", params={"artist": "schoolgirl byebye", "auto_collect": "false"})
                self.assertEqual(resp1.status_code, 200)
                data1 = resp1.json()
                self.assertFalse(data1["found"])
                self.assertEqual(data1["status"], "failed")
                self.assertIn("Timeout", data1["error"])

                # With auto_collect=True, it re-enqueues and returns collecting
                with patch("song_discovery.artist_knowledge.ArtistKnowledgeCollector.enqueue_artist", return_value=True):
                    resp2 = client.get("/api/artist-knowledge", params={"artist": "schoolgirl byebye", "auto_collect": "true"})
                    self.assertEqual(resp2.status_code, 200)
                    data2 = resp2.json()
                    self.assertFalse(data2["found"])
                    self.assertEqual(data2["status"], "collecting")

    def test_multi_format_sources_sanitization(self):
        """Ensure sanitize_sources supports dicts, markdown links, strips trailing punctuation, and filters dummy domains."""
        raw_sources = [
            "https://en.wikipedia.org/wiki/Schoolgirl_Byebye.",  # Trailing period
            {"url": "https://open.spotify.com/artist/12345)", "title": "Spotify"},  # Dict format + trailing paren
            "[Instagram](https://www.instagram.com/schoolgirlbyebye/)",  # Markdown link
            "https://streetvoice.cn/schoolgirlbyebye/;",  # Trailing semicolon
            "http://example.com/artist",  # Dummy domain
            "https://fakeurl.com/artist",  # Fake domain
            "not a url",
        ]
        cleaned = sanitize_sources(raw_sources)
        self.assertIn("https://en.wikipedia.org/wiki/Schoolgirl_Byebye", cleaned)
        self.assertIn("https://open.spotify.com/artist/12345", cleaned)
        self.assertIn("https://www.instagram.com/schoolgirlbyebye/", cleaned)
        self.assertIn("https://streetvoice.cn/schoolgirlbyebye/", cleaned)
        self.assertNotIn("http://example.com/artist", cleaned)
        self.assertNotIn("https://fakeurl.com/artist", cleaned)

    def test_hover_readonly_default_and_manual_collect(self):
        """Regression test: Hover/GET for missing artist must not enqueue AGy,
        existing local knowledge is returned directly, and manual collect can still queue.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "readonly_hover.db"
            db = DiscoveryDB(str(db_path))

            # Pre-seed an existing local artist
            db.upsert_artist_knowledge({
                "artist_name": "万能青年旅店",
                "display_name": "万能青年旅店 (Omnipotent Youth Society)",
                "factual_summary": "石家庄独立摇滚乐队。",
                "sources": ["https://example.com/omni"],
                "uncertainty": "low",
                "identity_context": {"genre": ["独立摇滚"]},
                "status": "completed",
                "search_queries": ["万能青年旅店"],
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)

                # 1. Hover/GET missing artist without auto_collect (default) must NOT enqueue AGy
                with patch("song_discovery.artist_knowledge.ArtistKnowledgeCollector.enqueue_artist") as mock_enqueue:
                    resp_missing = client.get("/api/artist-knowledge", params={"artist_name": "未收录新乐队"})
                    self.assertEqual(resp_missing.status_code, 200)
                    data_missing = resp_missing.json()
                    self.assertFalse(data_missing["found"])
                    self.assertIn(data_missing.get("status"), ("not_found", "local_missing"))
                    self.assertTrue(data_missing.get("local_missing"))
                    self.assertEqual(data_missing["artist_name"], "未收录新乐队")
                    mock_enqueue.assert_not_called()

                # 2. Hover/GET existing local artist returns knowledge and does NOT enqueue
                with patch("song_discovery.artist_knowledge.ArtistKnowledgeCollector.enqueue_artist") as mock_enqueue:
                    resp_exist = client.get("/api/artist-knowledge", params={"artist_name": "万能青年旅店"})
                    self.assertEqual(resp_exist.status_code, 200)
                    data_exist = resp_exist.json()
                    self.assertTrue(data_exist["found"])
                    self.assertEqual(data_exist["knowledge"]["display_name"], "万能青年旅店 (Omnipotent Youth Society)")
                    self.assertEqual(data_exist["knowledge"]["status"], "completed")
                    mock_enqueue.assert_not_called()

                # 3. Manual collection endpoint explicitly queues AGy research
                with patch("song_discovery.artist_knowledge.ArtistKnowledgeCollector.enqueue_artist", return_value=True) as mock_enqueue:
                    resp_collect = client.post(
                        "/api/artist-knowledge/collect",
                        json={
                            "artist_name": "未收录新乐队",
                            "song_title": "新曲目",
                            "album_title": "新专辑",
                            "force": True,
                        },
                    )
                    self.assertEqual(resp_collect.status_code, 200)
                    data_collect = resp_collect.json()
                    self.assertTrue(data_collect["success"])
                    self.assertTrue(data_collect["queued"])
                    self.assertEqual(data_collect["artist_name"], "未收录新乐队")
                    mock_enqueue.assert_called_once_with(
                        artist_name="未收录新乐队",
                        song_title="新曲目",
                        album_title="新专辑",
                        force=True,
                    )

    def test_collector_queue_dedup_and_in_flight_tracking(self):
        """Verify collector tracks queued tasks and rejects duplicate enqueues."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "queue_test.db"))

            import time
            def slow_runner(cmd, timeout):
                time.sleep(0.5)
                return json.dumps({
                    "display_name": "落日飞车",
                    "factual_summary": "台湾迷幻流行乐队。",
                    "sources": ["https://example.com/sunset"],
                    "uncertainty": "low",
                    "identity_context": {},
                    "status": "completed",
                })

            collector = ArtistKnowledgeCollector(
                db=db,
                max_workers=1,
                timeout=10,
                agy_runner=slow_runner,
            )

            # Enqueue artist 1
            enqueued1 = collector.enqueue_artist("落日飞车")
            self.assertTrue(enqueued1)
            self.assertTrue(collector.is_active_or_queued("落日飞车"))
            self.assertTrue(collector.is_active_or_queued("Sunset Rollercoaster") is False)

            # Duplicate enqueue while in flight must return False (no duplicate task!)
            enqueued2 = collector.enqueue_artist("落日飞车")
            self.assertFalse(enqueued2)

            # Case insensitivity in dedup
            enqueued3 = collector.enqueue_artist("落日飞车 ")
            self.assertFalse(enqueued3)

            collector.shutdown(wait=True)

    def test_get_artists_needing_backfill_prioritization_and_status_filter(self):
        """Verify get_artists_needing_backfill prioritizes pending over approved over noise,
        and correctly decomposes compound artist names.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "priority_test.db"))

            # 1. Rejected noise candidate
            cid1, _ = db.upsert_candidate(
                platform="netease",
                release_source_id="rel-rej",
                track_source_id="trk-rej",
                release_title="Rej Album",
                track_title="Rej Track",
                artist_names="噪音艺人",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/rej",
                track_url="https://example.com/trk/rej",
                selection_rule="rule",
                relevance_score=30,
                relevance_reasons=[],
                raw_metadata={},
                initial_review_status="machine_filtered",
            )
            db.update_review_status(cid1, "rejected", "Rejected by editor")

            # 2. Approved candidate with compound artists: "落日飞车 / 9m88"
            cid2, _ = db.upsert_candidate(
                platform="netease",
                release_source_id="rel-app",
                track_source_id="trk-app",
                release_title="App Album",
                track_title="App Track",
                artist_names="落日飞车 / 9m88",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/app",
                track_url="https://example.com/trk/app",
                selection_rule="rule",
                relevance_score=85,
                relevance_reasons=[],
                raw_metadata={},
                initial_review_status="pending",
            )
            db.update_review_status(cid2, "approved", "Approved by editor")

            # 3. Pending candidate
            db.upsert_candidate(
                platform="qq",
                release_source_id="rel-pen",
                track_source_id="trk-pen",
                release_title="Pen Album",
                track_title="Pen Track",
                artist_names="待审新星",
                release_type="single",
                release_date="2026-08-29",
                duration_ms=180000,
                track_number=1,
                release_url="https://example.com/rel/pen",
                track_url="https://example.com/trk/pen",
                selection_rule="rule",
                relevance_score=90,
                relevance_reasons=[],
                raw_metadata={},
                initial_review_status="pending",
            )

            # Pre-seed "落日飞车" as completed; "9m88" is missing
            db.upsert_artist_knowledge({
                "artist_name": "落日飞车",
                "display_name": "落日飞车",
                "factual_summary": "台湾迷幻流行乐团。",
                "sources": ["https://example.com/sunset"],
                "uncertainty": "low",
                "status": "completed",
            })

            # Query with status_filter="active" (pending + approved only)
            active_needing = db.get_artists_needing_backfill(limit=50, status_filter="active")
            names = [it["artist_name"] for it in active_needing]

            # Pending candidate ("待审新星") should come first
            self.assertEqual(names[0], "待审新星")
            # Compound "9m88" should be included because it was missing, while "落日飞车" is excluded
            self.assertIn("9m88", names)
            self.assertNotIn("落日飞车", names)
            # "噪音艺人" is in rejected, so it should NOT be in active_needing
            self.assertNotIn("噪音艺人", names)

            # Query with status_filter=None ("all")
            all_needing = db.get_artists_needing_backfill(limit=50)
            all_names = [it["artist_name"] for it in all_needing]
            self.assertIn("噪音艺人", all_names)
            # Pending and approved must still be ordered before rejected
            self.assertLess(all_names.index("待审新星"), all_names.index("噪音艺人"))

    def test_needs_refresh_includes_failed_and_pending(self):
        """Failed or interrupted pending records must return True from needs_refresh."""
        from song_discovery.artist_knowledge import artist_knowledge_needs_refresh

        self.assertTrue(artist_knowledge_needs_refresh(None))
        self.assertTrue(artist_knowledge_needs_refresh({"status": "failed"}))
        self.assertTrue(artist_knowledge_needs_refresh({"status": "pending"}))
        self.assertFalse(artist_knowledge_needs_refresh({"status": "completed", "sources": ["https://example.com"]}))

    def test_sqlite_queue_marker_and_profile_preservation(self):
        """Verify mark_artist_pending writes pending, prevents duplicate dispatch, and preserves profile fields."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DiscoveryDB(str(Path(temp_dir) / "marker_test.db"))

            # 1. Missing artist: marks pending and returns True
            claimed1 = db.mark_artist_pending("新晋乐队")
            self.assertTrue(claimed1)
            rec1 = db.get_artist_knowledge("新晋乐队")
            self.assertIsNotNone(rec1)
            self.assertEqual(rec1["status"], "pending")

            # 2. Already pending: duplicate dispatch prevented, returns False
            claimed_dup = db.mark_artist_pending("新晋乐队")
            self.assertFalse(claimed_dup)
            claimed_dup_force = db.mark_artist_pending("新晋乐队", force=True)
            self.assertFalse(claimed_dup_force)

            # An interrupted pending marker is reclaimable after its lease;
            # this is how the daemon resumes jobs left behind by a crash or
            # an AGy process failure without allowing immediate duplicates.
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE artist_knowledge SET updated_at=?, last_checked_at=? WHERE artist_name=?",
                    ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "新晋乐队"),
                )
                conn.commit()
            self.assertTrue(db.mark_artist_pending("新晋乐队"))

            # 3. Failed artist with existing profile fields: updates to pending and preserves all fields
            db.upsert_artist_knowledge({
                "artist_name": "故障乐队",
                "display_name": "故障乐队 (Glitch Band)",
                "factual_summary": "先前检索到的部分简介。",
                "sources": ["https://example.com/glitch"],
                "uncertainty": "high",
                "identity_context": {"genre": ["电子"]},
                "status": "failed",
                "search_queries": ["故障乐队"],
                "error": "Timeout connecting to search engine",
            })
            claimed_failed = db.mark_artist_pending("故障乐队")
            self.assertTrue(claimed_failed)
            rec_after = db.get_artist_knowledge("故障乐队")
            self.assertEqual(rec_after["status"], "pending")
            self.assertEqual(rec_after["display_name"], "故障乐队 (Glitch Band)")
            self.assertEqual(rec_after["factual_summary"], "先前检索到的部分简介。")
            self.assertEqual(rec_after["sources"], ["https://example.com/glitch"])
            self.assertEqual(rec_after["identity_context"], {"genre": ["电子"]})
            self.assertEqual(rec_after["search_queries"], ["故障乐队"])

            # 4. Status collecting: duplicate dispatch prevented, returns False
            db.upsert_artist_knowledge({
                "artist_name": "采集中艺人",
                "display_name": "采集中艺人",
                "status": "collecting",
            })
            self.assertFalse(db.mark_artist_pending("采集中艺人"))
            self.assertFalse(db.mark_artist_pending("采集中艺人", force=True))

    def test_cross_process_collector_idempotency_via_sqlite(self):
        """Simulate supervisor and uvicorn in separate processes sharing SQLite:
        Process 1 enqueues artist, Process 2 cannot duplicate dispatch.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "cross_proc.db")
            db1 = DiscoveryDB(db_path)
            db2 = DiscoveryDB(db_path)

            import time
            def slow_runner(cmd, timeout):
                time.sleep(0.3)
                return json.dumps({
                    "display_name": "慢速乐队",
                    "factual_summary": "慢速收集完成测试。",
                    "sources": ["https://example.com/slow"],
                    "uncertainty": "low",
                    "status": "completed",
                })

            # Process 1 collector (e.g. supervisor)
            collector1 = ArtistKnowledgeCollector(db=db1, max_workers=1, agy_runner=slow_runner)
            # Process 2 collector (e.g. uvicorn with separate memory sets)
            collector2 = ArtistKnowledgeCollector(db=db2, max_workers=1, agy_runner=slow_runner)

            # Process 1 enqueues artist
            self.assertTrue(collector1.enqueue_artist("慢速乐队"))
            # SQLite immediately has status='pending'
            rec_pending = db2.get_artist_knowledge("慢速乐队")
            self.assertIsNotNone(rec_pending)
            self.assertEqual(rec_pending["status"], "pending")

            # Process 2 (empty memory) attempts to enqueue same artist: rejected by SQLite idempotency
            self.assertFalse(collector2.enqueue_artist("慢速乐队"))
            self.assertFalse(collector2.enqueue_artist("慢速乐队", force=True))

            # Wait for Process 1 to finish
            collector1.shutdown(wait=True)
            collector2.shutdown(wait=True)

            # Once complete, SQLite is overwritten with 'completed'
            rec_done = db2.get_artist_knowledge("慢速乐队")
            self.assertEqual(rec_done["status"], "completed")
            self.assertEqual(rec_done["factual_summary"], "慢速收集完成测试。")

    def test_get_artist_knowledge_endpoint_returns_collecting_on_pending(self):
        """Verify GET /api/artist-knowledge returns status: 'collecting' when SQLite record is pending or collecting."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pending_hover.db"
            db = DiscoveryDB(str(db_path))

            # 1. Pending artist (e.g. enqueued by supervisor in background)
            db.upsert_artist_knowledge({
                "artist_name": "排队新星",
                "display_name": "排队新星",
                "status": "pending",
            })

            # 2. Collecting artist
            db.upsert_artist_knowledge({
                "artist_name": "正在检索艺人",
                "display_name": "正在检索艺人",
                "status": "collecting",
            })

            with patch.dict(os.environ, {"DISCOVERY_DB_PATH": str(db_path)}):
                client = TestClient(app)

                # GET pending artist without auto_collect: must return status 'collecting'
                resp_pen = client.get("/api/artist-knowledge", params={"artist": "排队新星"})
                self.assertEqual(resp_pen.status_code, 200)
                data_pen = resp_pen.json()
                self.assertFalse(data_pen["found"])
                self.assertEqual(data_pen["status"], "collecting")
                self.assertEqual(data_pen["artist_name"], "排队新星")

                # GET collecting artist without auto_collect: must return status 'collecting'
                resp_col = client.get("/api/artist-knowledge", params={"artist": "正在检索艺人"})
                self.assertEqual(resp_col.status_code, 200)
                data_col = resp_col.json()
                self.assertFalse(data_col["found"])
                self.assertEqual(data_col["status"], "collecting")
                self.assertEqual(data_col["artist_name"], "正在检索艺人")

    def test_default_agy_runner_uses_prompt_file_under_ascii_filesystem_encoding(self):
        """Chinese artist prompts must not be passed directly in argv under ASCII launchd locales."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs["env"]
            for arg in cmd:
                str(arg).encode("ascii")
            prompt_path = cmd[5]
            with open(prompt_path, "r", encoding="utf-8") as handle:
                captured["prompt"] = handle.read()
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"response": "{\"display_name\":\"万能青年旅店\"}"}).encode("utf-8"),
                stderr="".encode("utf-8"),
            )

        original_cmd = [
            "/Users/oshiki/.local/bin/agy",
            "-p",
            "Research artist 万能青年旅店 and return 中文 JSON",
            "--model",
            "gemini-3.8-flash-high",
            "--output-format",
            "json",
        ]

        with patch("sys.getfilesystemencoding", return_value="ascii"), patch(
            "song_discovery.artist_knowledge.subprocess.run",
            side_effect=fake_run,
        ):
            output = default_agy_runner(original_cmd, timeout=10)

        self.assertIn("万能青年旅店", captured["prompt"])
        self.assertEqual(captured["cmd"][0:4], ["/bin/sh", "-c", captured["cmd"][2], "artist-agy-runner"])
        self.assertIn("PYTHONUTF8", captured["env"])
        self.assertEqual(captured["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(captured["env"]["LC_ALL"], "C.UTF-8")
        self.assertEqual(captured["env"]["LC_CTYPE"], "C.UTF-8")
        self.assertEqual(captured["env"]["LANG"], "C.UTF-8")
        self.assertEqual(json.loads(output)["response"], "{\"display_name\":\"万能青年旅店\"}")

    def test_default_agy_runner_uses_prompt_file_for_unicode_even_when_python_reports_utf8(self):
        """launchd may cache a conflicting locale; always isolate CJK prompts from argv."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            for arg in cmd:
                str(arg).encode("ascii")
            prompt_path = cmd[5]
            with open(prompt_path, "r", encoding="utf-8") as handle:
                captured["prompt"] = handle.read()
            return subprocess.CompletedProcess(cmd, 0, stdout=b"{}", stderr=b"")

        with patch("sys.getfilesystemencoding", return_value="utf-8"), patch(
            "song_discovery.artist_knowledge.subprocess.run", side_effect=fake_run
        ):
            default_agy_runner(
                ["/Users/oshiki/.local/bin/agy", "-p", "研究 黃博", "--model", "gemini-3.8-flash-high"],
                timeout=10,
            )

        self.assertEqual(captured["prompt"], "研究 黃博")
        self.assertEqual(captured["cmd"][0], "/bin/sh")

    def test_default_agy_runner_decodes_non_utf8_stderr_without_losing_failure_semantics(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                2,
                stdout=b"",
                stderr=b"bad byte: \xff",
            )

        with patch("song_discovery.artist_knowledge.subprocess.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                default_agy_runner(["/Users/oshiki/.local/bin/agy", "-p", "ascii prompt"], timeout=10)

        self.assertIn("agy exited with code 2", str(ctx.exception))
        self.assertIn("\ufffd", str(ctx.exception))

    def test_ascii_safe_log_value_handles_chinese_artist_names(self):
        escaped = _ascii_log_value("万能青年旅店")
        escaped.encode("ascii")
        self.assertIn("\\u4e07", escaped)
