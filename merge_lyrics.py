
import json
import os

def merge():
    raw_path = 'raw_playlist.json'
    res_path = 'researched_playlist.json'

    if not os.path.exists(raw_path) or not os.path.exists(res_path):
        print("Missing files.")
        return

    with open(raw_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    with open(res_path, 'r', encoding='utf-8') as f:
        res_data = json.load(f)

    # Map lyrics from raw to researched
    # tracks are usually in same order, but let's use ID if available
    raw_tracks = {t['id']: t for t in raw_data['tracks']}

    merged_count = 0
    for t in res_data['tracks']:
        tid = t['id']
        if tid in raw_tracks and 'lyrics' in raw_tracks[tid]:
            t['lyrics'] = raw_tracks[tid]['lyrics']
            merged_count += 1

    with open(res_path, 'w', encoding='utf-8') as f:
        json.dump(res_data, f, indent=2, ensure_ascii=False)

    print(f"Successfully merged lyrics for {merged_count} tracks into {res_path}.")

if __name__ == "__main__":
    merge()
