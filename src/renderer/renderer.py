import sys
print(">>> [System] renderer.py loaded - PIL-Probe v4.1")
import os
import json
import re
import time
import hashlib
import requests
from io import BytesIO
from PIL import Image, ImageFilter, ImageDraw
import numpy as np
try:
    from moviepy.editor import (
        VideoClip, AudioFileClip, ImageClip, TextClip,
        CompositeVideoClip, CompositeAudioClip, ColorClip, concatenate_videoclips,
        VideoFileClip
    )
    try:
        from moviepy.video.fx.all import crossfadein
    except ImportError:
        try:
            from moviepy.video.fx.crossfadein import crossfadein
        except ImportError:
            # Fallback to fadein
            from moviepy.video.fx.all import fadein as crossfadein
    try:
        from moviepy.audio.fx.all import audio_fadeout, audio_fadein
    except ImportError:
        # Fallback: try direct import or define dummy
        try:
            from moviepy.audio.fx.audio_fadein import audio_fadein
            from moviepy.audio.fx.audio_fadeout import audio_fadeout
        except ImportError:
            print("!!! Warning: audio_fadein/out not found. Using dummy.")
            audio_fadein = lambda clip, d: clip
            audio_fadeout = lambda clip, d: clip

    MOVIEPY_V2 = False
except ImportError:
    # MoviePy v2 compatibility
    from moviepy import (
        VideoClip, AudioFileClip, ImageClip, TextClip,
        CompositeVideoClip, CompositeAudioClip, ColorClip, concatenate_videoclips,
        VideoFileClip
    )
    from moviepy.video.fx import CrossFadeIn
    from moviepy.audio.fx import AudioFadeOut, AudioFadeIn
    MOVIEPY_V2 = True
    crossfadein = CrossFadeIn
    audio_fadeout = AudioFadeOut
    audio_fadein = AudioFadeIn

def v_set_pos(clip, pos):
    if hasattr(clip, "with_position"): return clip.with_position(pos)
    return clip.set_position(pos)

def v_set_start(clip, t):
    if hasattr(clip, "with_start"): return clip.with_start(t)
    return clip.set_start(t)

def v_set_dur(clip, t):
    if hasattr(clip, "with_duration"): return clip.with_duration(t)
    return clip.set_duration(t)

def v_set_audio(clip, audio):
    if hasattr(clip, "with_audio"): return clip.with_audio(audio)
    return clip.set_audio(audio)

def v_set_opacity(clip, opacity):
    if hasattr(clip, "with_opacity"): return clip.with_opacity(opacity)
    return clip.set_opacity(opacity)

def v_volumex(clip, volume):
    factor = float(volume)

    def apply_gain(get_frame, t):
        return get_frame(t) * factor

    if hasattr(clip, "transform"):
        return clip.transform(apply_gain, keep_duration=True)
    if hasattr(clip, "fl"):
        return clip.fl(apply_gain, keep_duration=True)
    if hasattr(clip, "multiply_volume"):
        return clip.multiply_volume(factor)
    if hasattr(clip, "volumex"):
        return clip.volumex(factor)
    try:
        from moviepy.audio.fx.MultiplyVolume import MultiplyVolume
        return clip.with_effects([MultiplyVolume(factor)])
    except Exception:
        return clip

def v_subclip(clip, start, end):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)

def v_audio_volume_envelope(clip, envelope_func):
    def apply_gain(get_frame, t):
        frame = get_frame(t)
        gain = envelope_func(t)
        if isinstance(gain, np.ndarray) and isinstance(frame, np.ndarray) and frame.ndim > 1:
            return frame * gain.reshape((-1, 1))
        return frame * gain

    if hasattr(clip, "transform"):
        return clip.transform(apply_gain, keep_duration=True)
    if hasattr(clip, "fl"):
        return clip.fl(lambda gf, t: apply_gain(gf, t))
    return clip

# Font path - Try to find a system font or fallback
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def _first_existing_font(*paths):
    for path in paths:
        if path and os.path.exists(path):
            return path
    return paths[-1] if paths else ""

PROJECT_FONT = os.path.join(ROOT_DIR, "【MianFei】CangJiGaoDeGuoMiaoHei-CJgaodeguomh-2.ttf")
FONT_BOLD = _first_existing_font(
    os.getenv("NETEASE_WEEKLY_FONT_BOLD"),
    PROJECT_FONT,
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
)
FONT_REGULAR = _first_existing_font(
    os.getenv("NETEASE_WEEKLY_FONT_REGULAR"),
    PROJECT_FONT,
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
)
FONT_SERIF = _first_existing_font(
    os.getenv("NETEASE_WEEKLY_FONT_SERIF"),
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    PROJECT_FONT,
    "C:/Windows/Fonts/simsun.ttc",
)
IMAGE_CACHE_DIR = os.path.join(ROOT_DIR, "assets", "covers", "_render_cache")
IMAGE_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Referer": "https://music.163.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

def log_error(msg):
    try:
        log_path = os.path.join(ROOT_DIR, "output", "render_error.log")
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
        print(f"!!! [LOG] {msg}")
    except: pass

def get_safe_font(font_path, fallback=FONT_BOLD):
    if font_path and os.path.exists(font_path):
        try:
            from PIL import ImageFont
            ImageFont.truetype(font_path, 10)
            return font_path
        except:
            pass
    return fallback

def create_text_clip_pil(text, font_size, font_path, color, width, duration, align='center', debug_name=None):
    from PIL import Image, ImageFont, ImageDraw
    import numpy as np

    if not font_path or not os.path.exists(font_path):
        font_path = FONT_BOLD
    try:
        font = ImageFont.truetype(font_path, font_size)
    except:
        font = ImageFont.truetype(FONT_BOLD, font_size)

    lines = []
    if text:
        def get_text_width(text, font):
            if hasattr(font, 'getlength'):
                return font.getlength(text)
            return font.getsize(text)[0]

        def wrap_tokens(paragraph):
            return re.findall(r"[（(]?[A-Za-z0-9]+(?:[’'][A-Za-z0-9]+)*(?:[).,!?:;%）]+)?|[ \t]+|.", paragraph)

        for paragraph in text.split('\n'):
            line = ""
            for token in wrap_tokens(paragraph):
                if token.isspace() and not line:
                    continue

                candidate = line + token
                if get_text_width(candidate.rstrip(), font) <= width or not line:
                    line = candidate
                else:
                    lines.append(line.rstrip())
                    line = token.lstrip()
            if line.strip():
                lines.append(line.rstrip())
    else:
        lines = [" "]

    line_spacing = int(font_size * (0.24 if len(lines) > 1 else 0.35))

    joined_text = "\n".join(lines)

    # Render entire block to catch all pixels
    # Buffer: 4x font height per line to be extremely safe
    buf_w = width + 200
    expected_h = (font_size + line_spacing) * (len(lines) + 2)
    buf_h = int(expected_h * 2)

    temp_buf = Image.new("RGBA", (buf_w, buf_h), (0,0,0,0))
    temp_draw = ImageDraw.Draw(temp_buf)

    # Draw at a safe offset
    temp_draw.multiline_text((100, font_size), joined_text, font=font, fill=color, align=align, spacing=line_spacing)

    # Find true bbox of all pixels
    ink_bbox = temp_buf.getbbox()
    if not ink_bbox:
        # Fallback for empty text
        clip = ColorClip(size=(width, font_size), color=(0,0,0,0), is_mask=False)
        return clip.with_duration(duration) if MOVIEPY_V2 else clip.set_duration(duration)

    # Expand bbox for safety
    safe_bbox = (
        max(0, ink_bbox[0] - 20),
        max(0, ink_bbox[1] - 20),
        min(buf_w, ink_bbox[2] + 20),
        min(buf_h, ink_bbox[3] + 100) # Extreme bottom margin for artistic descenders
    )

    final_ink = temp_buf.crop(safe_bbox)
    final_arr = np.array(final_ink)

    # Emergency safety for 0-dimension arrays
    h_arr, w_arr = final_arr.shape[:2]
    if h_arr == 0 or w_arr == 0:
        final_arr = np.zeros((max(1, h_arr), max(1, w_arr), 4), dtype=np.uint8)

    clip = ImageClip(final_arr)
    clip = clip.with_duration(duration) if MOVIEPY_V2 else clip.set_duration(duration)
    clip.render_line_count = len(lines)
    clip.render_line_spacing = line_spacing
    clip.render_visual_height = int(font_size * 1.15 * len(lines) + line_spacing * max(0, len(lines) - 1))
    print(f"!!! [DEBUG] Created {debug_name or 'text'} clip: {final_arr.shape}")
    return clip

def _open_image_file(path):
    with Image.open(path) as img:
        return img.convert("RGBA")


def _render_cache_path_for_url(url):
    signature = hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]
    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    return os.path.join(IMAGE_CACHE_DIR, f"{signature}.img")


def download_image(url, local_path=None):
    placeholder = Image.new("RGBA", (100, 100), (50, 50, 50, 255))

    for candidate in [local_path]:
        if candidate and os.path.exists(candidate):
            try:
                return _open_image_file(candidate)
            except Exception as e:
                log_error(f"Failed to open local cover image {candidate}: {e}")

    if not url:
        return placeholder

    cache_path = _render_cache_path_for_url(url)
    if os.path.exists(cache_path):
        try:
            return _open_image_file(cache_path)
        except Exception as e:
            log_error(f"Failed to open cached cover image {cache_path}: {e}")

    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(url, headers=IMAGE_REQUEST_HEADERS, timeout=20)
            response.raise_for_status()
            content = response.content
            if content[:16].lower().startswith(b"<html"):
                raise ValueError("image response returned HTML")
            img = Image.open(BytesIO(content)).convert("RGBA")
            try:
                with open(cache_path, "wb") as f:
                    f.write(content)
            except Exception as e:
                log_error(f"Failed to write cover cache {cache_path}: {e}")
            return img
        except Exception as e:
            last_error = e
            time.sleep(1.0 + attempt * 0.5)

    log_error(f"Failed to download cover image after retries: {url} ({last_error})")
    return placeholder

def create_background(img_pil, w, h):
    # Resize to fill
    iw, ih = img_pil.size
    scale = max(w / iw, h / ih)
    new_size = (int(iw * scale), int(ih * scale))
    img_resized = img_pil.resize(new_size, Image.LANCZOS)

    # Crop center
    left = (img_resized.width - w) // 2
    top = (img_resized.height - h) // 2
    img_cropped = img_resized.crop((left, top, left + w, top + h))

    # Blur
    img_blurred = img_cropped.filter(ImageFilter.GaussianBlur(radius=40))

    # Dark overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * 0.4)))
    img_blurred = img_blurred.convert("RGBA")
    img_blurred.alpha_composite(overlay)

    # Return as RGB to ensure it's fully opaque for concatenation
    return np.array(img_blurred.convert("RGB"))

def create_album_art(img_pil, size=600):
    # Resize
    img_resized = img_pil.resize((size, size), Image.LANCZOS)

    # Add shadow (simple simulation by creating a larger image)
    shadow_margin = 40
    total_size = size + shadow_margin * 2
    shadow_img = Image.new("RGBA", (total_size, total_size), (0, 0, 0, 0))

    # Draw plain black rect
    draw = ImageDraw.Draw(shadow_img)
    # Offset shadow slightly down
    draw.rectangle(
        [shadow_margin, shadow_margin + 10, shadow_margin + size, shadow_margin + size + 10],
        fill=(0, 0, 0, 100)
    )
    # Blur shadow
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=15))

    # Paste artwork on top
    shadow_img.paste(img_resized, (shadow_margin, shadow_margin), mask=img_resized.split()[3] if len(img_resized.split()) > 3 else None)

    return np.array(shadow_img)

def create_circle_dot(radius=8, color=(255, 255, 255, 255)):
    size = radius * 2
    img = Image.new("RGBA", (size, size), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size, size), fill=color)
    return np.array(img)

def fallback_download_audio(track_id, local_path):
    print(f"   [*] Attempting emergency fallback download for ID {track_id}...")
    import requests
    os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

    # Attempt 1: Try pyncm internal API (can fetch VIP trial clips)
    try:
        from pyncm import apis
        import pyncm
        cookie_path = 'cookie.txt'
        if os.path.exists(cookie_path):
            from http.cookiejar import MozillaCookieJar
            cj = MozillaCookieJar(cookie_path)
            cj.load(ignore_discard=True, ignore_expires=True)
            pyncm.GetCurrentSession().cookies = cj

        audio_info = apis.track.GetTrackAudio([int(track_id)])
        if audio_info.get('data') and audio_info['data'][0].get('url'):
            url = audio_info['data'][0]['url']
            print(f"   [*] Internal API fetched URL: {url[:40]}...")
            r = requests.get(url, stream=True, timeout=15)
            if r.status_code in [200, 206]:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
                    print("   [+] Internal API download successful!")
                    return True
    except Exception as e:
        print(f"   [!] Internal API attempt failed: {e}")

    # Attempt 2: Fallback to outer URL
    print(f"   [*] Falling back to outer URL...")
    url = f"http://music.163.com/song/media/outer/url?id={track_id}.mp3"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://music.163.com/'
    }
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=15)
        if r.status_code in [200, 206]:
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.exists(local_path):
                with open(local_path, 'rb') as f:
                    content = f.read(200).lower()
                    if b'<!doctype' in content or b'<html' in content:
                        print("   [!] Fallback download failed: Received HTML error page instead of audio.")
                        f.close()
                        try: os.remove(local_path)
                        except: pass
                        return False
                if os.path.getsize(local_path) > 1024:
                    print("   [+] Fallback download successful!")
                    return True
                else:
                    print("   [!] Fallback download failed: File too small.")
                    try: os.remove(local_path)
                    except: pass
    except Exception as e:
        print(f"   [!] Fallback download failed: {e}")
    return False

def render_clip(track, output_folder, layout=None, render_options=None):
    w, h = 1080, 1440
    merged_render_options = dict(track.get("render_options") or {})
    merged_render_options.update(render_options or {})
    render_options = merged_render_options

    # Defaults if layout not provided
    if not layout:
        layout = {
            "album_art": { "x": "center", "y": 200, "size": 0.7 },
            "title": { "x": "center", "y": 0.55, "font_size": 60, "font": FONT_BOLD },
            "artist": { "x": "center", "y": 0.60, "font_size": 40, "font": FONT_REGULAR },
            "description": { "x": 100, "y": 0.75, "font_size": 35, "font": FONT_SERIF },
            "progress_bar": { "x": "center", "y": 0.68 }
        }

    print(f"Rendering {track['name']}...")

    # 1. Download Cover
    cover_pil = download_image(track.get('cover_url'), track.get('cover_local_path'))

    clip_duration = float(render_options.get("clip_duration", track.get("clip_duration_sec", 15.0)))
    radio_window_sec = float(render_options.get("radio_window_sec", track.get("song_fadein_sec", 0.0)))
    radio_intro_path = render_options.get("radio_intro_path", track.get("tts_audio_path"))
    radio_intro_volume = float(render_options.get("radio_intro_volume", track.get("radio_intro_volume", 2.2425)))
    song_fadein_duration = float(render_options.get("song_fadein_duration", track.get("song_fadein_sec", 0.0)))
    radio_voice_max_duration = radio_window_sec if radio_window_sec > 0 else None

    def resolve_segment_window(selected_track, desired_duration_sec, options):
        source = str(options.get("segment_source", "preferred") or "preferred").strip().lower()
        duration_ms = int(float(desired_duration_sec) * 1000)
        radio_window_sec_effective = float(
            options.get(
                "radio_window_sec",
                selected_track.get(
                    "song_fadein_sec",
                    max(0.0, float(desired_duration_sec) - float(selected_track.get("chorus_clip_duration_sec") or 15.0)),
                ),
            ) or 0.0
        )
        radio_window_ms = int(radio_window_sec_effective * 1000)

        def build_window(start_key, end_key, align_music_start=False):
            start_ms_raw = selected_track.get(start_key)
            if start_ms_raw is None:
                return None
            start_ms = int(start_ms_raw)
            if align_music_start and radio_window_ms > 0:
                start_ms = max(0, start_ms - radio_window_ms)
            end_ms = start_ms + duration_ms
            return start_ms, end_ms

        prefer_chorus = source in {"preferred", "auto", "chorus_first", "chorus-preferred"}
        force_chorus = source in {"chorus", "chorus_candidate", "pychorus"}

        if prefer_chorus or force_chorus:
            chorus_window = build_window(
                "chorus_candidate_start_ms",
                "chorus_candidate_end_ms",
                align_music_start=True,
            )
            if chorus_window is not None:
                return chorus_window, "chorus_candidate"
            if force_chorus:
                return build_window("highlight_start_ms", "highlight_end_ms") or (0, duration_ms), "highlight_fallback"

        return build_window("highlight_start_ms", "highlight_end_ms") or (0, duration_ms), "highlight"

    # Audio duration/clip info
    (segment_start_ms, segment_end_ms), segment_source_used = resolve_segment_window(track, clip_duration, render_options)
    start_sec = segment_start_ms / 1000.0
    end_sec = max(start_sec, segment_end_ms / 1000.0)
    print(f"   -> Segment source: {segment_source_used} @ {start_sec:.3f}s")

    # 2. Audio (moved up to ensure bounds check before clipping)
    song_audio_clip = None
    local_path = track.get('local_path', '')
    track_id = track.get('id', '')

    if not local_path and track_id:
        local_path = os.path.abspath(f"assets/audio/{track_id}.mp3")

    def load_audio_file():
        nonlocal start_sec, end_sec
        try:
            if not os.path.exists(local_path) or os.path.getsize(local_path) < 1024:
                return None

            # Pre-flight check to prevent FFmpeg crashes on HTML error pages
            with open(local_path, 'rb') as f:
                header = f.read(200).lower()
                if b'<!doctype' in header or b'<html' in header:
                    return None

            full_audio = AudioFileClip(local_path)
            # Safe Clamp the bounds for the audio track
            if end_sec > full_audio.duration:
                end_sec = full_audio.duration
                if start_sec >= end_sec:
                    start_sec = max(0.0, end_sec - clip_duration)

            return v_subclip(full_audio, start_sec, end_sec)
        except Exception as e:
            print(f"Error loading audio {local_path}: {e}")
            return None

    song_audio_clip = load_audio_file()

    # Retry fallback download if audio clip is invalid
    if not song_audio_clip and track_id:
        print(f"   [!] Audio invalid/missing. Retrying download...")
        if os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass
        if fallback_download_audio(track_id, local_path):
            song_audio_clip = load_audio_file()

    if song_audio_clip:
        print(f"   -> Audio loaded: {song_audio_clip.duration}s from {local_path}")
    else:
        print(f"   -> [!] NO AUDIO for {track['name']} (path: {local_path})")
        return None

    # Final adjusted duration
    duration = end_sec - start_sec
    final_audio = song_audio_clip
    voice_clip = None
    voice_duration = 0.0
    if radio_intro_path and os.path.exists(radio_intro_path):
        try:
            voice_clip = AudioFileClip(radio_intro_path)
            if radio_voice_max_duration:
                voice_clip = v_subclip(voice_clip, 0, min(voice_clip.duration, radio_voice_max_duration))
            voice_duration = float(voice_clip.duration or 0.0)
            voice_clip = v_set_start(v_volumex(voice_clip, radio_intro_volume), 0)
        except Exception as e:
            voice_clip = None
            print(f"   [!] Failed to load radio intro audio {radio_intro_path}: {e}")

    if voice_clip is not None and voice_duration > 0:
        duck_volume = float(render_options.get("radio_song_duck_volume", track.get("radio_song_duck_volume", 0.1088)))
        start_volume = float(render_options.get("radio_song_start_volume", track.get("radio_song_start_volume", 0.0544)))
        guard_sec = float(render_options.get("radio_voice_guard_sec", track.get("radio_voice_guard_sec", 0.35)))
        release_sec = float(render_options.get("radio_song_release_sec", track.get("radio_song_release_sec", 1.8)))
        duck_until = min(duration, voice_duration + guard_sec)
        full_volume_at = min(duration, max(song_fadein_duration, duck_until + release_sec))

        def radio_duck_envelope(t):
            ts = np.asarray(t, dtype=float)
            gain = np.ones_like(ts, dtype=float)
            if duck_until > 0:
                intro_ramp = min(0.7, duck_until)
                early = ts < intro_ramp
                gain = np.where(early, start_volume + (duck_volume - start_volume) * (ts / max(intro_ramp, 0.001)), gain)
                gain = np.where((ts >= intro_ramp) & (ts < duck_until), duck_volume, gain)
            if full_volume_at > duck_until:
                release = (ts - duck_until) / max(full_volume_at - duck_until, 0.001)
                release = np.clip(release, 0.0, 1.0)
                eased = release * release * (3 - 2 * release)
                gain = np.where((ts >= duck_until) & (ts < full_volume_at), duck_volume + (1.0 - duck_volume) * eased, gain)
            gain = np.where(ts >= full_volume_at, 1.0, gain)
            if np.isscalar(t):
                return float(gain)
            return gain

        song_audio_clip = v_audio_volume_envelope(song_audio_clip, radio_duck_envelope)
        final_audio = CompositeAudioClip([song_audio_clip, voice_clip])
    elif song_fadein_duration > 0:
        if MOVIEPY_V2:
            song_audio_clip = song_audio_clip.with_effects([AudioFadeIn(duration=min(song_fadein_duration, duration))])
        else:
            song_audio_clip = song_audio_clip.fx(audio_fadein, min(song_fadein_duration, duration))
        final_audio = song_audio_clip

    # 3. Prepare Background
    bg_arr = create_background(cover_pil, w, h)
    bg_clip = v_set_dur(ImageClip(bg_arr), duration)

    # 4. Album Art
    art_cfg = layout.get("album_art", {})
    album_clip = None
    if art_cfg.get("visible", True):
        album_size = int(w * art_cfg.get("size", 0.7))
        album_arr = create_album_art(cover_pil, size=album_size)
        album_clip = v_set_dur(ImageClip(album_arr), duration)
        album_clip = v_set_pos(album_clip, (art_cfg.get("x", "center"), art_cfg.get("y", 200)))

    # 5. Text Info
    def resolve_layout_y(cfg, key, fallback):
        value = cfg.get(key, fallback)
        if isinstance(value, (int, float)) and value <= 1:
            return value * h
        return value

    # Title
    t_cfg = layout.get("title", {})
    txt_title = None
    title_bottom = None
    if t_cfg.get("visible", True):
        title_clip = create_text_clip_pil(
            track['name'],
            t_cfg.get("font_size", 60),
            get_safe_font(t_cfg.get("font", FONT_BOLD), FONT_BOLD),
            'white', 800, duration, 'center', debug_name="title"
        )
        title_y = resolve_layout_y(t_cfg, "y", h * 0.55)
        txt_title = v_set_pos(title_clip, (t_cfg.get("x", "center"), title_y))
        title_bottom = title_y + getattr(title_clip, "render_visual_height", title_clip.size[1])

    # Artist
    ar_cfg = layout.get("artist", {})
    txt_artist = None
    txt_type = None
    text_stack_bottom = title_bottom or 0
    if ar_cfg.get("visible", True):
        artist_str = f"{track['artists'][0]} - {track['album']}"
        artist_clip = create_text_clip_pil(
            artist_str,
            ar_cfg.get("font_size", 40),
            get_safe_font(ar_cfg.get("font", FONT_REGULAR), FONT_REGULAR),
            '#cccccc', 900, duration, 'center', debug_name="artist"
        )
        artist_y = resolve_layout_y(ar_cfg, "y", h * 0.60)
        if title_bottom is not None:
            artist_y = max(artist_y, title_bottom + int(t_cfg.get("stack_gap_after", 12)))
        txt_artist = v_set_pos(artist_clip, (ar_cfg.get("x", "center"), artist_y))
        artist_bottom = artist_y + getattr(artist_clip, "render_visual_height", artist_clip.size[1])

        # Album Type
        type_str = f"[{track.get('album_type', '单曲')}]"
        type_y = max(artist_y + 50, artist_bottom + int(ar_cfg.get("stack_gap_after", 8)))

        type_clip = create_text_clip_pil(
            type_str,
            30,
            get_safe_font(FONT_REGULAR, FONT_REGULAR),
            '#aaaaaa', 900, duration, 'center', debug_name="album_type"
        )
        txt_type = v_set_pos(type_clip, (ar_cfg.get("x", "center"), type_y))
        text_stack_bottom = type_y + getattr(type_clip, "render_visual_height", type_clip.size[1])

    # 6. Description
    d_cfg = layout.get("description", {})
    txt_desc = None
    if d_cfg.get("visible", True):
        desc_text = track.get('description', '')
        txt_desc = v_set_pos(create_text_clip_pil(
            desc_text,
            d_cfg.get("font_size", 35),
            get_safe_font(d_cfg.get("font", FONT_SERIF), FONT_SERIF),
            'white', 800, duration, 'left', debug_name="desc"
        ), (d_cfg.get("x", 100), (d_cfg.get("y", h * 0.75) if isinstance(d_cfg.get("y"), (int, float)) and d_cfg.get("y") > 1 else h * d_cfg.get("y", 0.75))))

    # 7. Progress Bar
    p_cfg = layout.get("progress_bar", {})
    color_clip = None
    dot_clip = None
    if p_cfg.get("visible", True):
        line_w = w - 200
        progress_y = resolve_layout_y(p_cfg, "y", h * 0.68)
        progress_y = max(progress_y, text_stack_bottom + int(p_cfg.get("stack_gap_before", 34)))
        color_clip = v_set_pos(v_set_dur(ColorClip(size=(line_w, 4), color=(255, 255, 255)), duration), (p_cfg.get("x", "center"), progress_y))

        # Animated Dot
        dot_radius = 6
        dot_arr = create_circle_dot(dot_radius)
        dot_y = progress_y + 2 - dot_radius
        start_x_cfg = p_cfg.get("x", "center")
        start_x = (w - line_w) // 2 if start_x_cfg == "center" else start_x_cfg
        def dot_pos(t):
            if duration <= 0: return (start_x, dot_y)
            progress = t / duration
            return (start_x + line_w * progress - dot_radius, dot_y)
        dot_clip = v_set_pos(v_set_dur(ImageClip(dot_arr), duration), dot_pos)

    # 8. Lyrics Overlay
    lyrics_clips = []
    if 'lyrics' in track and track['lyrics']:
        ly_cfg = layout.get("lyrics", {})
        if not ly_cfg.get("visible", True):
            print("   -> Lyrics hidden (visible=False)")
        else:
            start_ms = segment_start_ms
            end_ms = segment_end_ms
            if end_ms < start_ms: end_ms = start_ms + 30000

            duration_ly = (end_ms - start_ms) / 1000.0

            art_y = art_cfg.get("y", 200)
            overlay_h = 80

            # Resolve Y position
            lyric_y_cfg = ly_cfg.get("y", "album_bottom")
            if lyric_y_cfg == "album_bottom":
                 overlay_top = art_y + album_size - overlay_h
            else:
                 overlay_top = lyric_y_cfg
                 if isinstance(overlay_top, float) and overlay_top <= 1.0: overlay_top *= h

            lyric_y = overlay_top + 10
            ly_font_size = ly_cfg.get("font_size", 28)
            ly_opacity = ly_cfg.get("opacity", 0.4)

            # Filter lines that overlap with [start_ms, end_ms]
            valid_indices = []
            lyric_list = track['lyrics']
            for i, l in enumerate(lyric_list):
                 t = l['time']
                 next_t = lyric_list[i+1]['time'] if i < len(lyric_list)-1 else t + 5000
                 if t < end_ms and next_t > start_ms:
                     valid_indices.append(i)

            for i in valid_indices:
                line = lyric_list[i]
                l_text = line['text']
                if not l_text: continue

                # Determine end time of this line
                if i < len(lyric_list) - 1:
                    line_end_ms = lyric_list[i+1]['time']
                else:
                    line_end_ms = line['time'] + 5000

                # Clip-relative start and end (in seconds)
                rel_start = (line['time'] - start_ms) / 1000.0
                rel_end = (line_end_ms - start_ms) / 1000.0

                display_start = max(0, rel_start)
                display_end = min(duration, rel_end)
                dur = display_end - display_start

                if dur <= 0.1: continue

                txt = create_text_clip_pil(
                    l_text,
                    ly_font_size,
                    get_safe_font(FONT_BOLD, FONT_BOLD),
                    'white',
                    album_size - 40,
                    dur,
                    align='center',
                    debug_name=f"lyric_{i}"
                )
                txt = v_set_pos(v_set_start(txt, display_start), ('center', lyric_y))
                lyrics_clips.append(txt)

            # Apply Semi-transparent darkened background if lyrics exist and change
            visible_text_content = [track['lyrics'][i]['text'].strip() for i in valid_indices if track['lyrics'][i]['text'].strip()]
            if len(set(visible_text_content)) > 1:
                overlay_h = 80
                lyric_bg = ColorClip(size=(album_size, overlay_h), color=(0,0,0))
                lyric_bg = v_set_dur(lyric_bg, duration)
                lyric_bg = v_set_opacity(lyric_bg, ly_opacity)
                lyric_bg = v_set_pos(lyric_bg, ('center', overlay_top))

                # Insert at the beginning of lyrics_clips so it's behind text
                lyrics_clips.insert(0, lyric_bg)
            else:
                # If no lyrics or text never changes, don't show any lyrics or overlay
                lyrics_clips = []

    # Composite
    all_clips = [bg_clip, album_clip, txt_title, txt_artist, txt_type, color_clip, txt_desc, dot_clip] + lyrics_clips

    # Filter out None and clips that are entirely off-screen or have 0 dimension to prevent MoviePy crashes
    valid_clips = []
    for c in all_clips:
        if c is None: continue
        try:
            # Check if clip is within bounds (at least partially)
            # 1440 is height, 1080 is width.
            # If pos is a fixed tuple (x, y), we can check if it's way out.
            pos = c.pos(0) if callable(c.pos) else c.pos
            if isinstance(pos, (list, tuple)):
                px, py = pos
                # Crude check: if top is below 1440 or bottom is above 0, etc.
                # However, TextClips can have 'center' pos which returns 0 usually.
                # The real fix is to ensure the clip has actual size and is not sliced to 0.
                if c.size[0] <= 0 or c.size[1] <= 0: continue
                # Clamping/Bound checking
                if isinstance(py, (int, float)) and py >= h: continue
                if isinstance(py, (int, float)) and py + c.size[1] <= 0: continue
                if isinstance(px, (int, float)) and px >= w: continue
                if isinstance(px, (int, float)) and px + c.size[0] <= 0: continue

            valid_clips.append(c)
        except Exception as e:
            # Fallback for dynamic positions or other errors
            valid_clips.append(c)

    final_clip = CompositeVideoClip(
        valid_clips,
        size=(w, h)
    ).with_duration(duration) if MOVIEPY_V2 else CompositeVideoClip(valid_clips, size=(w,h)).set_duration(duration)

    # CRITICAL: Strip mask for final track clips in v2 to ensure switch
    final_clip.mask = None

    if final_audio:
        final_clip = v_set_audio(final_clip, final_audio)

    return final_clip

def render_preview_frame(track, layout, output_path="output/preview.png"):
    print(f"[*] Generating preview frame: {output_path}")
    print(f"[*] Layout received: {json.dumps(layout, indent=2)}")
    try:
        if not os.path.exists("output"):
            os.makedirs("output")
        clip = render_clip(track, "output", layout)
        # Save first frame (t=0) or mid frame
        save_t = min(1.0, clip.duration / 2) if clip.duration > 0 else 0
        print(f"[*] Saving frame (t={save_t}) to {output_path}...")
        clip.save_frame(output_path, t=save_t)
        print(f"[*] Frame saved successfully.")
        return output_path
    except Exception as e:
        import traceback
        err_msg = f"ERROR rendering preview frame: {e}\n{traceback.format_exc()}"
        log_error(err_msg)
        return None

def render_project(project_file="project.json", output_path="output/netease_weekly_final_compilation.mp4", intro_path="output/intro.mp4"):
    if not os.path.exists(project_file):
        raise FileNotFoundError(f"Project file not found: {project_file}")

    if not os.path.exists("output"):
        os.makedirs("output")

    with open(project_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    all_tracks = data.get('tracks', [])
    layout = data.get('layout')
    if not all_tracks:
        raise ValueError("No tracks found in project")

    print(f"[*] Starting compilation of {len(all_tracks)} tracks into one video...")

    clips = []
    crossfade_dur = 1.0 # 1 second crossfade

    # Check for Intro video
    if os.path.exists(intro_path):
        print(f"[*] Intro video found at {intro_path}, including it in compilation.")
        try:
            intro_clip = VideoFileClip(intro_path)
            clips.append(intro_clip)
        except Exception as e:
            print(f"!!! Error loading intro video: {e}")

    for i, track in enumerate(all_tracks):
        clip = render_clip(track, "output", layout)

        if clip is None:
            print(f"[*] Skipping {track.get('name', 'unknown')} due to missing/invalid clip (e.g. no audio).")
            continue

        # Apply 2-second audio fade-in at the start of each track
        fadein_dur = 2.0
        if not track.get("song_fadein_sec"):
            if MOVIEPY_V2:
                clip = clip.with_effects([AudioFadeIn(duration=fadein_dur)])
            else:
                clip = clip.fx(audio_fadein, fadein_dur)

        # Apply 2-second audio fade-out at the end of each track
        fadeout_dur = 2.0
        if MOVIEPY_V2:
            clip = clip.with_effects([AudioFadeOut(duration=fadeout_dur)])
        else:
            clip = clip.fx(audio_fadeout, fadeout_dur)

        # Apply crossfade if it's not the very first clip in the list
        if (len(clips) > 0) and crossfadein:
            if MOVIEPY_V2:
                clip = clip.with_effects([CrossFadeIn(duration=crossfade_dur)])
            else:
                clip = clip.fx(crossfadein, crossfade_dur)

        clips.append(clip)

    print("[*] Concatenating clips with crossfade...")
    final_video = concatenate_videoclips(clips, method="compose", padding=-crossfade_dur)

    if not clips:
        raise RuntimeError("No valid clips were rendered")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    print(f"[*] Exporting final video to {output_path}...")
    final_video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", preset="slow")
    print(f"[*] Done! Saved to {output_path}")

def render_track_demo(project_file="researched_playlist.json", track_index=0, output_path="output/track_demo.mp4", segment_source="highlight"):
    if not os.path.exists(project_file):
        raise FileNotFoundError(f"Project file not found: {project_file}")

    with open(project_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    tracks = data.get("tracks", [])
    layout = data.get("layout")
    if not tracks:
        raise ValueError("No tracks found in project.")
    if track_index < 0 or track_index >= len(tracks):
        raise IndexError(f"Track index out of range: {track_index}")

    clip = render_clip(tracks[track_index], "output", layout, render_options={"segment_source": segment_source})
    if clip is None:
        raise RuntimeError("Failed to render track demo clip.")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    clip.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", preset="medium")
    try:
        clip.close()
    except Exception:
        pass
    print(f"[*] Track demo saved to {output_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("project_file", nargs="?", default="project.json", help="Project JSON file")
    parser.add_argument("--track-demo", action="store_true", help="Render only one track demo clip")
    parser.add_argument("--track-index", type=int, default=0, help="Track index for demo rendering")
    parser.add_argument("--output-path", default=None, help="Output path for the full video or track demo")
    parser.add_argument("--intro-path", default="output/intro.mp4", help="Intro video path for full compilation")
    parser.add_argument("--segment-source", default="preferred", help="Segment source: preferred, highlight, or chorus_candidate")
    args = parser.parse_args()

    if args.track_demo:
        render_track_demo(
            project_file=args.project_file,
            track_index=args.track_index,
            output_path=args.output_path or "output/track_demo.mp4",
            segment_source=args.segment_source,
        )
    else:
        render_project(
            project_file=args.project_file,
            output_path=args.output_path or "output/netease_weekly_final_compilation.mp4",
            intro_path=args.intro_path,
        )
