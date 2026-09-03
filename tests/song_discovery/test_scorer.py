"""Unit tests for RelevanceScorer."""

import unittest
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track
from song_discovery.scorer import RelevanceScorer


class TestRelevanceScorer(unittest.TestCase):
    def setUp(self):
        self.scorer = RelevanceScorer()

    def _make_track(self, title: str, artist_name: str, album_title: str) -> Track:
        artist = Artist(name=artist_name)
        return Track(
            platform="qq",
            source_id="T1",
            title=title,
            artists=[artist],
            album_title=album_title,
            release_type="album",
            release_date="2026-08-01",
        )

    def test_standard_music_gets_baseline_score(self):
        """Standard pop/indie music gets baseline 50.0 score and is kept as candidate."""
        track = self._make_track(title="普通流行歌", artist_name="陈奕迅", album_title="标准专辑")
        result = self.scorer.score_candidate(track)
        self.assertEqual(result.score, 50.0)
        self.assertTrue(result.is_candidate)
        self.assertTrue(any("基准分" in r for r in result.reasons))

    def test_band_and_rock_keywords_boost_score(self):
        """Band artist name and rock keywords boost relevance score."""
        track = self._make_track(title="摇滚序曲 (Live)", artist_name="新裤子乐队", album_title="生命因你而火热")
        result = self.scorer.score_candidate(track)
        self.assertGreaterEqual(result.score, 90.0)
        self.assertTrue(result.is_candidate)
        reasons_text = " ".join(result.reasons)
        self.assertIn("乐队", reasons_text)
        self.assertIn("摇滚", reasons_text)
        self.assertIn("现场", reasons_text)

    def test_hard_exclusion_for_spoken_audiobook_white_noise(self):
        """Spoken/Audiobook/White Noise utility material is hard-excluded."""
        tracks = [
            self._make_track("第01集 斗罗大陆", "有声书演播室", "斗罗大陆有声书"),
            self._make_track("深度睡眠白噪音 8小时", "助眠大师", "自然流水雨声"),
            self._make_track("郭德纲于谦相声精选", "德云社", "相声精选集"),
            self._make_track("3D沉浸式ASMR耳语", "助眠小仙女", "ASMR催眠系列"),
        ]

        for trk in tracks:
            result = self.scorer.score_candidate(trk)
            self.assertFalse(result.is_candidate, f"Failed for {trk.title}")
            self.assertLessEqual(result.score, 10.0)
            self.assertTrue(any("排除" in r for r in result.reasons))


if __name__ == "__main__":
    unittest.main()
