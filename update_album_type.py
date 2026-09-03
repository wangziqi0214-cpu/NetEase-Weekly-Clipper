import json
import os
import sys

# Ensure src in path
sys.path.append(os.path.join(os.path.abspath('.'), 'src'))
from crawler.crawler import get_album_info
from pyncm import apis

def update_file(filename):
    if not os.path.exists(filename): return
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to read {filename}: {e}")
        return
    print(f'Updating {filename}...')
    changed = False
    for t in data.get('tracks', []):
        if 'album_type' not in t:
            try:
                # get track details
                details = apis.track.GetTrackDetail([t['id']])
                album_id = details['songs'][0]['al']['id']
                _, size = get_album_info(album_id)
                n_l = t['name'].lower()
                a_l = t['album'].lower()
                if 'live' in n_l or '现场' in n_l or 'live' in a_l or '现场' in a_l:
                    t['album_type'] = '现场'
                elif size >= 7: t['album_type'] = '专辑'
                elif 3 <= size <= 6: t['album_type'] = 'EP'
                else: t['album_type'] = '单曲'
                changed = True
                print(f"  {t['name']} -> {t['album_type']}")
            except Exception as e:
                print(e)
    if changed:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

update_file('raw_playlist.json')
update_file('researched_playlist.json')
update_file('project.json')
