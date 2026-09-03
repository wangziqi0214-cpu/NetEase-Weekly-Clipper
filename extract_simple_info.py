import json
import os

input_file = "raw_playlist.json"
output_file = "simplified_playlist.json"

if os.path.exists(input_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    simplified_tracks = []
    for t in data.get('tracks', []):
        simplified_tracks.append({
            "name": t.get("name"),
            "artists": t.get("artists"),
            "album": t.get("album"),
            "album_type": t.get("album_type")
        })

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(simplified_tracks, f, indent=2, ensure_ascii=False)

    print(f"Successfully extracted {len(simplified_tracks)} tracks to {output_file}.")
else:
    print(f"Error: {input_file} not found.")
