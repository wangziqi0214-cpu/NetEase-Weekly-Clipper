import streamlit as st
import json
import os
import sys
from streamlit_drawable_canvas import st_canvas

# Add project root path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

# Add local lib path
lib_dir = os.path.join(root_dir, 'lib')
if os.path.exists(lib_dir) and lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

st.set_page_config(layout="wide", page_title="NetEase Clipper Editor")

def load_data(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    st.success(f"Saved to {path}")

def _get_font_name(path):
    from fontTools import ttLib
    try:
        # For TTC files, we just take the first font in the collection for the label
        if path.lower().endswith(".ttc"):
            ttc = ttLib.TTCollection(path)
            font = ttc.fonts[0]
        else:
            font = ttLib.TTFont(path)

        # Name IDs: 4=Full name, 1=Font family
        # We try to find Chinese name first (platform 3, encoding 1, lang 0x404 or 0x804)
        name = ""
        for record in font['name'].names:
            if record.nameID in [4, 1]:
                # Try to decode
                try:
                    curr_name = record.toUnicode()
                    # Prefer Chinese names if available
                    if record.langID in [0x404, 0x804]:
                        return curr_name
                    if not name: name = curr_name
                except:
                    continue
        return name if name else os.path.basename(path)
    except:
        return os.path.basename(path)

def get_available_fonts():
    font_dirs = [
        "/System/Library/Fonts",
        "/System/Library/Fonts/Supplemental",
        "/Library/Fonts",
        os.path.expanduser("~/Library/Fonts"),
        "C:\\Windows\\Fonts",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        root_dir # Also scan project root for custom uploaded fonts
    ]
    fonts = {}
    import glob
    for d in font_dirs:
        if os.path.exists(d):
            # Only scan a subset or popular ones if there are too many,
            # but usually it's fine for Streamlit to handle a few hundred
            for f in glob.glob(os.path.join(d, "*.tt*")):
                display_name = _get_font_name(f)
                # Avoid duplicates and empty names
                if display_name and display_name not in fonts:
                    fonts[display_name] = f

    return dict(sorted(fonts.items()))

def main():
    st.title("🎵 NetEase Weekly Clipper - Global Editor")

    # File selection
    chk_files = ["project.json", "researched_playlist.json", "raw_playlist.json"]
    active_file = None
    for f in chk_files:
        if os.path.exists(f):
            active_file = f
            break

    if not active_file:
        st.error("No playlist json found. Run crawler first.")
        return

    if 'data' not in st.session_state:
        st.session_state.data = load_data(active_file)

    data = st.session_state.data
    layout = data.get("layout", {})

    # --- Interactive Layout Workspace ---
    st.header("🎚️ Visual Layout Workspace")

    # Canvas Settings
    CANVAS_W = 400
    CANVAS_H = 533
    SCALE = 1080 / CANVAS_W # 2.7

    # Map layout to canvas objects
    # We use Rects to represent elements
    # Album Art (Rect)
    # Text (Rects or just Points, but Rects are easier to drag)

    # --- State Management to prevent "jumping" ---
    if 'canvas_state' not in st.session_state:
        st.session_state.canvas_state = None  # Stores the actual JSON returned by canvas

    # Check if sliders changed (Slider -> Canvas sync)
    sliders_changed = False
    slider_keys = ["s_art_y", "s_art_size", "s_title_y", "s_artist_y", "s_desc_y", "s_lyrics_y"]
    for k in slider_keys:
        if k in st.session_state:
            # We compare with a shadow copy of the layout to detect user sliding
            if 'shadow_layout' not in st.session_state:
                 st.session_state.shadow_layout = {}
            if st.session_state.get(k) != st.session_state.shadow_layout.get(k):
                sliders_changed = True
                break

    # Generate initial drawing only when needed
    if 'initial_drawing_stable' not in st.session_state or sliders_changed:
        initial_drawing = {"objects": []}

        def add_to_drawing(name, x, y, w_val, h_val, color):
            if x == "center":
                cx = CANVAS_W / 2 - (w_val / SCALE) / 2
            else:
                cx = x / SCALE
            cy = ((y * 1440 if isinstance(y, float) and y <= 1.0 else y) / SCALE)
            initial_drawing["objects"].append({
                "type": "rect", "left": cx, "top": cy,
                "width": w_val / SCALE, "height": h_val / SCALE,
                "fill": color, "stroke": color, "strokeWidth": 2, "label": name
            })

        if layout["album_art"].get("visible", True):
            add_to_drawing("Album", layout["album_art"].get("x", "center"), layout["album_art"].get("y", 200), int(1080 * layout["album_art"].get("size", 0.7)), int(1080 * layout["album_art"].get("size", 0.7)), "rgba(0, 255, 0, 0.3)")
        if layout["title"].get("visible", True):
            add_to_drawing("Title", layout["title"].get("x", "center"), layout["title"].get("y", 0.55), 800, 80, "rgba(255, 0, 0, 0.3)")
        if layout["artist"].get("visible", True):
            add_to_drawing("Artist", layout["artist"].get("x", "center"), layout["artist"].get("y", 0.60), 600, 60, "rgba(0, 0, 255, 0.3)")
        if layout["description"].get("visible", True):
            add_to_drawing("Description", layout["description"].get("x", 100), layout["description"].get("y", 0.75), 800, 200, "rgba(255, 255, 255, 0.3)")

        ly_cfg_draw = layout.get("lyrics", {})
        if ly_cfg_draw.get("visible", True):
            art_y_draw = layout["album_art"].get("y", 200)
            art_size_draw = int(1080 * layout["album_art"].get("size", 0.7))
            ly_y_draw = ly_cfg_draw.get("y", "album_bottom")
            if ly_y_draw == "album_bottom":
                 ly_y_draw = art_y_draw + art_size_draw - 80
            add_to_drawing("Lyrics", "center", ly_y_draw, art_size_draw, 80, "rgba(255, 0, 255, 0.4)")

        if layout["progress_bar"].get("visible", True):
            add_to_drawing("Progress", layout["progress_bar"].get("x", "center"), layout["progress_bar"].get("y", 0.68), 880, 10, "rgba(255, 255, 0, 0.5)")
        st.session_state.initial_drawing_stable = initial_drawing

        # Sync shadow layout
        st.session_state.shadow_layout = {k: st.session_state.get(k) for k in slider_keys}

    col_canvas, col_info = st.columns([1, 1])

    with col_canvas:
        st.write("Drag boxes to reposition. (Stable Mode Enabled)")
        canvas_result = st_canvas(
            fill_color="rgba(255, 165, 0, 0.3)",
            stroke_width=2,
            initial_drawing=st.session_state.initial_drawing_stable,
            update_streamlit=True,
            height=CANVAS_H,
            width=CANVAS_W,
            drawing_mode="transform",
            key="layout_canvas",
            background_color="#222"
        )

    with col_info:
        if st.button("🖼 Refresh Preview Frame"):
             try:
                 import src.renderer.renderer
                 import importlib
                 importlib.reload(src.renderer.renderer)
                 from src.renderer.renderer import render_preview_frame
                 import time
                 ts = int(time.time())
                 preview_path = render_preview_frame(data["tracks"][0], layout, f"output/preview_{ts}.png")
                 if preview_path:
                     st.session_state.preview_path = preview_path
                 else:
                     st.error("Rendering failed (Check terminal for details). Is your font path valid?")
             except Exception as e:
                 st.error(f"UI Error: {e}")
                 import traceback
                 st.code(traceback.format_exc())

        if st.session_state.get('preview_path') and os.path.exists(st.session_state.preview_path):
            from PIL import Image
            img = Image.open(st.session_state.preview_path)
            st.image(img, caption="Render Preview", use_container_width=True)

    # --- Handle Canvas Updates (Moved up to affect sliders in the SAME run) ---
    color_map = {
        "rgba(0, 255, 0, 0.3)": "Album",
        "rgba(255, 0, 0, 0.3)": "Title",
        "rgba(0, 0, 255, 0.3)": "Artist",
        "rgba(255, 255, 255, 0.3)": "Description",
        "rgba(255, 0, 255, 0.4)": "Lyrics",
        "rgba(255, 255, 0, 0.5)": "Progress"
    }

    if canvas_result and canvas_result.json_data:
        objs = canvas_result.json_data.get("objects", [])
        for obj in objs:
            # Check label first, fallback to fill color
            label = obj.get("label") or color_map.get(obj.get("fill"))
            if not label: continue

            new_y = round(obj.get("top") * SCALE, 1)
            new_x_val = round(obj.get("left") * SCALE, 1)

            if label == "Album":
                if abs(layout["album_art"]["y"] - new_y) > 1.0:
                    layout["album_art"]["y"] = new_y
                    st.session_state["s_art_y"] = int(new_y)
                    st.session_state.shadow_layout["s_art_y"] = int(new_y)

                new_size = round((obj.get("width") * SCALE) / 1080, 2)
                if abs(layout["album_art"]["size"] - new_size) > 0.01:
                    layout["album_art"]["size"] = new_size
                    st.session_state["s_art_size"] = new_size
                    st.session_state.shadow_layout["s_art_size"] = new_size

                # Centering logic
                center_x = new_x_val + (obj.get("width") * SCALE / 2)
                if abs(center_x - 540) < 15:
                    layout["album_art"]["x"] = "center"
                else:
                    layout["album_art"]["x"] = new_x_val

            elif label == "Title":
                if abs(layout["title"]["y"] - new_y) > 1.0:
                    layout["title"]["y"] = new_y
                    st.session_state["s_title_y"] = int(new_y)
                    st.session_state.shadow_layout["s_title_y"] = int(new_y)
            elif label == "Artist":
                if abs(layout["artist"]["y"] - new_y) > 1.0:
                    layout["artist"]["y"] = new_y
                    st.session_state["s_artist_y"] = int(new_y)
                    st.session_state.shadow_layout["s_artist_y"] = int(new_y)
            elif label == "Description":
                if abs(layout["description"]["y"] - new_y) > 1.0:
                    layout["description"]["y"] = new_y
                    st.session_state["s_desc_y"] = int(new_y)
                    st.session_state.shadow_layout["s_desc_y"] = int(new_y)
            elif label == "Progress":
                if abs(layout["progress_bar"]["y"] - new_y) > 1.0:
                    layout["progress_bar"]["y"] = new_y
            elif label == "Lyrics":
                if layout["lyrics"].get("y") == "album_bottom" or abs(layout["lyrics"]["y"] - new_y) > 1.0:
                    layout["lyrics"]["y"] = new_y
                    st.session_state["s_lyrics_y"] = int(new_y)
                    st.session_state.shadow_layout["s_lyrics_y"] = int(new_y)

    # --- Sidebar: Detailed Controls ---
    st.sidebar.header("🎨 Fine-tune Layout")
    st.sidebar.info("PIL-Renderer v4.0 Active")

    # Pre-load fonts
    if 'available_fonts' not in st.session_state:
        st.session_state.available_fonts = get_available_fonts()
    fonts_dict = st.session_state.available_fonts
    font_labels = list(fonts_dict.keys())

    def font_selector(label, current_path, key):
        # find index of current path
        current_label = font_labels[0]
        for l, p in fonts_dict.items():
            if p.lower() == current_path.lower():
                current_label = l
                break
        idx = font_labels.index(current_label) if current_label in font_labels else 0
        new_label = st.selectbox(label, font_labels, index=idx, key=key)
        return fonts_dict[new_label]

    with st.sidebar.expander("Album Art", expanded=False):
        layout["album_art"]["visible"] = st.checkbox("Visible", layout["album_art"].get("visible", True), key="s_art_v")
        layout["album_art"]["y"] = st.slider("Y Position", 0, 2000, int(layout["album_art"]["y"]), key="s_art_y")
        layout["album_art"]["size"] = st.slider("Size", 0.1, 1.0, float(layout["album_art"]["size"]), key="s_art_size")

    with st.sidebar.expander("Title", expanded=False):
        layout["title"]["visible"] = st.checkbox("Visible", layout["title"].get("visible", True), key="s_title_v")
        layout["title"]["y"] = st.slider("Y Position", 0, 2000, int(layout["title"].get("y", 0.55)), key="s_title_y")
        layout["title"]["font_size"] = st.number_input("Font Size", 10, 200, int(layout["title"].get("font_size", 60)), key="s_title_fs")
        layout["title"]["font"] = font_selector("Font", layout["title"].get("font", ""), "s_title_font")

    with st.sidebar.expander("Artist", expanded=False):
        layout["artist"]["visible"] = st.checkbox("Visible", layout["artist"].get("visible", True), key="s_artist_v")
        layout["artist"]["y"] = st.slider("Y Position", 0, 2000, int(layout["artist"].get("y", 0.60)), key="s_artist_y")
        layout["artist"]["font_size"] = st.number_input("Font Size", 10, 200, int(layout["artist"].get("font_size", 40)), key="s_artist_fs")
        layout["artist"]["font"] = font_selector("Font", layout["artist"].get("font", ""), "s_artist_font")

    with st.sidebar.expander("Description", expanded=False):
        layout["description"]["visible"] = st.checkbox("Visible", layout["description"].get("visible", True), key="s_desc_v")
        layout["description"]["y"] = st.slider("Y Position", 0, 2000, int(layout["description"].get("y", 0.75)), key="s_desc_y")
        layout["description"]["font_size"] = st.number_input("Font Size", 10, 200, int(layout["description"].get("font_size", 30)), key="s_desc_fs")
        layout["description"]["font"] = font_selector("Font", layout["description"].get("font", ""), "s_desc_font")

    with st.sidebar.expander("Lyrics", expanded=False):
        layout["lyrics"]["visible"] = st.checkbox("Visible", layout["lyrics"].get("visible", True), key="s_lyrics_v")
        ly_y_val = layout["lyrics"].get("y", "album_bottom")
        if ly_y_val == "album_bottom":
            art_y = layout["album_art"].get("y", 200)
            art_size = int(1080 * layout["album_art"].get("size", 0.7))
            ly_y_val = art_y + art_size - 80

        layout["lyrics"]["y"] = st.slider("Y Position", 0, 2000, int(ly_y_val), key="s_lyrics_y")
        layout["lyrics"]["font_size"] = st.number_input("Font Size", 10, 100, int(layout["lyrics"].get("font_size", 28)), key="s_lyrics_fs")
        layout["lyrics"]["opacity"] = st.slider("BG Opacity", 0.0, 1.0, float(layout["lyrics"].get("opacity", 0.4)), key="s_lyrics_op")

    with st.sidebar.expander("Progress Bar", expanded=False):
        layout["progress_bar"]["visible"] = st.checkbox("Visible", layout["progress_bar"].get("visible", True), key="s_progress_v")
        layout["progress_bar"]["y"] = st.slider("Y Position", 0, 2000, int(layout["progress_bar"].get("y", 0.68)), key="s_progress_y")

    st.header(f"Playlist: {data.get('playlist_name', 'Unknown')}")

    # Global Actions - Two Save Buttons
    col_layout, col_tracks, col_clear = st.columns([1, 1, 1])

    with col_layout:
        if st.button("🎨 保存布局 (Save Layout)", help="保存布局配置到 project.json 和 default_layout.json"):
            # Save layout to both project.json and default_layout.json
            save_data(active_file, data)
            save_data("project.json", data)
            # Also update default layout template
            default_layout_path = os.path.join(root_dir, 'config', 'default_layout.json')
            try:
                os.makedirs(os.path.dirname(default_layout_path), exist_ok=True)
                with open(default_layout_path, 'w', encoding='utf-8') as f:
                    json.dump(data.get('layout', {}), f, indent=2, ensure_ascii=False)
                st.success("✅ 布局已保存（包括默认模板）")
            except Exception as e:
                st.warning(f"布局已保存，但默认模板更新失败: {e}")

    with col_tracks:
        if st.button("🎵 保存曲目 (Save Tracks)", help="仅保存曲目调整到 project.json"):
            # Save only track data to project.json
            save_data(active_file, data)
            save_data("project.json", data)
            st.success("✅ 曲目调整已保存")

    with col_clear:
        if st.button("🗑️ Clear Preview Cache"):
            import glob
            for f in glob.glob("output/preview_*.png"):
                try: os.remove(f)
                except: pass
            st.success("Cache cleared")


    # List Tracks
    tracks = data.get('tracks', [])
    for i, track in enumerate(tracks):
        with st.expander(f"{i+1}. {track['name']} - {track['artists'][0]}", expanded=(i==0)):
            col1, col2 = st.columns([1, 2])

            with col1:
                st.image(track['cover_url'], width=150)
                if st.button(f"Remove Track #{i+1}", key=f"del_{i}"):
                    tracks.pop(i)
                    st.rerun()

            with col2:
                # Time Editor
                duration_s = track['duration_ms'] / 1000.0
                start = track.get('highlight_start_ms', 0) / 1000.0
                end = track.get('highlight_end_ms', 30000) / 1000.0

                s_range = st.slider(
                    "Clip Range (seconds)",
                    0.0, duration_s, (start, end),
                    key=f"range_{i}"
                )

                # Audio Preview
                audio_url = track.get('audio_url')
                local_audio = track.get('local_path')

                # Prioritize local file if exists
                if local_audio and os.path.exists(local_audio):
                    st.caption("Playing from local file")
                    st.audio(local_audio, start_time=int(s_range[0]))
                elif audio_url:
                    st.caption("Playing from URL")
                    st.audio(audio_url, start_time=int(s_range[0]))
                else:
                    st.warning("No Preview available")

                # Update data
                track['highlight_start_ms'] = int(s_range[0] * 1000)
                track['highlight_end_ms'] = int(s_range[1] * 1000)

                # Text Editor
                col_text, col_btn = st.columns([4, 1])
                with col_text:
                    desc = st.text_area(
                        "Description / Copy",
                        value=track.get('description', ''),
                        height=100,
                        key=f"desc_{i}"
                    )
                    track['description'] = desc

                with col_btn:
                     if st.button("Research", key=f"res_{i}"):
                         # Quick Research
                         from research_utils import search_song_info, generate_ai_copy
                         ctx = search_song_info(track['name'], track['artists'][0])
                         new_desc = generate_ai_copy(track['name'], track['artists'][0], ctx)
                         track['research_context'] = ctx
                         track['description'] = new_desc
                         st.rerun()

    st.divider()
    st.caption("NetEase Weekly Clipper v2.1")

if __name__ == "__main__":
    main()
