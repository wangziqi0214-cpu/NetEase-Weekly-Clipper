import json
import re
import os
import sys
import requests
from urllib.parse import urlparse, parse_qs
import pyncm
from pyncm import apis

class NetEaseCrawler:
    """
    Encapsulated NetEase Cloud Music crawler module.
    封装的网易云音乐爬虫模块。
    """
    def __init__(self, cookie_path='cookie.txt'):
        self.session = pyncm.GetCurrentSession()
        self.cookie_path = cookie_path
        self._load_cookies()

    def _load_cookies(self):
        if os.path.exists(self.cookie_path):
            from http.cookiejar import MozillaCookieJar
            print(f"[*] Loading cookies from {self.cookie_path}")
            cj = MozillaCookieJar(self.cookie_path)
            cj.load(ignore_discard=True, ignore_expires=True)
            self.session.cookies = cj

    def extract_playlist_id(self, url):
        """Extracts playlist ID from a NetEase Cloud Music URL."""
        if '/#/' in url:
            url = url.replace('/#/', '/')
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        if 'id' in query_params:
            return query_params['id'][0]
        match = re.search(r'/playlist/(\d+)', url)
        if match:
            return match.group(1)
        return None

    def get_playlist_tracks(self, url_or_id):
        """Fetches all tracks in a playlist. / 获取歌单中的所有歌曲。"""
        playlist_id = self.extract_playlist_id(url_or_id) if "http" in str(url_or_id) else url_or_id
        if not playlist_id:
            return None

        print(f"[*] Fetching Playlist ID: {playlist_id}")
        playlist_info = apis.playlist.GetPlaylistInfo(playlist_id)
        if playlist_info['code'] != 200:
            print(f"[!] Error fetching playlist: {playlist_info}")
            return None

        playlist = playlist_info['playlist']
        track_ids = [t['id'] for t in playlist['trackIds']]
        print(f"[*] Found {len(track_ids)} tracks. Fetching details...")

        chunk_size = 50
        all_tracks = []
        for i in range(0, len(track_ids), chunk_size):
            chunk = track_ids[i:i+chunk_size]
            details = apis.track.GetTrackDetail(chunk)
            if details['code'] == 200:
                all_tracks.extend(details['songs'])

        return {
            "playlist_id": playlist_id,
            "playlist_name": playlist['name'],
            "playlist_cover": playlist['coverImgUrl'],
            "description": playlist.get('description', ''),
            "tracks": all_tracks
        }

    def get_song_lyrics(self, song_id):
        """Retrieves and parses lyrics. / 获取并解析歌词。"""
        try:
            lrc_data = apis.track.GetTrackLyrics(song_id)
            lrc_text = lrc_data.get('lrc', {}).get('lyric', '')
            return self._parse_lrc(lrc_text)
        except Exception as e:
            print(f"   [!] Lyric fetch error for {song_id}: {e}")
            return []

    def _parse_lrc(self, lrc_str):
        entries = []
        if not lrc_str: return entries
        for line in lrc_str.split('\n'):
            line = line.strip()
            if not line: continue
            try:
                time_tags = re.findall(r'\[(\d+):(\d+\.?\d*)\]', line)
                if not time_tags: continue
                text = re.sub(r'\[.*?\]', '', line).strip()
                for (min_s, sec_s) in time_tags:
                    total_ms = (int(min_s) * 60 + float(sec_s)) * 1000
                    entries.append({"time": int(total_ms), "text": text})
            except: continue
        entries.sort(key=lambda x: x['time'])
        return entries

    def download_audio(self, song_id, save_dir="assets/audio"):
        """Downloads the MP3 file. / 下载 MP3 文件。"""
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        filename = os.path.join(save_dir, f"{song_id}.mp3")
        if os.path.exists(filename) and os.path.getsize(filename) > 1024:
            return os.path.abspath(filename)

        url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
        print(f"   [Download] Downloading {song_id}...")
        try:
            sess = requests.Session()
            sess.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://music.163.com/'
            })
            if os.path.exists(self.cookie_path):
                from http.cookiejar import MozillaCookieJar
                cj = MozillaCookieJar(self.cookie_path)
                cj.load(ignore_discard=True, ignore_expires=True)
                sess.cookies = cj

            r = sess.get(url, stream=True, timeout=15)
            if r.status_code in [200, 206]:
                with open(filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                return os.path.abspath(filename)
        except Exception as e:
            print(f"   [!] Download error for {song_id}: {e}")
        return ""
