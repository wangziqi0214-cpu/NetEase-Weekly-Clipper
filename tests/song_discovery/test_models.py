"""Unit tests for unified metadata models."""

import unittest
from song_discovery.models import Artist, Track, Release, Platform, ReleaseType


class TestModels(unittest.TestCase):
    def test_artist_model(self):
        artist = Artist(name="周杰伦", id="12345", raw_metadata={"mid": "002J4UUk29y8BY"})
        self.assertEqual(artist.name, "周杰伦")
        self.assertEqual(artist.id, "12345")

        d_with_raw = artist.to_dict(include_raw=True)
        self.assertIn("raw_metadata", d_with_raw)
        self.assertEqual(d_with_raw["raw_metadata"]["mid"], "002J4UUk29y8BY")

        d_no_raw = artist.to_dict(include_raw=False)
        self.assertNotIn("raw_metadata", d_no_raw)

    def test_track_and_release_model(self):
        artists = [Artist(name="林俊杰", id="101"), Artist(name="王嘉尔", id="102")]
        track1 = Track(
            platform=Platform.QQ.value,
            source_id="T001",
            title="曲目一",
            artists=artists,
            album_title="新专辑",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-20",
            duration_ms=210000,
            track_number=1,
            source_url="https://y.qq.com/n/ryqq/songDetail/T001",
        )
        track2 = Track(
            platform=Platform.QQ.value,
            source_id="T002",
            title="曲目二",
            artists=artists,
            album_title="新专辑",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-20",
            duration_ms=220000,
            track_number=2,
            source_url="https://y.qq.com/n/ryqq/songDetail/T002",
        )
        release = Release(
            platform=Platform.QQ.value,
            source_id="A001",
            title="新专辑",
            artists=artists,
            album_title="新专辑",
            release_type=ReleaseType.ALBUM.value,
            release_date="2026-08-20",
            track_count=2,
            source_url="https://y.qq.com/n/ryqq/albumDetail/A001",
            tracks=[track1, track2],
        )

        self.assertEqual(track1.artist_names, ["林俊杰", "王嘉尔"])
        self.assertEqual(track1.artist_names_str, "林俊杰 / 王嘉尔")
        self.assertEqual(release.artist_names_str, "林俊杰 / 王嘉尔")

        rel_dict = release.to_dict(include_raw=False)
        self.assertEqual(rel_dict["title"], "新专辑")
        self.assertEqual(len(rel_dict["tracks"]), 2)
        self.assertEqual(rel_dict["tracks"][0]["title"], "曲目一")
        self.assertEqual(rel_dict["tracks"][1]["title"], "曲目二")


if __name__ == "__main__":
    unittest.main()
