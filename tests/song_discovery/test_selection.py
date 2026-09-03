"""Unit tests for unified track selection rules and rule labeling."""

import unittest
from song_discovery.exceptions import EmptyReleaseError
from song_discovery.models import Artist, Platform, Release, ReleaseType, Track
from song_discovery.selection import (
    get_selection_rule_label,
    select_target_track,
    select_track_from_release,
)


class TestSelectionRules(unittest.TestCase):
    def setUp(self):
        self.artist = Artist(name="测试歌手", id="A1")

    def _create_track(self, index: int, title: str) -> Track:
        return Track(
            platform=Platform.NETEASE.value,
            source_id=f"T_{index}",
            title=title,
            artists=[self.artist],
            album_title="测试发行",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-01",
            track_number=index,
            source_url=f"https://example.com/track/{index}",
        )

    def test_single_release_with_two_tracks_selects_first(self):
        """Rule: release_type == 'single' with 2 tracks (main + accompaniment) selects track 1."""
        track1 = self._create_track(1, "主打单曲原曲")
        track2 = self._create_track(2, "主打单曲 (伴奏)")
        release = Release(
            platform=Platform.NETEASE.value,
            source_id="REL_SINGLE_2",
            title="双曲单曲发行",
            artists=[self.artist],
            album_title="双曲单曲发行",
            release_type=ReleaseType.SINGLE.value,
            release_date="2026-08-01",
            track_count=2,
            tracks=[track1, track2],
        )

        selected = select_track_from_release(release)
        self.assertEqual(selected.source_id, "T_1")
        self.assertEqual(selected.title, "主打单曲原曲")
        self.assertEqual(selected.track_number, 1)

        label = get_selection_rule_label(release)
        self.assertEqual(label, "single_main_track (1/2)")

    def test_single_track_release_selects_first(self):
        """Rule: If track_count == 1, pick the only track (index 0)."""
        track1 = self._create_track(1, "唯一主打单曲")
        release = Release(
            platform=Platform.QQ.value,
            source_id="REL_SINGLE_1",
            title="单曲发行",
            artists=[self.artist],
            album_title="单曲发行",
            release_type=ReleaseType.SINGLE.value,
            release_date="2026-08-01",
            track_count=1,
            tracks=[track1],
        )

        selected = select_track_from_release(release)
        self.assertEqual(selected.source_id, "T_1")
        self.assertEqual(selected.title, "唯一主打单曲")
        self.assertEqual(select_target_track(release).source_id, "T_1")

        label = get_selection_rule_label(release)
        self.assertEqual(label, "only_track (1/1)")

    def test_two_track_ep_selects_second(self):
        """Rule: EP with 2 tracks selects physical order 2nd track (index 1)."""
        track1 = self._create_track(1, "第一首先行曲")
        track2 = self._create_track(2, "第二首主打曲")
        release = Release(
            platform=Platform.NETEASE.value,
            source_id="REL_EP_2",
            title="双单曲EP",
            artists=[self.artist],
            album_title="双单曲EP",
            release_type=ReleaseType.EP.value,
            release_date="2026-08-01",
            track_count=2,
            tracks=[track1, track2],
        )

        selected = select_track_from_release(release)
        self.assertEqual(selected.source_id, "T_2")
        self.assertEqual(selected.title, "第二首主打曲")
        self.assertEqual(selected.track_number, 2)

        label = get_selection_rule_label(release)
        self.assertEqual(label, "album_or_ep_2nd_track (2/2)")

    def test_multi_track_album_selects_second(self):
        """Rule: Full album (e.g. 10 tracks) picks physical 2nd track (index 1)."""
        tracks = [self._create_track(i, f"曲目 {i}") for i in range(1, 11)]
        release = Release(
            platform=Platform.KKBOX.value,
            source_id="REL_ALBUM",
            title="十首完整大碟",
            artists=[self.artist],
            album_title="十首完整大碟",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-01",
            track_count=10,
            tracks=tracks,
        )

        selected = select_track_from_release(release)
        self.assertEqual(selected.source_id, "T_2")
        self.assertEqual(selected.title, "曲目 2")
        self.assertEqual(selected.track_number, 2)

        label = get_selection_rule_label(release)
        self.assertEqual(label, "album_or_ep_2nd_track (2/10)")

    def test_one_track_album_selects_first(self):
        """Rule: Album with only 1 track selects that only track."""
        track1 = self._create_track(1, "先行曝光专辑单曲")
        release = Release(
            platform=Platform.NETEASE.value,
            source_id="REL_ALBUM_1",
            title="单曲专辑",
            artists=[self.artist],
            album_title="单曲专辑",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-01",
            track_count=1,
            tracks=[track1],
        )

        selected = select_track_from_release(release)
        self.assertEqual(selected.source_id, "T_1")
        label = get_selection_rule_label(release)
        self.assertEqual(label, "only_track (1/1)")

    def test_empty_release_raises_error(self):
        """Rule: If release has 0 tracks, raises EmptyReleaseError."""
        release = Release(
            platform=Platform.QQ.value,
            source_id="REL_EMPTY",
            title="空专辑",
            artists=[self.artist],
            album_title="空专辑",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-01",
            track_count=0,
            tracks=[],
        )

        with self.assertRaises(EmptyReleaseError) as ctx:
            select_track_from_release(release)
        self.assertIn("REL_EMPTY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
