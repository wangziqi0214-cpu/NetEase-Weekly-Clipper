import json
import os
import sys

# Ensure src is in path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from utils.netease_crawler import NetEaseCrawler

def main():
    crawler = NetEaseCrawler()
    playlist_url = "https://music.163.com/#/playlist?id=17748231391"

    print(f"[*] Starting crawl for: {playlist_url}")
    data = crawler.get_playlist_tracks(playlist_url)

    if not data:
        print("[!] Failed to fetch playlist data.")
        return

    # Post-processing: fetch lyrics and download audio for each track
    # In a real scenario we might want to cap this if there are many tracks
    parsed_tracks = []
    tracks_to_process = data['tracks']
    print(f"[*] Processing {len(tracks_to_process)} tracks...")

    for i, track in enumerate(tracks_to_process):
        s_id = track['id']
        s_name = track['name']
        print(f"[{i+1}/{len(tracks_to_process)}] Processing: {s_name} (ID: {s_id})")

        # Get lyrics
        lyrics = crawler.get_song_lyrics(s_id)

        # Download audio
        local_path = crawler.download_audio(s_id)

        # Build parsed track data
        parsed_tracks.append({
            "id": s_id,
            "name": s_name,
            "artists": [ar['name'] for ar in track['ar']],
            "album": track['al']['name'],
            "cover_url": track['al'].get('picUrl', ''),
            "local_path": local_path,
            "duration_ms": track.get('dt', 0),
            "lyrics": lyrics
        })

    # Save to JSON
    output_data = {
        "playlist_id": data["playlist_id"],
        "playlist_name": data["playlist_name"],
        "playlist_cover": data["playlist_cover"],
        "description": data["description"],
        "tracks": parsed_tracks
    }

    output_path = "raw_playlist_requested.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"[*] Done! Saved to {output_path}")

if __name__ == "__main__":
    main()
