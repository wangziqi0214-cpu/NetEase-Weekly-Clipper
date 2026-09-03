import os
import sys
import json
import math
import time
import re
import datetime
import traceback
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# -------------------------------------------------------------
# MoviePy Compatibility (v1 and v2)
# -------------------------------------------------------------
try:
    try:
        from moviepy.editor import (
            VideoClip, AudioFileClip, ImageClip,
            CompositeVideoClip, ColorClip, CompositeAudioClip, concatenate_audioclips,
            concatenate_videoclips
        )
        MOVIEPY_V2 = False
    except ImportError:
        from moviepy import (
            VideoClip, AudioFileClip, ImageClip,
            CompositeVideoClip, ColorClip, CompositeAudioClip, concatenate_audioclips,
            concatenate_videoclips
        )
        MOVIEPY_V2 = True
except Exception as e:
    print(f"[!] MoviePy import error: {e}", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)

def v_set_dur(clip, t):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(t)
    return clip.set_duration(t)

def v_set_audio(clip, audio):
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio)
    return clip.set_audio(audio)

def v_volumex(clip, vol):
    if hasattr(clip, "multiply_volume"):
        return clip.multiply_volume(vol)
    if hasattr(clip, "volumex"):
        return clip.volumex(vol)
    try:
        from moviepy.audio.fx.multiply_volume import multiply_volume
        return clip.with_effects([multiply_volume(vol)])
    except Exception:
        return clip

def v_subclip(clip, start, end):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)

# -------------------------------------------------------------
# Design Constants & Color Tokens (1:1 Square Poster Visual Style)
# -------------------------------------------------------------
WIDTH = 1080
HEIGHT = 1440
FPS = 24
CARD_SIZE = 720  # 2/3 width strictly 1:1 square ratio (720x720)
BASE_CENTER_X = 540
BASE_BOTTOM_Y = 1140  # Top at 420px, Bottom at 1140px (100px shifted down)
MAX_VISIBLE_DEPTH = 5

# Header Typography & Accent Bar
HEADER_BAR_X0 = 60
HEADER_BAR_X1 = 68                    # Width = 8px
HEADER_TITLE_POS = (86, 105)
TITLE_PREFIX = "华语摇滚"
TITLE_SUFFIX = "新歌速递"

# Subtitle Typography (Positioned directly under main title)
SUBTITLE_TAG = "BAND NEVER STOP"
SUBTITLE_TOP_GAP = 12                 # 10-14px gap below visible title bottom
SUBTITLE_SQUASH_Y = 0.82              # Vertical aspect ratio compression ~0.8-0.85x
FONT_SIZE_SUBTITLE = 30
FONT_SIZE_DATE = 27                   # 34 * 0.8 = 27.2

# Footer Typography (Single line spanning bottom safe zone)
FOOTER_TEXT = "WEEKLY CHINESE BAND RELEASES"
FOOTER_MARGIN_X = 60                  # Left & right margin = 60px
FOOTER_BOTTOM_MARGIN = 52             # 45-60px bottom margin

COLOR_BG_DARK = (11, 17, 32, 255)         # Deep rich navy
COLOR_TITLE_WHITE = (255, 255, 255, 255)
COLOR_TITLE_ORANGE = (255, 62, 24, 255)   # Vivid orange-red
COLOR_DATE_SILVER = (138, 156, 180, 255)  # Muted silver-blue
COLOR_ENG_WHITE = (255, 255, 255, 255)
COLOR_TAG_BRIGHT = (215, 225, 240, 255)   # Bright clearly-visible light gray-white
COLOR_TEXT_DIM = (165, 180, 205, 255)

# Font Locator
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_FONT_PATH = os.path.join(ROOT_DIR, "【MianFei】CangJiGaoDeGuoMiaoHei-CJgaodeguomh-2.ttf")
SYSTEM_EN_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

def get_font(size, font_path=None):
    candidate_paths = [
        font_path,
        DEFAULT_FONT_PATH,
        os.path.join(ROOT_DIR, "assets", "fonts", "SourceHanSansCN-Bold.otf"),
        os.path.join(ROOT_DIR, "assets", "fonts", "SourceHanSansCN-Regular.otf"),
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "Arial.ttf"
    ]
    for p in candidate_paths:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def get_en_font(size):
    if os.path.exists(SYSTEM_EN_FONT):
        try:
            return ImageFont.truetype(SYSTEM_EN_FONT, size)
        except Exception:
            pass
    return get_font(size)

# -------------------------------------------------------------
# Background Canvas (Deep Navy Poster Texture)
# -------------------------------------------------------------
def create_poster_background():
    bg = Image.new("RGBA", (WIDTH, HEIGHT), COLOR_BG_DARK)
    draw = ImageDraw.Draw(bg)

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        if ratio < 0.45:
            r = int(14 * (1 - ratio*2.22) + 9 * (ratio*2.22))
            g = int(22 * (1 - ratio*2.22) + 14 * (ratio*2.22))
            b = int(44 * (1 - ratio*2.22) + 28 * (ratio*2.22))
        else:
            local_r = (ratio - 0.45) * 1.818
            r = int(9 * (1 - local_r) + 6 * local_r)
            g = int(14 * (1 - local_r) + 9 * local_r)
            b = int(28 * (1 - local_r) + 18 * local_r)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0,0,0,0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([540-500, 600-450, 540+500, 600+450], fill=(24, 42, 80, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    bg.alpha_composite(glow)

    vignette = Image.new("RGBA", (WIDTH, HEIGHT), (0,0,0,0))
    vdraw = ImageDraw.Draw(vignette)
    for y in range(260):
        a = int(160 * (1 - y/260))
        vdraw.line([(0, y), (WIDTH, y)], fill=(4, 6, 12, a))
        vdraw.line([(0, HEIGHT - 1 - y), (WIDTH, HEIGHT - 1 - y)], fill=(4, 6, 12, a))
    bg.alpha_composite(vignette)
    return bg

# -------------------------------------------------------------
# Placeholder Cover Generator (1:1 Square)
# -------------------------------------------------------------
def create_placeholder_cover(size=CARD_SIZE, track_name="", artist_name=""):
    img = Image.new("RGBA", (size, size), (18, 26, 44, 255))
    draw = ImageDraw.Draw(img)

    for gx in range(0, size, 25):
        draw.line([(gx, 0), (gx, size)], fill=(28, 40, 68, 80), width=1)
    for gy in range(0, size, 25):
        draw.line([(0, gy), (size, gy)], fill=(28, 40, 68, 80), width=1)

    cx, cy = size // 2, size // 2 - 30
    radii_colors = [
        (130, (10, 14, 26, 255)),
        (100, (22, 32, 54, 255)),
        (70, (14, 20, 34, 255)),
        (45, (255, 62, 24, 255)),
        (14, (10, 14, 26, 255))
    ]
    for r, col in radii_colors:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

    return img

# -------------------------------------------------------------
# Card Texture Builder (Strictly 1:1 Square)
# -------------------------------------------------------------
def build_card_textures(tracks, project_base_dir=".", size=CARD_SIZE):
    card_textures = []
    font_title = get_font(46)
    font_body = get_font(28)
    font_badge = get_font(24)

    for idx, track in enumerate(tracks):
        cpath = track.get("cover_local_path")
        cover_img = None

        if cpath:
            candidate_paths = [
                cpath,
                os.path.join(project_base_dir, cpath),
                os.path.join(ROOT_DIR, cpath)
            ]
            for cp in candidate_paths:
                if cp and os.path.exists(cp):
                    try:
                        cover_img = Image.open(cp).convert("RGBA")
                        break
                    except Exception as e:
                        print(f"[!] Warning: Failed reading cover image at {cp}: {e}", file=sys.stderr)

        card = Image.new("RGBA", (size, size), (18, 26, 44, 255))
        if cover_img:
            # 1:1 square center crop & resize
            cw, ch = cover_img.size
            min_d = min(cw, ch)
            cover_cropped = cover_img.crop(((cw - min_d)//2, (ch - min_d)//2, (cw + min_d)//2, (ch + min_d)//2))
            cover_resized = cover_cropped.resize((size, size), Image.LANCZOS)
            card.paste(cover_resized, (0, 0))
        else:
            t_name = track.get("name", "Unknown Track")
            print(f"[!] Warning: Cover missing for track #{idx+1} ({t_name}), generating placeholder.", file=sys.stderr)
            placeholder = create_placeholder_cover(size, t_name, track.get("artists", [""])[0])
            card.paste(placeholder, (0, 0))

        # Bottom Dark Gradient Overlay inside square card
        grad_h = 240
        grad = Image.new("RGBA", (size, grad_h), (0,0,0,0))
        gdraw = ImageDraw.Draw(grad)
        for gy in range(grad_h):
            ga = int(240 * (gy / grad_h)**1.3)
            gdraw.line([(0, gy), (size, gy)], fill=(6, 10, 20, ga))
        card.alpha_composite(grad, (0, size - grad_h))

        # 1px border
        draw = ImageDraw.Draw(card)
        draw.rectangle([0, 0, size-1, size-1], outline=(255, 255, 255, 50), width=1)

        # Top-right Type Badge (专辑 / 单曲 / EP)
        raw_type = track.get("album_type") or track.get("normalized_release_type")
        atype = str(raw_type).strip() if raw_type else "单曲"
        if atype not in ["专辑", "单曲", "EP"]:
            print(f"[!] Warning: Track #{idx+1} ({track.get('name')}) has unknown album_type '{raw_type}', defaulting to '单曲'.", file=sys.stderr)
            atype = "单曲"

        if atype == "专辑":
            type_color = (255, 69, 58, 255)    # 橙红
            type_border = (255, 69, 58, 220)
        elif atype == "EP":
            type_color = (85, 175, 255, 255)   # 偏蓝
            type_border = (64, 156, 255, 220)
        else:  # 单曲
            type_color = (210, 220, 235, 255)  # 浅灰
            type_border = (170, 185, 205, 200)

        badge_w, badge_h = 88, 44
        tbadge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
        tbdraw = ImageDraw.Draw(tbadge)
        tbdraw.rounded_rectangle([0, 0, badge_w, badge_h], radius=10, fill=(8, 12, 22, 230), outline=type_border, width=2)
        tbdraw.text((badge_w//2, badge_h//2), atype, font=font_badge, fill=type_color, anchor="mm")
        card.alpha_composite(tbadge, (size - badge_w - 18, 18))

        # Song Title and Artist Name
        name_text = track.get("name", "")
        if len(name_text) > 14:
            name_text = name_text[:13] + "…"
        artists_list = track.get("artists", ["未知乐队"])
        artist_text = artists_list[0] if artists_list else ""
        if len(artist_text) > 16:
            artist_text = artist_text[:15] + "…"

        draw.text((28, size - 110), name_text, font=font_title, fill=COLOR_TITLE_WHITE)
        draw.text((28, size - 50), artist_text, font=font_body, fill=COLOR_TEXT_DIM)

        card_textures.append({
            "image": card,
            "track": track,
            "index": idx,
            "album_type": atype
        })
    return card_textures

# -------------------------------------------------------------
# Floor Mirror Reflection Helper
# -------------------------------------------------------------
def create_card_reflection(card_img, max_opacity=0.36, refl_height=140):
    flipped = card_img.transpose(Image.FLIP_TOP_BOTTOM)
    w, h = card_img.size
    crop_h = min(h, refl_height)
    refl_crop = flipped.crop((0, 0, w, crop_h))

    arr = np.array(refl_crop).astype(float)
    for y in range(crop_h):
        decay = (1.0 - y / crop_h) ** 1.3
        arr[y, :, 3] = arr[y, :, 3] * max_opacity * decay
    return Image.fromarray(arr.astype(np.uint8))

# -------------------------------------------------------------
# Date Range & Metadata Helper
# -------------------------------------------------------------
def compute_week_date_range(year, week):
    try:
        mon = datetime.date.fromisocalendar(year, week, 1)
        sun = datetime.date.fromisocalendar(year, week, 7)
        return f"{mon.strftime('%m.%d')} - {sun.strftime('%m.%d')}"
    except Exception:
        return "08.24 - 08.30"

def extract_playlist_metadata(data):
    playlist_title = str(data.get("playlist_name") or "").strip()

    # 1. Parse Year & Week/Issue from playlist_title
    year = None
    week = None

    match_year = re.search(r"(\d{4})", playlist_title)
    if match_year:
        year = int(match_year.group(1))

    match_cn = re.search(r"第\s*(\d+)\s*[周期卷]", playlist_title)
    if match_cn:
        week = int(match_cn.group(1))
    else:
        match_en = re.search(r"(?:Week|VOL\.?|Vol\.?|Issue)\s*(\d+)", playlist_title, re.IGNORECASE)
        if match_en:
            week = int(match_en.group(1))

    # 2. Check explicit fields
    if week is None:
        for field in ["week", "issue", "volume", "vol"]:
            val = data.get(field)
            if val is not None:
                try:
                    week = int(str(val).replace("VOL", "").replace("vol", "").replace("期", "").replace("周", "").strip())
                    break
                except Exception:
                    pass

    # 3. Date determination
    date_val = data.get("date") or data.get("publish_date") or data.get("created_at")
    dt = None
    if date_val:
        for fmt in ["%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"]:
            try:
                dt = datetime.datetime.strptime(str(date_val).strip(), fmt)
                break
            except Exception:
                pass
    if not dt:
        dt = datetime.datetime.today()

    if year is None:
        year = dt.year
    if week is None:
        week = dt.isocalendar()[1]

    date_range_str = compute_week_date_range(year, week)

    return {
        "title_prefix": TITLE_PREFIX,
        "title_suffix": TITLE_SUFFIX,
        "date_range": date_range_str,
        "year": year,
        "week": week
    }

# -------------------------------------------------------------
# Timing & Rhythm Calculations
# -------------------------------------------------------------
def get_rhythmic_progress(t, step_dur=0.68):
    """
    Stepped cubic easing for card transitions:
    First 68% of each step executes smooth cubic transition, followed by a steady hold.
    """
    step = t / step_dur
    base_step = math.floor(step)
    frac = step - base_step
    t_trans = 0.68
    if frac < t_trans:
        p = frac / t_trans
        ease = 3*p*p - 2*p*p*p
    else:
        ease = 1.0
    return base_step + ease

def calc_intro_duration(track_count, step_dur=0.68, audio_dur=None):
    """
    Computes total intro duration deterministically based on track count (1-30).
    Ensures every track becomes the central focus card at least once.
    """
    if track_count <= 0:
        return 5.0, step_dur
    if track_count == 1:
        base_dur = max(2.5, step_dur * 3.0)
        if audio_dur and audio_dur > base_dur:
            return float(audio_dur), float(audio_dur) / 3.0
        return float(base_dur), float(step_dur)

    content_dur = track_count * step_dur
    if audio_dur and audio_dur > content_dur:
        adj_step_dur = float(audio_dur) / track_count
        return float(audio_dur), adj_step_dur
    return float(content_dur), float(step_dur)

# -------------------------------------------------------------
# Main Core Intro Video Generator
# -------------------------------------------------------------
def create_intro_video(
    project_file="project.json",
    output_path="output/intro.mp4",
    voice_path=None,
    bgm_path=None,
    step_dur=0.68
):
    """
    Production-grade Album Flow Intro Generator (1:1 Square Poster Visual Style).
    - Accepts arbitrary authoritative project_file (researched_playlist.json).
    - Preserves ordered_track_ids list strictly.
    - Dynamically supports 1-30 tracks with deterministic duration calculation.
    - Top title: Orange vertical accent bar + '华语摇滚' (White) + '新歌速递' (Orange-Red).
    - Strictly 1:1 square cards for main and all queue cards.
    - Floor mirror reflection fading downwards.
    - Bottom typography with dynamic week date range, 'WEEKLY CHINESE BAND RELEASES' and bright 'BAND NEVER STOP' at (60, 1300).
    """
    print(f"[*] Loading playlist from: {project_file}")

    if not os.path.exists(project_file):
        raise FileNotFoundError(f"Project file not found: {project_file}")

    with open(project_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    tracks = data.get("tracks", [])
    if not tracks:
        raise ValueError(f"No tracks found in playlist file '{project_file}'!")

    total_tracks = len(tracks)
    print(f"[*] Loaded {total_tracks} tracks in authoritative order.")

    # Resolve Output Directory
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Audio Probe & Duration Calculation
    audio_dur = None
    if voice_path and os.path.exists(voice_path):
        try:
            vclip = AudioFileClip(voice_path)
            audio_dur = float(vclip.duration)
            vclip.close()
        except Exception as e:
            print(f"[!] Warning: Failed probing voice audio duration: {e}", file=sys.stderr)

    total_duration, effective_step_dur = calc_intro_duration(total_tracks, step_dur=step_dur, audio_dur=audio_dur)
    print(f"[*] Computed Intro Timing: {total_duration:.2f}s total ({effective_step_dur:.3f}s per card step)")

    # Metadata & Styling
    meta = extract_playlist_metadata(data)
    font_hero = get_font(124)
    font_date = get_font(FONT_SIZE_DATE)
    font_tag = get_en_font(FONT_SIZE_SUBTITLE)

    # Pre-calculate optimal font size for single-line footer to fill width 960px (between left/right 60px)
    target_footer_w = WIDTH - 2 * FOOTER_MARGIN_X
    opt_footer_sz = 53
    dummy_img = Image.new("RGBA", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)
    for sz in range(45, 75):
        f_test = get_en_font(sz)
        tb = dummy_draw.textbbox((0, 0), FOOTER_TEXT, font=f_test)
        if (tb[2] - tb[0]) <= target_footer_w:
            opt_footer_sz = sz
        else:
            break
    font_footer = get_en_font(opt_footer_sz)

    base_bg = create_poster_background()
    project_base = os.path.dirname(os.path.abspath(project_file))
    card_cache = build_card_textures(tracks, project_base_dir=project_base, size=CARD_SIZE)

    def render_frame(t):
        canvas = base_bg.copy()
        draw = ImageDraw.Draw(canvas)

        # 1. Top Header: Orange Vertical Bar + Title Text + Subtitle & Date directly under title
        bbox_prefix = draw.textbbox(HEADER_TITLE_POS, TITLE_PREFIX, font=font_hero)
        suffix_x = bbox_prefix[2] + 4
        bbox_suffix = draw.textbbox((suffix_x, HEADER_TITLE_POS[1]), TITLE_SUFFIX, font=font_hero)

        bar_y0 = min(bbox_prefix[1], bbox_suffix[1])
        bar_y1 = max(bbox_prefix[3], bbox_suffix[3])
        draw.rectangle([HEADER_BAR_X0, bar_y0, HEADER_BAR_X1, bar_y1], fill=COLOR_TITLE_ORANGE)

        draw.text(HEADER_TITLE_POS, TITLE_PREFIX, font=font_hero, fill=COLOR_TITLE_WHITE)
        draw.text((suffix_x, HEADER_TITLE_POS[1]), TITLE_SUFFIX, font=font_hero, fill=COLOR_TITLE_ORANGE)

        # Subtitle: BAND NEVER STOP directly below main title text (aligned to HEADER_TITLE_POS[0])
        rel_tag_bbox = draw.textbbox((0, 0), SUBTITLE_TAG, font=font_tag)
        t_w = rel_tag_bbox[2] - rel_tag_bbox[0]
        t_h = rel_tag_bbox[3] - rel_tag_bbox[1]

        pad = 8
        tag_canvas = Image.new("RGBA", (t_w + pad*2, t_h + pad*2), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tag_canvas)
        tdraw.text((pad - rel_tag_bbox[0], pad - rel_tag_bbox[1]), SUBTITLE_TAG, font=font_tag, fill=COLOR_TAG_BRIGHT)

        squashed_h = int(round((t_h + pad*2) * SUBTITLE_SQUASH_Y))
        tag_squashed = tag_canvas.resize((t_w + pad*2, squashed_h), Image.LANCZOS)

        tag_top_y = bar_y1 + SUBTITLE_TOP_GAP
        tag_left_x = HEADER_TITLE_POS[0] - pad
        canvas.alpha_composite(tag_squashed, (tag_left_x, int(tag_top_y - pad * SUBTITLE_SQUASH_Y)))

        # Dynamic Date: Placed in same horizontal band below "新歌速递", right-aligned to bbox_suffix[2]
        date_text = meta["date_range"]
        date_bbox = draw.textbbox((0, tag_top_y), date_text, font=font_date)
        date_w = date_bbox[2] - date_bbox[0]
        date_draw_x = bbox_suffix[2] - date_w - date_bbox[0]
        draw.text((date_draw_x, tag_top_y), date_text, font=font_date, fill=COLOR_DATE_SILVER)

        # 2. Rhythmic Stepped Queue with 720x720 Square Cards & Floor Reflection
        head_progress = get_rhythmic_progress(t, step_dur=effective_step_dur)

        card_render_list = []
        for i in range(total_tracks):
            pos = (i - head_progress) % total_tracks
            if pos > total_tracks - 1.5:
                pos -= total_tracks
            if pos < -1.0 or pos > 5.5:
                continue

            if pos >= 0:
                scale = 0.88 ** pos
                size = int(CARD_SIZE * scale)
                cx = BASE_CENTER_X + pos * 125.0
                cy_bottom = BASE_BOTTOM_Y - pos * 16.0
                opacity = min(1.0, max(0.0, 1.0 - (pos - 4.5) / 1.0))
            else:
                rush_ratio = -pos / 1.0
                scale = 1.0
                size = CARD_SIZE
                cx = BASE_CENTER_X - (rush_ratio ** 1.25) * 1000.0
                cy_bottom = BASE_BOTTOM_Y
                opacity = max(0.0, 1.0 - max(0.0, (rush_ratio - 0.88) / 0.12))

            card_render_list.append({
                "card_idx": i,
                "pos": pos,
                "cx": cx,
                "cy_bottom": cy_bottom,
                "size": size,
                "scale": scale,
                "opacity": opacity
            })

        # Sort from back to front (largest pos rendered first)
        card_render_list.sort(key=lambda item: item["pos"], reverse=True)

        for item in card_render_list:
            if item["opacity"] <= 0.01:
                continue

            card_orig = card_cache[item["card_idx"]]["image"]
            size = item["size"]
            left = int(item["cx"] - size // 2)
            top = int(item["cy_bottom"] - size)

            card_scaled = card_orig.resize((size, size), Image.LANCZOS)

            # Shadow
            s_img = Image.new("RGBA", (size, size), (0, 0, 0, int(150 * item["scale"] * item["opacity"])))
            canvas.alpha_composite(s_img, (left + 10, top + 10))

            # Floor Mirror Reflection
            refl_h = max(20, int(160 * item["scale"]))
            refl = create_card_reflection(card_scaled, max_opacity=0.32 * item["scale"] * item["opacity"], refl_height=refl_h)
            canvas.alpha_composite(refl, (left, int(item["cy_bottom"] + 2)))

            # Main Card
            if item["opacity"] < 0.99:
                c_arr = np.array(card_scaled)
                c_arr[:, :, 3] = (c_arr[:, :, 3].astype(float) * item["opacity"]).astype(np.uint8)
                card_scaled = Image.fromarray(c_arr)

            canvas.alpha_composite(card_scaled, (left, top))

        # 3. Bottom Typography: Single Line 'WEEKLY CHINESE BAND RELEASES'
        tb_final = draw.textbbox((0, 0), FOOTER_TEXT, font=font_footer)
        w_final = tb_final[2] - tb_final[0]
        footer_draw_x = FOOTER_MARGIN_X + (target_footer_w - w_final) // 2 - tb_final[0]
        target_bottom_y = HEIGHT - FOOTER_BOTTOM_MARGIN
        footer_draw_y = target_bottom_y - tb_final[3]
        draw.text((footer_draw_x, footer_draw_y), FOOTER_TEXT, font=font_footer, fill=COLOR_ENG_WHITE)

        return np.array(canvas)[:, :, :3]

    print(f"[*] Rendering Intro Video to: {output_path} ({WIDTH}x{HEIGHT} @ {FPS}fps, {total_duration:.2f}s)...")
    t0 = time.time()

    video_clip = VideoClip(render_frame, duration=total_duration)

    # Audio Compositing
    audio_clips = []
    if bgm_path and os.path.exists(bgm_path):
        try:
            bgm = AudioFileClip(bgm_path)
            if bgm.duration < total_duration:
                segments = []
                rem = total_duration
                while rem > 0:
                    piece = min(bgm.duration, rem)
                    segments.append(v_subclip(bgm, 0, piece))
                    rem -= piece
                bgm = concatenate_audioclips(segments)
            else:
                bgm = v_subclip(bgm, 0, total_duration)
            bgm = v_volumex(bgm, 0.3)
            audio_clips.append(bgm)
        except Exception as e:
            print(f"[!] Warning: Failed loading BGM {bgm_path}: {e}", file=sys.stderr)

    if voice_path and os.path.exists(voice_path):
        try:
            voice = AudioFileClip(voice_path)
            voice = v_volumex(voice, 0.95)
            audio_clips.append(voice)
        except Exception as e:
            print(f"[!] Warning: Failed loading voice {voice_path}: {e}", file=sys.stderr)

    if audio_clips:
        composite_audio = CompositeAudioClip(audio_clips)
        composite_audio = v_set_dur(composite_audio, total_duration)
        video_clip = v_set_audio(video_clip, composite_audio)

    video_clip.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac" if audio_clips else None,
        preset="medium",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )

    t1 = time.time()
    print(f"[✓] Intro video successfully created in {t1-t0:.2f}s -> {output_path}")
    return output_path

# -------------------------------------------------------------
# CLI Entry Point
# -------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NetEase Weekly Clipper 1:1 Square Poster Intro Generator")
    parser.add_argument("project_file", nargs="?", default="project.json", help="Path to researched_playlist.json or project.json")
    parser.add_argument("--output-path", dest="output_path", default="output/intro.mp4", help="Output intro MP4 path")
    parser.add_argument("--voice-path", dest="voice_path", default=None, help="Optional voiceover audio path")
    parser.add_argument("--bgm-path", dest="bgm_path", default=None, help="Optional BGM audio path")
    parser.add_argument("--step-dur", dest="step_dur", type=float, default=0.68, help="Duration per track in seconds")

    args = parser.parse_args()

    try:
        create_intro_video(
            project_file=args.project_file,
            output_path=args.output_path,
            voice_path=args.voice_path,
            bgm_path=args.bgm_path,
            step_dur=args.step_dur
        )
    except Exception as exc:
        print(f"[!] Fatal intro rendering error: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
