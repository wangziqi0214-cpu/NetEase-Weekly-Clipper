import json
import re
import os
import sys
import time
from urllib.parse import urlparse, parse_qs

# Ensure we can import from src if needed, though this is a standalone script for now
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

import requests
import pyncm
from pyncm import apis

from src.researcher.common import fetch_album_detail, init_netease_session, normalize_release_type

def extract_playlist_id(url):
    """Extracts playlist ID from a NetEase Cloud Music URL."""
    # Handle fragmented URLs like /#/playlist?id=...
    if '/#/' in url:
        url = url.replace('/#/', '/')

    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    if 'id' in query_params:
        return query_params['id'][0]

    # Handle /playlist/12345 format if exists
    match = re.search(r'/playlist/(\d+)', url)
    if match:
        return match.group(1)

    return None

def get_best_cover_url(al_data):
    """Gets the simplistic cover url, preferring high res if available."""
    # NetEase often appends param size, we can strip to get original or replace with large size
    if 'picUrl' in al_data:
        url = al_data['picUrl']
        # Remove size constraints if present or force large
        return url
    return ""


def download_cover(url, cover_id, save_dir="assets/covers", max_retries=4):
    if not url or not cover_id:
        return ""

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    filename = os.path.join(save_dir, f"{cover_id}.jpg")
    if os.path.exists(filename) and os.path.getsize(filename) > 1024:
        return os.path.abspath(filename)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
        "Referer": "https://music.163.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "image" not in content_type and response.content[:16].lower().startswith(b"<html"):
                raise ValueError("cover response is not an image")

            with open(filename, "wb") as f:
                f.write(response.content)

            if os.path.getsize(filename) <= 1024:
                raise ValueError("cover file too small")

            return os.path.abspath(filename)
        except Exception as e:
            print(f"   [!] Cover download failed for {cover_id} attempt {attempt + 1}/{max_retries}: {e}")
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except Exception:
                    pass
            if attempt < max_retries - 1:
                time.sleep(1.5)

    return ""

def get_album_info(album_id):
    if not album_id:
        return {}
    print(f"   [Album] Fetching info for ID {album_id}...")
    return fetch_album_detail(album_id)

def parse_lrc(lrc_str):
    """
    Parse an LRC string into a list of dicts: [{'time': ms, 'text': '...'}]
    """
    entries = []
    if not lrc_str:
        return entries

    # Regex to match [mm:ss.xx]
    # Simple split might be enough if standard format
    for line in lrc_str.split('\n'):
        line = line.strip()
        if not line: continue

        # Check standard [mm:ss.xx] or [mm:ss.xxx]
        # Example: [00:01.000]Lyrics
        try:
            # Find all timestamps
            import re
            time_tags = re.findall(r'\[(\d+):(\d+\.?\d*)\]', line)
            if not time_tags: continue

            # Extract text (everything after the last ']')
            text = re.sub(r'\[.*?\]', '', line).strip()

            for (min_s, sec_s) in time_tags:
                total_ms = (int(min_s) * 60 + float(sec_s)) * 1000
                entries.append({
                    "time": int(total_ms),
                    "text": text
                })
        except Exception:
            continue

    # Sort by time
    entries.sort(key=lambda x: x['time'])

    # FOOLPROOF LYRICS: Filter out metadata lyrics completely here.
    # This guarantees the renderer will never see or display them.
    entries = [e for e in entries if not is_metadata_lyric(e['text'])]

    return entries

def download_track(url, song_id, save_dir="assets/audio"):
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    filename = os.path.join(save_dir, f"{song_id}.mp3")
    if os.path.exists(filename) and os.path.getsize(filename) > 1024:
        # Skip if exists and assumes valid (>1KB)
        return os.path.abspath(filename)

    print(f"   [Download] Downloading {song_id} from {url}...")
    try:
        # Use a session with headers to mimic browser
        sess = requests.Session()
        sess.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://music.163.com/'
        })

        # Load local cookies if available to help with VIP/Higher quality
        if os.path.exists('cookie.txt'):
             from http.cookiejar import MozillaCookieJar
             cj = MozillaCookieJar('cookie.txt')
             cj.load(ignore_discard=True, ignore_expires=True)
             sess.cookies = cj

        r = sess.get(url, stream=True, timeout=15)
        # 200 OK, or sometimes 20x. Note: NetEase might return 404 or a redirect page if invalid.
        if r.status_code in [200, 206]:
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Verify it's not an html error page (often >100KB)
            with open(filename, 'rb') as f:
                content = f.read(200).lower()
                if b'<!doctype' in content or b'<html' in content:
                     print("   [!] Downloaded content seems to be HTML error page.")
                     f.close()
                     os.remove(filename)
                     return ""

            if os.path.getsize(filename) < 1024:
                print("   [!] Downloaded file is too small.")
                os.remove(filename)
                return ""

            return os.path.abspath(filename)
        else:
            print(f"   [!] Failed download: Status {r.status_code}")
    except Exception as e:
        print(f"   [!] Download exception: {e}")

    return ""

def is_metadata_lyric(text):
    """
    Detect if a lyric line is metadata (credits, copyright, etc.) rather than actual song content.
    Returns True if the text contains production credits or copyright warnings.
    """
    if not text or not text.strip():
        return False

    text_clean = text.lower().replace(' ', '').replace('　', '').replace('\u200b', '')
    if not text_clean: return True

    copyright_kws = ['未经许可', '不得翻唱', '版权所有', '侵权必究', 'copyright', '未经授权']
    if any(kw in text_clean for kw in copyright_kws):
        return True

    safe_prefixes = [
        '作词', '作曲', '编曲', '制作', '混音', '母带', '监制', '统筹', '企划',
        '作詞', '編曲', '製作', '監製', '錄音', '母帶', '企劃', '发行', '發行',
        '后期', '混音室', '录音室', '出品', 'op', 'sp', 'isrc', 'upc', '词曲',
        '执行监制', '总监制', '总策划', '总出品', '行政监制', '音乐监制', '音乐统筹',
        '和声编写', '配唱', '人声编辑', '封面设计', '视觉设计', '企划统筹', '制作统筹'
    ]
    if any(text_clean.startswith(prefix) for prefix in safe_prefixes):
        return True

    # Catch standalone roles followed by spaces, slashes, or commas (e.g. "词 张三", "录音 李四")
    text_stripped = text.strip()
    space_roles = [
        '词', '曲', '唱', '录音', '录像', '录影', '录制', '和声', '和音', '合声', '视效', '混音',
        '吉他', '贝斯', '键盘', '鼓', '配唱', '后期', '企划', '导演', '摄影', '剪辑', '导播',
        '作词', '作曲', '编曲', '制作', '制作人', '出品', '出品人', '发行', '监制', '总监制',
        '母带', '统筹', '执行', '总策划', '总出品', '混音室', '录音室', 'op', 'sp', 'isrc', 'upc',
        '词曲', '演唱', '主唱', '吉它', '结他', '貝斯', '鍵盤', '录音工程', '母带工程', '混音工程',
        '和声编写', '人声编辑', '配唱制作', '企划统筹', '音乐制作',
        'lyric', 'lyrics', 'composer', 'arranger', 'producer', 'mixer', 'master',
        'mastering', 'recording', 'guitar', 'bass', 'drum', 'vocal', 'vocals', 'music',
        'produced', 'written', 'mixed', 'mastered'
    ]
    for role in space_roles:
        if text_stripped.startswith(role + ' ') or text_stripped.startswith(role + '　'):
            return True
        if text_stripped.startswith(role + '/') or text_stripped.startswith(role + '、'):
            return True

    import re
    parts = re.split(r'[:：]', text.lower())
    if len(parts) > 1:
        prefix = parts[0].replace(' ', '').strip()
        metadata_roles = [
            '演唱', '主唱', '吉他', '贝斯', '鼓', '键盘', '和声', '和音', '合声', '视效', '弦乐', '吉它',
            '结他', '貝斯', '鍵盤', '录音', '录像', '录影', '导演', '导播', '摄影', '剪辑', '配唱', '和声编写',
            'lyric', 'lyrics', 'composer', 'arranger', 'producer',
            'mixer', 'mastering', 'recording', 'guitar', 'bass', 'drum', 'vocal', 'vocals',
            'presented', 'music', 'musicby', '词', '曲', '唱'
        ]
        all_roles = safe_prefixes + metadata_roles
        if prefix in all_roles or any(prefix.endswith(r) for r in all_roles):
             return True

    match = re.match(r'^【(.*?)】|^\[(.*?)\]', text.replace(' ', ''))
    if match:
        role = match.group(1) or match.group(2)
        if role:
            role = role.lower()
            metadata_roles = ['作词', '作曲', '编曲', '制作', '混音', '演唱', '原唱', 'lyric', 'music', 'vocal', '词', '曲', '唱']
            if role in metadata_roles or any(role.endswith(r) for r in metadata_roles):
                 return True

    return False

def get_valid_lyric_ranges(lyrics, song_duration_ms):
    """
    Analyze lyrics and return time ranges that contain actual song content (not metadata).
    Returns a list of (start_ms, end_ms) tuples representing valid ranges.
    """
    if not lyrics or len(lyrics) == 0:
        return [(0, song_duration_ms)]

    # Find the last metadata lyric
    last_metadata_time = 0
    for lyric in lyrics:
        lyric_time = lyric.get('time', 0)
        lyric_text = lyric.get('text', '').strip()

        if is_metadata_lyric(lyric_text):
            last_metadata_time = max(last_metadata_time, lyric_time)

    # Add a 2-second buffer after the last metadata
    safe_start = last_metadata_time + 2000 if last_metadata_time > 0 else 0

    # Ensure we don't start too late in the song
    # If metadata takes up more than 15% of the song, cap it
    max_metadata_skip = int(song_duration_ms * 0.15)
    safe_start = min(safe_start, max_metadata_skip)

    return [(safe_start, song_duration_ms)]

def calculate_highlight(song_duration_ms, song_info, lyrics=None):
    """
    Determines the 30s highlight segment.
    Priority 1: NetEase API highlight info (if available).
    Priority 2: Lyrics density analysis (find densest 30s window).
    Priority 3: 30% mark of the song (fallback).
    """
    clip_duration = 30000  # 30 seconds

    # --- Priority 1: NetEase API Highlight ---
    # Check for various possible highlight fields in the API response
    # Some endpoints return 'pc' (privilege control) or specific highlight fields
    highlight_keys = ['highlightTime', 'startTime', 'hightlightTime']
    for key in highlight_keys:
        if key in song_info and song_info[key]:
            api_start = int(song_info[key])
            if 0 < api_start < song_duration_ms - clip_duration:
                print(f"   [Highlight] Using API highlight: {api_start}ms")
                return api_start, api_start + clip_duration

    # --- Priority 2: Lyrics Density Analysis ---
    if lyrics and len(lyrics) > 5:
        try:
            # First, determine valid time ranges (excluding metadata)
            valid_ranges = get_valid_lyric_ranges(lyrics, song_duration_ms)
            safe_start_ms = valid_ranges[0][0] if valid_ranges else 0

            # Find the 30s window with the most lyric lines in valid range
            best_start = safe_start_ms
            max_density = 0

            upper_bound = song_duration_ms - clip_duration

            # Start searching after 40s to avoid boring early verses, unless song is too short
            search_start_ideal = 40000
            search_start = max(safe_start_ms, search_start_ideal)

            if search_start >= upper_bound:
                # If song is super short, fallback to 30% of the song
                search_start = max(safe_start_ms, int(song_duration_ms * 0.3))

            for candidate_start in range(search_start, max(search_start + 1, upper_bound), 5000):  # Step 5s
                candidate_end = candidate_start + clip_duration

                # Count only non-metadata lyrics in this window
                density = 0
                for l in lyrics:
                    lyric_time = l.get('time', 0)
                    lyric_text = l.get('text', '').strip()
                    if candidate_start <= lyric_time < candidate_end and lyric_text:
                        # Skip if this is metadata
                        if not is_metadata_lyric(lyric_text):
                            density += 1

                if density > max_density:
                    max_density = density
                    best_start = candidate_start

            if max_density >= 3:  # At least 3 lyric lines in the window
                # Find the first valid (non-metadata) lyric line in the best window
                first_lyric_time = None
                for lyric in lyrics:
                    lyric_time = lyric.get('time', 0)
                    lyric_text = lyric.get('text', '').strip()
                    if best_start <= lyric_time < best_start + clip_duration and lyric_text:
                        if not is_metadata_lyric(lyric_text):
                            first_lyric_time = lyric_time
                            break

                # Start 500ms before the first lyric for a better lead-in
                if first_lyric_time is not None:
                    adjusted_start = max(safe_start_ms, first_lyric_time - 500)
                    print(f"   [Highlight] Using lyrics density: {adjusted_start}ms (density={max_density}, skipped metadata until {safe_start_ms}ms)")
                    return adjusted_start, adjusted_start + clip_duration
                else:
                    print(f"   [Highlight] Using lyrics density: {best_start}ms (density={max_density}, skipped metadata)")
                    return best_start, best_start + clip_duration
        except Exception as e:
            print(f"   [!] Lyrics analysis error: {e}")

    # --- Priority 3: Default 30% Position ---
    start_ms = int(song_duration_ms * 0.30)

    # Ensure we don't go over bounds
    if start_ms + clip_duration > song_duration_ms:
        start_ms = max(0, song_duration_ms - clip_duration)

    print(f"   [Highlight] Using default 30%: {start_ms}ms")
    return start_ms, start_ms + clip_duration


def crawl_playlist_from_track_ids(
    track_ids,
    playlist_id=None,
    playlist_name=None,
    playlist_cover=None,
    description=None,
    url=None,
    output_path="raw_playlist.json",
    cookie_path="cookie.txt",
):
    """
    Fetches details and downloads assets for an authoritative list of track IDs,
    strictly preserving their order and failing fast if any track is missing.
    """
    if not track_ids:
        raise ValueError("track_ids must not be empty")

    normalized_ids = [str(tid).strip() for tid in track_ids if str(tid).strip()]
    if len(normalized_ids) != len(track_ids):
        raise ValueError(f"Invalid track_ids provided: {track_ids}")

    if os.path.exists(cookie_path):
        print(f"[*] Loading cookies from {cookie_path}")
    init_netease_session(cookie_path)

    if not playlist_id and url:
        playlist_id = extract_playlist_id(url)

    final_playlist_id = str(playlist_id or "custom_playlist")
    final_playlist_name = playlist_name or "华语新歌周刊"
    final_playlist_cover = playlist_cover or ""
    final_description = description or ""

    if playlist_id and (not playlist_name or not playlist_cover):
        try:
            playlist_info = apis.playlist.GetPlaylistInfo(playlist_id)
            if isinstance(playlist_info, dict) and playlist_info.get("code") == 200 and "playlist" in playlist_info:
                pl = playlist_info["playlist"]
                final_playlist_name = playlist_name or pl.get("name") or final_playlist_name
                final_playlist_cover = playlist_cover or pl.get("coverImgUrl") or final_playlist_cover
                final_description = description or pl.get("description") or final_description
        except Exception as e:
            print(f"[*] Note: Could not fetch playlist metadata for {playlist_id}: {e}")

    print(f"[*] Found {len(normalized_ids)} authoritative tracks. Fetching details...")

    chunk_size = 50
    fetched_songs_by_id = {}

    for i in range(0, len(normalized_ids), chunk_size):
        chunk = normalized_ids[i:i + chunk_size]
        details = apis.track.GetTrackDetail(chunk)
        if not isinstance(details, dict) or details.get("code") != 200:
            raise RuntimeError(f"Failed to fetch track details for chunk {i}: {details}")
        for s in details.get("songs") or []:
            fetched_songs_by_id[str(s["id"])] = s

    missing_ids = [tid for tid in normalized_ids if tid not in fetched_songs_by_id]
    if missing_ids:
        raise RuntimeError(
            f"Failed to fetch details for {len(missing_ids)} tracks: {missing_ids}. "
            f"Expected {len(normalized_ids)} tracks, received {len(fetched_songs_by_id)}. Fail fast!"
        )

    ordered_raw_songs = [fetched_songs_by_id[tid] for tid in normalized_ids]
    if len(ordered_raw_songs) != len(normalized_ids):
        raise RuntimeError(f"Track count mismatch: expected {len(normalized_ids)}, got {len(ordered_raw_songs)}")

    print(f"[*] Processing audio downloads...")

    parsed_tracks = []
    for song in ordered_raw_songs:
        s_id = song['id']
        s_name = song['name']
        s_artists = [ar['name'] for ar in (song.get('ar') or [])]
        s_album = (song.get('al') or {}).get('name', '')
        s_album_id = (song.get('al') or {}).get('id')
        s_cover = get_best_cover_url(song.get('al') or {})
        s_duration = song.get('dt', 0)

        # 0. Fetch Album Description and official metadata
        album_info = get_album_info(s_album_id)
        s_album_desc = album_info.get('description', '')
        s_album_size = album_info.get('size', 0)
        s_album_type_raw = album_info.get('type')
        s_album_subtype_raw = album_info.get('subType')
        s_publish_time = album_info.get('publishTime') or song.get('publishTime') or 0
        s_release_company = album_info.get('company', '')

        album_type = normalize_release_type(
            s_album_type_raw,
            s_album_subtype_raw,
            s_album_size,
            s_name,
            s_album,
        )

        # 0.5 Fetch Lyrics
        s_lyrics = []
        try:
            lrc_data = apis.track.GetTrackLyrics(s_id)
            lrc_text = lrc_data.get('lrc', {}).get('lyric', '')
            s_lyrics = parse_lrc(lrc_text)
            print(f"   [Lyrics] Parsed {len(s_lyrics)} lines for {s_name}")
        except Exception:
            pass

        # 1 & 2. Try fetching and downloading audio with retries
        local_path = ""
        max_retries = 3
        s_audio_url = ""
        for attempt in range(max_retries):
            try:
                audio_info = apis.track.GetTrackAudio([s_id])
                if audio_info.get('data') and len(audio_info['data']) > 0 and audio_info['data'][0].get('url'):
                    s_audio_url = audio_info['data'][0]['url']
            except Exception:
                pass

            # Fallback to outer URL
            if not s_audio_url:
                s_audio_url = f"http://music.163.com/song/media/outer/url?id={s_id}.mp3"

            local_path = download_track(s_audio_url, s_id)
            if local_path:
                break
            else:
                print(f"   [!] Attempt {attempt+1} failed to download audio for {s_name}. Retrying in 2 seconds...")
                time.sleep(2)

        if not local_path:
            print(f"   [!] Warning: Audio download failed for {s_name} after {max_retries} attempts. Song will remain in list (No Audio).")

        cover_local_path = download_cover(s_cover, s_album_id or s_id)

        # Highlight Logic
        start, end = calculate_highlight(s_duration, song, s_lyrics)

        parsed_tracks.append({
            "id": s_id,
            "name": s_name,
            "artists": s_artists,
            "album": s_album,
            "album_id": s_album_id,
            "album_type": album_type,
            "album_description": s_album_desc,
            "release_title": s_album,
            "release_raw_type": s_album_type_raw,
            "release_raw_subtype": s_album_subtype_raw,
            "release_size": s_album_size,
            "release_publish_time": s_publish_time,
            "release_company": s_release_company,
            "normalized_release_type": album_type,
            "cover_url": s_cover,
            "cover_local_path": cover_local_path,
            "audio_url": s_audio_url,
            "local_path": local_path,
            "duration_ms": s_duration,
            "highlight_start_ms": start,
            "highlight_end_ms": end,
            "lyrics": s_lyrics,
            "raw_info_snippet": {
                "fee": song.get('fee'),
                "mv": song.get('mv')
            }
        })

    if len(parsed_tracks) != len(normalized_ids):
        raise RuntimeError(f"Parsed track count mismatch: expected {len(normalized_ids)}, got {len(parsed_tracks)}")

    parsed_ids = [str(t["id"]) for t in parsed_tracks]
    if parsed_ids != normalized_ids:
        raise RuntimeError(f"Parsed track order mismatch: expected {normalized_ids}, got {parsed_ids}")

    project_data = {
        "version": "2.1",
        "playlist_id": final_playlist_id,
        "playlist_name": final_playlist_name,
        "playlist_cover": final_playlist_cover,
        "description": final_description,
        "tracks": parsed_tracks
    }

    # Load default layout if exists (use absolute path from project root)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    default_layout_path = os.path.join(project_root, 'config', 'default_layout.json')
    if os.path.exists(default_layout_path):
        try:
            with open(default_layout_path, 'r', encoding='utf-8') as f:
                project_data["layout"] = json.load(f)
            print(f"   [+] Applied default layout from config/default_layout.json")
        except Exception as e:
            print(f"   [!] Could not load default layout: {e}")
    else:
        print(f"   [!] Default layout not found at: {default_layout_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(project_data, f, indent=2, ensure_ascii=False)

    # Generate a simple track list document
    tracklist_path = output_path.replace('.json', '_tracklist.txt')
    try:
        with open(tracklist_path, 'w', encoding='utf-8') as f:
            f.write(f"歌单：{final_playlist_name}\n")
            f.write(f"播放列表ID：{final_playlist_id}\n")
            f.write(f"曲目数量：{len(parsed_tracks)}\n")
            f.write("=" * 50 + "\n\n")

            for i, track in enumerate(parsed_tracks, 1):
                artist = track['artists'][0] if track['artists'] else "未知艺人"
                album = track['album']
                f.write(f"{i}. {artist} 《{album}》\n")

        print(f"[*] Track list saved to {tracklist_path}")
    except Exception as e:
        print(f"[!] Could not save track list: {e}")

    print(f"[*] Success! Saved {len(parsed_tracks)} tracks to {output_path}")
    return output_path


def crawl_playlist(url, output_path="raw_playlist.json"):
    print(f"[*] Processing URL: {url}")
    playlist_id = extract_playlist_id(url)
    if not playlist_id:
        print("[!] Invalid Playlist URL")
        return None

    print(f"[*] Fetching Playlist ID: {playlist_id}")

    # Initialize pyncm
    cookie_path = 'cookie.txt'
    if os.path.exists(cookie_path):
        print(f"[*] Loading cookies from {cookie_path}")
    init_netease_session(cookie_path)

    try:
        # Get Playlist Details
        playlist_info = apis.playlist.GetPlaylistInfo(playlist_id)

        if playlist_info['code'] != 200:
            print(f"[!] Error fetching playlist: {playlist_info}")
            return None

        playlist = playlist_info['playlist']
        track_ids = [t['id'] for t in playlist.get('trackIds') or []]
        if not track_ids:
            print(f"[!] Playlist has no tracks: {playlist_id}")
            return None

        return crawl_playlist_from_track_ids(
            track_ids=track_ids,
            playlist_id=playlist_id,
            playlist_name=playlist.get('name'),
            playlist_cover=playlist.get('coverImgUrl'),
            description=playlist.get('description', ''),
            url=url,
            output_path=output_path,
            cookie_path=cookie_path,
        )

    except Exception as e:
        print(f"[!] Exception during crawl: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = "https://music.163.com/playlist?id=17684977343"

    output_path = sys.argv[2] if len(sys.argv) > 2 else "raw_playlist.json"
    result_path = crawl_playlist(url, output_path=output_path)
    sys.exit(0 if result_path else 1)
