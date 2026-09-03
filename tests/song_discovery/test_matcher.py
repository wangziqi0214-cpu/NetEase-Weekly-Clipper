"""Unit tests for TrackMatcher cross-platform matching."""

import unittest
from song_discovery.matcher import TrackMatcher, clean_search_title, generate_search_queries, normalize_title


class TestTrackMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = TrackMatcher()

    def test_confident_exact_match(self):
        candidates = [
            {
                "id": 99001,
                "name": "生命因你而火热 (Live)",
                "ar": [{"id": 10, "name": "新裤子"}],
                "dt": 240000,
            }
        ]
        res = self.matcher.match(
            target_title="生命因你而火热 (Live)",
            target_artists="新裤子",
            target_duration_ms=241000,
            search_candidates=candidates,
        )
        self.assertEqual(res.status, "matched")
        self.assertEqual(res.netease_track_id, "99001")
        self.assertGreaterEqual(res.confidence, 0.85)

    def test_traditional_and_simplified_titles_compare_equal(self):
        self.assertEqual(normalize_title("螢火蟲"), normalize_title("萤火虫"))
        self.assertEqual(normalize_title("南門町"), normalize_title("南门町"))

    def test_search_queries_include_simplified_core_title_without_suffix(self):
        queries = generate_search_queries("鹹酸苦汫 - 電影《網紅老爸》片尾曲", "麋先生 聖皓")
        self.assertIn("咸酸苦汫 麋先生 圣皓", queries)
        self.assertEqual(clean_search_title("追光行者 - Demo"), "追光行者")
        self.assertIn("追光行者 5D香蕉乐队", generate_search_queries("追光行者 - Demo", "5D香蕉樂隊"))

    def test_traditional_source_matches_simplified_netease_result(self):
        result = self.matcher.match(
            target_title="螢火蟲",
            target_artists="蘇菲花園樂隊",
            target_duration_ms=210000,
            search_candidates=[{"id": 301, "name": "萤火虫", "ar": [{"name": "苏菲花园乐队"}], "dt": 211000}],
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.netease_track_id, "301")

    def test_demo_does_not_auto_match_studio_version(self):
        result = self.matcher.match(
            target_title="追光行者 - Demo",
            target_artists="5D香蕉樂隊",
            target_duration_ms=210000,
            search_candidates=[{"id": 302, "name": "追光行者", "ar": [{"name": "5D香蕉乐队"}], "dt": 210000}],
        )
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.netease_track_id)

    def test_ambiguous_match_detection(self):
        # Two top candidates with virtually identical names and close scores
        candidates = [
            {
                "id": 101,
                "name": "梦醒时分",
                "ar": [{"id": 1, "name": "陈淑桦"}],
                "dt": 240000,
            },
            {
                "id": 102,
                "name": "梦醒时分",
                "ar": [{"id": 1, "name": "陈淑桦"}],
                "dt": 242000,
            },
        ]
        res = self.matcher.match(
            target_title="梦醒时分",
            target_artists="陈淑桦",
            target_duration_ms=240000,
            search_candidates=candidates,
        )
        self.assertEqual(res.status, "ambiguous")
        self.assertIsNone(res.netease_track_id)
        self.assertTrue(any("存疑" in r for r in res.reasons))

    def test_unmatched_low_similarity(self):
        candidates = [
            {
                "id": 201,
                "name": "完全不相关的歌曲",
                "ar": [{"id": 2, "name": "某歌手"}],
                "dt": 180000,
            }
        ]
        res = self.matcher.match(
            target_title="火车驶向云外",
            target_artists="刺猬乐队",
            target_duration_ms=280000,
            search_candidates=candidates,
        )
        self.assertEqual(res.status, "unmatched")
        self.assertIsNone(res.netease_track_id)


if __name__ == "__main__":
    unittest.main()
