"""Unit tests for the deterministic publish copy generator and artifact lifecycle."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from song_discovery.publish_copy import (
    build_structural_judgment,
    derive_week_info,
    generate_publish_copy,
    get_headline_artists,
    get_track_artist_text,
    get_track_release_title,
    get_track_release_type,
    is_album_or_ep,
    parse_week_from_playlist_name,
    write_publish_copy_artifacts,
)


class TestPublishCopy(unittest.TestCase):
    def test_parse_week_from_playlist_name(self):
        self.assertEqual(
            parse_week_from_playlist_name("华语新歌周刊 2026年第35周"),
            (2026, 35),
        )
        self.assertEqual(
            parse_week_from_playlist_name("2025年第1周"),
            (2025, 1),
        )
        self.assertIsNone(parse_week_from_playlist_name("华语摇滚周刊"))
        self.assertIsNone(parse_week_from_playlist_name(""))

    def test_derive_week_info_2026_week_35(self):
        info = derive_week_info(playlist_name="华语新歌周刊 2026年第35周")
        self.assertEqual(info["year"], 2026)
        self.assertEqual(info["week"], 35)
        self.assertEqual(info["month"], 8)
        self.assertEqual(info["week_of_month"], 5)
        self.assertEqual(info["date_range_str"], "08.24-08.30")
        self.assertEqual(info["summary_str"], "2026年第35周（08.24-08.30，8月第5周）")

    def test_derive_week_info_fallback_to_tracks(self):
        # 1787673600000 ms is 2026-08-25 UTC (week 35 of 2026)
        tracks = [{"release_publish_time": 1787673600000}]
        info = derive_week_info(playlist_name="华语新歌周刊", tracks=tracks)
        self.assertEqual(info["year"], 2026)
        self.assertEqual(info["week"], 35)
        self.assertEqual(info["month"], 8)
        self.assertEqual(info["week_of_month"], 5)
        self.assertEqual(info["date_range_str"], "08.24-08.30")

    def test_derive_week_info_fallback_to_release_date_str(self):
        tracks = [{"release_date": "2026-08-25"}]
        info = derive_week_info(playlist_name=None, tracks=tracks)
        self.assertEqual(info["year"], 2026)
        self.assertEqual(info["week"], 35)
        self.assertEqual(info["month"], 8)
        self.assertEqual(info["week_of_month"], 5)

    def test_headline_artists_first_three_unique(self):
        tracks = [
            {"artists": ["乐队A"]},
            {"artists": ["乐队B", "乐队C"]},
            {"artists": ["乐队A"]},  # duplicate
            {"artists": ["乐队D"]},
        ]
        artists = get_headline_artists(tracks, limit=3)
        self.assertEqual(artists, ["乐队A", "乐队B", "乐队C"])

    def test_headline_artists_fewer_than_limit(self):
        tracks = [
            {"artists": ["乐队A"]},
            {"artists": ["乐队A"]},
        ]
        artists = get_headline_artists(tracks, limit=3)
        self.assertEqual(artists, ["乐队A"])

    def test_title_selection_rules(self):
        # 1. Album: must prioritize release_title / album over song name
        album_track = {
            "name": "歌曲名",
            "album": "全长专辑名",
            "release_title": "发行专辑名",
            "normalized_release_type": "专辑",
        }
        self.assertEqual(get_track_release_title(album_track), "发行专辑名")

        album_track2 = {
            "name": "歌曲名",
            "album": "全长专辑名",
            "normalized_release_type": "专辑",
        }
        self.assertEqual(get_track_release_title(album_track2), "全长专辑名")

        # 2. EP: must prioritize release_title / album
        ep_track = {
            "name": "EP内单曲名",
            "album": "EP名",
            "normalized_release_type": "EP",
        }
        self.assertEqual(get_track_release_title(ep_track), "EP名")

        # 3. Single: MUST use song name
        single_track = {
            "name": "单曲歌名",
            "album": "同名单曲专辑名",
            "release_title": "同名单曲发行名",
            "normalized_release_type": "单曲",
        }
        self.assertEqual(get_track_release_title(single_track), "单曲歌名")

    def test_structural_judgments(self):
        self.assertEqual(
            build_structural_judgment(total=10, album_count=0, ep_count=0, single_count=10),
            "本周全部为单曲发行，呈现高频单曲更新节奏。",
        )
        self.assertEqual(
            build_structural_judgment(total=5, album_count=3, ep_count=2, single_count=0),
            "本周全部为长碟作品，呈现完整的专辑企划释放。",
        )
        self.assertEqual(
            build_structural_judgment(total=10, album_count=6, ep_count=1, single_count=3),
            "本周全长专辑集中释放，发行重心向完整作品倾斜。",
        )
        self.assertEqual(
            build_structural_judgment(total=10, album_count=5, ep_count=0, single_count=5),
            "本周专辑与单曲数量并重，既有即时单曲更新，也有完整作品释出。",
        )
        self.assertEqual(
            build_structural_judgment(total=12, album_count=2, ep_count=1, single_count=9),
            "本周发行以单曲为主，同时伴有长碟作品的释出。",
        )

    def test_full_publish_copy_generation_format_and_order(self):
        sample_data = {
            "playlist_name": "华语新歌周刊 2026年第35周",
            "tracks": [
                {
                    "name": "古斯特洛夫号(On Board)",
                    "artists": ["浅水ShallowEnd"],
                    "album": "容器（Nothing Left）",
                    "release_title": "容器（Nothing Left）",
                    "normalized_release_type": "专辑",
                },
                {
                    "name": "很久",
                    "artists": ["新学校合唱团"],
                    "album": "很久",
                    "normalized_release_type": "单曲",
                },
                {
                    "name": "原厂任性",
                    "artists": ["Hyper Slash"],
                    "album": "原厂任性",
                    "normalized_release_type": "单曲",
                },
                {
                    "name": "一直飞行在大海之上",
                    "artists": ["幽瀛"],
                    "album": "幽瀛：记忆渤海湾",
                    "normalized_release_type": "EP",
                },
            ],
        }
        copy_text = generate_publish_copy(sample_data)
        lines = copy_text.strip().split("\n")

        # Line 1: Headline with first 3 unique artists
        self.assertEqual(lines[0], "# 浅水ShallowEnd、新学校合唱团、Hyper Slash等乐队新歌电台来了！")

        # Line 3: Week + counts + judgment
        self.assertEqual(
            lines[2],
            "本期为2026年第35周（08.24-08.30，8月第5周），共收录 4 组发行，包含 1 张专辑、1 张EP、2 首单曲。本周发行以单曲为主，同时伴有长碟作品的释出。",
        )

        # Line 5: Detail marker
        self.assertEqual(lines[4], "🔴本周发行详情：")

        # Order must match original tracks
        self.assertEqual(lines[5], "·浅水ShallowEnd发行[专辑] 《容器（Nothing Left）》")
        self.assertEqual(lines[6], "·新学校合唱团发行[单曲] 《很久》")
        self.assertEqual(lines[7], "·Hyper Slash发行[单曲] 《原厂任性》")
        self.assertEqual(lines[8], "·幽瀛发行[EP] 《幽瀛：记忆渤海湾》")

    def test_write_publish_copy_artifacts_lifecycle_and_protection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            initial_text = "# Title V1\n\nBody V1"

            # 1. First run: writes both publish_copy.auto.md and publish_copy.md
            auto_path, editable_path = write_publish_copy_artifacts(job_dir, initial_text)
            self.assertTrue(auto_path.is_file())
            self.assertTrue(editable_path.is_file())
            self.assertEqual(auto_path.read_text(encoding="utf-8"), initial_text)
            self.assertEqual(editable_path.read_text(encoding="utf-8"), initial_text)

            # 2. Rerun when editable is NOT modified by user: updates both
            v2_text = "# Title V2\n\nBody V2"
            write_publish_copy_artifacts(job_dir, v2_text)
            self.assertEqual(auto_path.read_text(encoding="utf-8"), v2_text)
            self.assertEqual(editable_path.read_text(encoding="utf-8"), v2_text)

            # 3. User customizes editable draft
            custom_user_text = "# Custom User Title\n\nCustom content"
            editable_path.write_text(custom_user_text, encoding="utf-8")

            # 4. Rerun with new auto draft: auto is updated, BUT editable is PRESERVED
            v3_text = "# Title V3\n\nBody V3"
            write_publish_copy_artifacts(job_dir, v3_text)
            self.assertEqual(auto_path.read_text(encoding="utf-8"), v3_text)
            self.assertEqual(editable_path.read_text(encoding="utf-8"), custom_user_text)

            # 5. Overwrite explicitly forced: editable is updated
            write_publish_copy_artifacts(job_dir, v3_text, overwrite_editable=True)
            self.assertEqual(editable_path.read_text(encoding="utf-8"), v3_text)


if __name__ == "__main__":
    unittest.main()
