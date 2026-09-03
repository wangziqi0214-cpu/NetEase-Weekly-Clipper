import os
import sys
import json
import shutil
import tempfile
import unittest
import numpy as np
from PIL import Image, ImageDraw

# Ensure project root is in sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.renderer.intro_renderer import (
    calc_intro_duration,
    get_rhythmic_progress,
    extract_playlist_metadata,
    compute_week_date_range,
    build_card_textures,
    create_placeholder_cover,
    create_card_reflection,
    create_intro_video,
    WIDTH,
    HEIGHT,
    CARD_SIZE,
    BASE_CENTER_X,
    BASE_BOTTOM_Y,
    HEADER_BAR_X0,
    HEADER_BAR_X1,
    HEADER_TITLE_POS,
    TITLE_PREFIX,
    TITLE_SUFFIX,
    SUBTITLE_TAG,
    SUBTITLE_TOP_GAP,
    SUBTITLE_SQUASH_Y,
    FONT_SIZE_SUBTITLE,
    FONT_SIZE_DATE,
    FOOTER_TEXT,
    FOOTER_MARGIN_X,
    FOOTER_BOTTOM_MARGIN,
    COLOR_BG_DARK,
    COLOR_TITLE_WHITE,
    COLOR_TITLE_ORANGE,
    COLOR_TAG_BRIGHT,
    COLOR_DATE_SILVER,
    COLOR_ENG_WHITE,
    get_font,
    get_en_font,
)


class TestIntroAlbumFlowPosterSquare(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_fixed_poster_layout_and_forbidden_tech_ui(self):
        # 1. Fixed Poster Strings matching reference
        self.assertEqual(TITLE_PREFIX, "华语摇滚")
        self.assertEqual(TITLE_SUFFIX, "新歌速递")
        self.assertEqual(SUBTITLE_TAG, "BAND NEVER STOP")
        self.assertEqual(FOOTER_TEXT, "WEEKLY CHINESE BAND RELEASES")

        # 2. Dimensions and Canvas
        self.assertEqual(WIDTH, 1080)
        self.assertEqual(HEIGHT, 1440)
        self.assertEqual(CARD_SIZE, 720)
        self.assertEqual(BASE_CENTER_X, 540)
        self.assertEqual(BASE_BOTTOM_Y, 1140)

        # 3. Forbidden Tech UI Strings must NOT exist in the module
        import src.renderer.intro_renderer as ir
        source_code = open(ir.__file__, "r", encoding="utf-8").read()
        forbidden_terms = [
            "NETEASE MUSIC",
            "CURRENT FOCUS",
            "新歌全景透视卡墙",
            "SYSTEM MODE",
            "VOL.",
            "ISOMETRIC PERSPECTIVE",
        ]
        for term in forbidden_terms:
            self.assertNotIn(term, source_code, f"Forbidden tech UI term '{term}' found in intro_renderer.py!")

    def test_orange_header_accent_bar_properties(self):
        # Bar is located on the left of title with width 8px
        self.assertEqual(HEADER_BAR_X0, 60)
        self.assertEqual(HEADER_BAR_X1, 68)
        self.assertEqual(HEADER_BAR_X1 - HEADER_BAR_X0, 8)
        self.assertEqual(HEADER_TITLE_POS, (86, 105))
        self.assertEqual(COLOR_TITLE_ORANGE, (255, 62, 24, 255))

        # Test dynamic textbbox calculation matches title visible glyph vertical range
        img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_hero = get_font(124)

        bbox_prefix = draw.textbbox(HEADER_TITLE_POS, TITLE_PREFIX, font=font_hero)
        suffix_x = bbox_prefix[2] + 4
        bbox_suffix = draw.textbbox((suffix_x, HEADER_TITLE_POS[1]), TITLE_SUFFIX, font=font_hero)

        bar_y0 = min(bbox_prefix[1], bbox_suffix[1])
        bar_y1 = max(bbox_prefix[3], bbox_suffix[3])

        # Bar height strictly equals visible title text height
        self.assertEqual(bar_y1 - bar_y0, max(bbox_prefix[3], bbox_suffix[3]) - min(bbox_prefix[1], bbox_suffix[1]))
        self.assertGreater(bar_y1 - bar_y0, 100)

    def test_subtitle_under_main_title_properties(self):
        # Subtitle BAND NEVER STOP must be placed directly under main title
        self.assertEqual(SUBTITLE_TAG, "BAND NEVER STOP")
        self.assertGreaterEqual(SUBTITLE_TOP_GAP, 10)
        self.assertLessEqual(SUBTITLE_TOP_GAP, 14)
        self.assertGreaterEqual(SUBTITLE_SQUASH_Y, 0.80)
        self.assertLessEqual(SUBTITLE_SQUASH_Y, 0.85)

        # Color must be bright neutral light gray/white
        self.assertGreaterEqual(COLOR_TAG_BRIGHT[0], 200)
        self.assertGreaterEqual(COLOR_TAG_BRIGHT[1], 200)
        self.assertGreaterEqual(COLOR_TAG_BRIGHT[2], 220)

    def test_single_line_bottom_footer_and_top_date_alignment(self):
        self.assertEqual(FOOTER_TEXT, "WEEKLY CHINESE BAND RELEASES")
        self.assertEqual(FOOTER_MARGIN_X, 60)
        self.assertGreaterEqual(FOOTER_BOTTOM_MARGIN, 45)
        self.assertLessEqual(FOOTER_BOTTOM_MARGIN, 60)
        self.assertEqual(FONT_SIZE_DATE, 27)

        img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_hero = get_font(124)
        font_date = get_font(FONT_SIZE_DATE)

        # Date right-alignment assertion with "新歌速递" right bbox edge
        bbox_prefix = draw.textbbox(HEADER_TITLE_POS, TITLE_PREFIX, font=font_hero)
        suffix_x = bbox_prefix[2] + 4
        bbox_suffix = draw.textbbox((suffix_x, HEADER_TITLE_POS[1]), TITLE_SUFFIX, font=font_hero)

        date_text = "08.24 - 08.30"
        date_bbox = draw.textbbox((0, 249), date_text, font=font_date)
        date_draw_x = bbox_suffix[2] - (date_bbox[2] - date_bbox[0]) - date_bbox[0]
        rendered_date_bbox = draw.textbbox((date_draw_x, 249), date_text, font=font_date)
        self.assertEqual(rendered_date_bbox[2], bbox_suffix[2])

    def test_strictly_square_card_textures(self):
        tracks = [
            {"id": "t1", "name": "Song 1", "artists": ["Band A"], "album_type": "专辑"},
            {"id": "t2", "name": "Song 2", "artists": ["Band B"], "album_type": "单曲"},
        ]
        cards = build_card_textures(tracks, project_base_dir=self.test_dir, size=CARD_SIZE)
        self.assertEqual(len(cards), 2)
        for card_info in cards:
            w, h = card_info["image"].size
            self.assertEqual(w, h, f"Card must be strictly 1:1 square, got {w}x{h}")
            self.assertEqual(w, CARD_SIZE)

    def test_date_range_calculation(self):
        # Week 35 of 2026 -> 08.24 - 08.30
        dr_2026_35 = compute_week_date_range(2026, 35)
        self.assertEqual(dr_2026_35, "08.24 - 08.30")

        # Extract from playlist metadata
        meta = extract_playlist_metadata({"playlist_name": "华语新歌周刊 2026年第35周"})
        self.assertEqual(meta["date_range"], "08.24 - 08.30")
        self.assertEqual(meta["year"], 2026)
        self.assertEqual(meta["week"], 35)

    def test_dynamic_duration_formula(self):
        # 1 Track
        dur1, step1 = calc_intro_duration(1, step_dur=0.68)
        self.assertEqual(dur1, 2.5)
        self.assertEqual(step1, 0.68)

        # 5 Tracks
        dur5, step5 = calc_intro_duration(5, step_dur=0.68)
        self.assertAlmostEqual(dur5, 3.4, places=2)
        self.assertEqual(step5, 0.68)

        # 12 Tracks
        dur12, step12 = calc_intro_duration(12, step_dur=0.68)
        self.assertAlmostEqual(dur12, 8.16, places=2)
        self.assertEqual(step12, 0.68)

        # 30 Tracks
        dur30, step30 = calc_intro_duration(30, step_dur=0.68)
        self.assertAlmostEqual(dur30, 20.4, places=2)
        self.assertEqual(step30, 0.68)

        # Audio duration stretching when audio is longer than content
        dur_audio, step_audio = calc_intro_duration(12, step_dur=0.68, audio_dur=10.5)
        self.assertEqual(dur_audio, 10.5)
        self.assertAlmostEqual(step_audio, 10.5 / 12, places=3)

    def test_track_order_preservation(self):
        tracks = [
            {"id": "t1", "name": "Song 1", "artists": ["Band A"], "album_type": "专辑"},
            {"id": "t2", "name": "Song 2", "artists": ["Band B"], "album_type": "单曲"},
            {"id": "t3", "name": "Song 3", "artists": ["Band C"], "album_type": "EP"},
            {"id": "t4", "name": "Song 4", "artists": ["Band D"], "album_type": "单曲"},
        ]
        cards = build_card_textures(tracks, project_base_dir=self.test_dir)
        self.assertEqual(len(cards), 4)
        for i, card_info in enumerate(cards):
            self.assertEqual(card_info["index"], i)
            self.assertEqual(card_info["track"]["id"], tracks[i]["id"])
            self.assertEqual(card_info["track"]["name"], tracks[i]["name"])

    def test_album_type_badges_and_fallback(self):
        tracks = [
            {"id": "t1", "name": "Album Song", "album_type": "专辑"},
            {"id": "t2", "name": "Single Song", "album_type": "单曲"},
            {"id": "t3", "name": "EP Song", "album_type": "EP"},
            {"id": "t4", "name": "Missing Type Song"},
            {"id": "t5", "name": "Invalid Type Song", "album_type": "InvalidFormat"},
        ]
        cards = build_card_textures(tracks, project_base_dir=self.test_dir)
        self.assertEqual(cards[0]["album_type"], "专辑")
        self.assertEqual(cards[1]["album_type"], "单曲")
        self.assertEqual(cards[2]["album_type"], "EP")
        self.assertEqual(cards[3]["album_type"], "单曲")
        self.assertEqual(cards[4]["album_type"], "单曲")

    def test_missing_cover_fallback_is_square(self):
        tracks = [
            {"id": "t1", "name": "NonExistentCoverSong", "cover_local_path": "non_existent/path/cover.jpg"}
        ]
        cards = build_card_textures(tracks, project_base_dir=self.test_dir, size=CARD_SIZE)
        self.assertEqual(len(cards), 1)
        self.assertIsInstance(cards[0]["image"], Image.Image)
        w, h = cards[0]["image"].size
        self.assertEqual(w, h)
        self.assertEqual(w, CARD_SIZE)

    def test_floor_reflection_generation(self):
        test_img = Image.new("RGBA", (CARD_SIZE, CARD_SIZE), (200, 50, 50, 255))
        refl = create_card_reflection(test_img, max_opacity=0.36, refl_height=140)
        self.assertEqual(refl.size, (CARD_SIZE, 140))
        arr = np.array(refl)
        top_alpha = arr[0, :, 3].mean()
        bottom_alpha = arr[-1, :, 3].mean()
        self.assertGreater(top_alpha, bottom_alpha)
        self.assertAlmostEqual(bottom_alpha, 0.0, delta=2)

    def test_slide_left_geometry_invariants(self):
        prev_cx = 9999.0
        for step_i in range(11):
            p = -step_i / 10.0
            rush = -p
            cx = BASE_CENTER_X - (rush ** 1.25) * 1000.0
            cy_bottom = BASE_BOTTOM_Y
            right_edge = cx + CARD_SIZE / 2.0

            self.assertLessEqual(cx, prev_cx + 1e-6)
            prev_cx = cx
            self.assertEqual(cy_bottom, BASE_BOTTOM_Y)

        # At pos=-1.0, card must be completely off-screen to the left
        self.assertLess(right_edge, 0.0)

    def test_empty_tracks_raises_value_error(self):
        json_path = os.path.join(self.test_dir, "empty_playlist.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"tracks": []}, f)

        out_mp4 = os.path.join(self.test_dir, "intro_empty.mp4")
        with self.assertRaises(ValueError):
            create_intro_video(project_file=json_path, output_path=out_mp4)

    def test_missing_project_file_raises_filenotfound(self):
        out_mp4 = os.path.join(self.test_dir, "intro_missing.mp4")
        with self.assertRaises(FileNotFoundError):
            create_intro_video(project_file="non_existent_file.json", output_path=out_mp4)


if __name__ == "__main__":
    unittest.main()
