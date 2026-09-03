
import json
import os

def sync():
    res_path = 'researched_playlist.json'
    proj_path = 'project.json'

    if not os.path.exists(res_path) or not os.path.exists(proj_path):
        print("Missing files.")
        return

    with open(res_path, 'r', encoding='utf-8') as f:
        res_data = json.load(f)
    with open(proj_path, 'r', encoding='utf-8') as f:
        proj_data = json.load(f)

    # Map tracks in researched to ID
    res_tracks = {t['id']: t for t in res_data['tracks']}

    updated_count = 0
    for t in proj_data['tracks']:
        tid = t['id']
        if tid in res_tracks:
            source = res_tracks[tid]
            # Sync key metadata
            t['description'] = source.get('description', t.get('description', ''))
            t['ai_description'] = source.get('ai_description', t.get('ai_description', ''))
            t['lyrics'] = source.get('lyrics', [])
            t['album_description'] = source.get('album_description', t.get('album_description', ''))
            updated_count += 1

    with open(proj_path, 'w', encoding='utf-8') as f:
        json.dump(proj_data, f, indent=2, ensure_ascii=False)

    print(f"Successfully synced lyrics and descriptions for {updated_count} tracks into {proj_path}.")

if __name__ == "__main__":
    sync()
