from src.renderer.renderer import render_clip, MOVIEPY_V2
import json
import os

def render_preview():
    if not os.path.exists("project.json"):
        print("project.json not found")
        return

    if not os.path.exists("output"):
        os.makedirs("output")

    with open("project.json", 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data['tracks']:
        print("No tracks found")
        return

    # Just take the first track for preview
    track = data['tracks'][0]
    print(f"[*] Rendering preview for: {track['name']}")

    clip = render_clip(track, "output")

    output_path = "output/single_track_preview.mp4"
    # Render a shorter version if it's too long, but usually it's 30s
    # We'll just render the full clip duration set for this song
    clip.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast")
    print(f"[*] Preview ready at {output_path}")

if __name__ == "__main__":
    render_preview()
