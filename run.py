import sys
import os
import json
import subprocess
import argparse
import shutil
import time

DEFAULT_VOICE_REF_CANDIDATES = [
    os.getenv("NETEASE_WEEKLY_VOICE_REF"),
    os.getenv("QWEN3TTS_VOICE_REF"),
    os.path.join("output", "qwen3tts", "voice_assets", "self_voice"),
    os.path.join("assets", "voice", "self_voice"),
]


def resolve_project_file(project_file=None):
    input_file = project_file or "project.json"
    if not project_file:
        if os.path.exists("researched_playlist.json"):
            input_file = "researched_playlist.json"
        elif os.path.exists("raw_playlist.json"):
            input_file = "raw_playlist.json"
    return input_file


def normalize_voice_ref_path(candidate):
    if not candidate:
        return None
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(candidate)))
    if os.path.isfile(path) and os.path.basename(path).lower() == "manifest.json":
        path = os.path.dirname(path)
    manifest_path = os.path.join(path, "manifest.json")
    if os.path.isdir(path) and os.path.exists(manifest_path):
        return path
    if os.path.isfile(path):
        return path
    return None


def resolve_default_voice_ref_path(voice_ref_path=None):
    ordered_candidates = [voice_ref_path] + DEFAULT_VOICE_REF_CANDIDATES
    for candidate in ordered_candidates:
        normalized = normalize_voice_ref_path(candidate)
        if normalized and os.path.exists(normalized):
            return normalized
    return None

def run_crawler(url, output_path=None):
    print(f"--- Running Crawler for {url} ---")
    cmd = [sys.executable, "src/crawler/crawler.py", url]
    if output_path:
        cmd.append(output_path)
    return subprocess.run(cmd).returncode == 0

def run_research_prepare(input_path="raw_playlist.json", output_path="researched_playlist.json"):
    print("--- Running Research Prepare ---")
    cmd = [sys.executable, "src/researcher/researcher.py", "prepare", input_path, output_path]
    subprocess.run(cmd, check=True)
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Research output was not created: {output_path}")
    return output_path

def run_chorus_candidates(
    input_path="researched_playlist.json",
    output_path="researched_playlist.json",
    clip_length_sec=15.0,
    track_index=None,
    track_id=None,
    overwrite=False,
):
    print("--- Running pychorus Candidate Scan ---")
    cmd = [sys.executable, "src/crawler/chorus.py", input_path, output_path, "--clip-length", str(clip_length_sec)]
    if track_index is not None:
        cmd.extend(["--track-index", str(track_index)])
    if track_id is not None:
        cmd.extend(["--track-id", str(track_id)])
    if overwrite:
        cmd.append("--overwrite")
    subprocess.run(cmd, check=True)
    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Chorus output was not created: {output_path}")
    return output_path

def run_research_radio(
    input_path="researched_playlist.json",
    output_path="researched_playlist.json",
    voice_ref_path=None,
    voice_ref_text=None,
    track_index=None,
    track_id=None,
    force_tts=False,
):
    print("--- Running Radio Research ---")
    cmd = [sys.executable, "src/researcher/researcher.py", "radio", input_path, output_path]
    if voice_ref_path:
        cmd.extend(["--voice-ref", voice_ref_path])
    if voice_ref_text:
        cmd.extend(["--voice-ref-text", voice_ref_text])
    if track_index is not None:
        cmd.extend(["--track-index", str(track_index)])
    if track_id is not None:
        cmd.extend(["--track-id", str(track_id)])
    if force_tts:
        cmd.append("--force-tts")
    # researcher.py is a dedicated subprocess, so it is safe for the per-track
    # watchdog to hard-exit if a native PyTorch/MPS generation call hangs.
    tts_env = {**os.environ, "TTS_HARD_TIMEOUT": "1"}
    subprocess.run(cmd, check=True, env=tts_env)

def run_editor():
    print("--- Launching Editor ---")
    cmd = [sys.executable, "-m", "streamlit", "run", "src/editor/app.py"]
    subprocess.run(cmd)

def run_intro(project_file=None, voice_path=None, bgm_path=None, output_path=None):
    print("--- Running Intro Renderer ---")

    input_file = resolve_project_file(project_file)

    # Pass input file to script to avoid file lock issues
    cmd = [sys.executable, "src/renderer/intro_renderer.py", input_file]
    if voice_path:
        cmd.extend(["--voice-path", voice_path])
    if bgm_path:
        cmd.extend(["--bgm-path", bgm_path])
    if output_path:
        cmd.extend(["--output-path", output_path])
    target_path = output_path or os.path.join("output", "intro.mp4")
    subprocess.run(cmd, check=True)
    if not os.path.isfile(target_path) or os.path.getsize(target_path) == 0:
        raise RuntimeError(f"Intro video was not created: {target_path}")
    return target_path

def escape_markdown_cell(value):
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")

def run_song_info_export(project_file=None, output_path=None):
    print("--- Exporting Song Info Document ---")

    input_file = resolve_project_file(project_file)
    if not os.path.exists(input_file):
        print(f"Error: project file not found: {input_file}")
        return None

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        playlist_id = data.get("playlist_id") or os.path.splitext(os.path.basename(input_file))[0]
        playlist_name = data.get("playlist_name") or playlist_id
        if not output_path:
            output_path = os.path.join("output", f"{playlist_id}_song_info.md")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        lines = [
            "# 歌曲信息",
            "",
            f"- 歌单：{escape_markdown_cell(playlist_name)}",
            f"- 歌单 ID：{escape_markdown_cell(playlist_id)}",
            f"- 来源文件：`{input_file}`",
            "",
            "| 序号 | 歌曲名 | 歌手名 | 专辑名 | 专辑类型 |",
            "| --- | --- | --- | --- | --- |",
        ]

        for index, track in enumerate(data.get("tracks", []), start=1):
            artists = track.get("artists") or []
            if isinstance(artists, list):
                artist_text = " / ".join(str(item) for item in artists)
            else:
                artist_text = str(artists)
            album_title = track.get("release_title") or track.get("album") or ""
            album_type = track.get("normalized_release_type") or track.get("album_type") or ""
            lines.append(
                "| {index} | {name} | {artist} | {album} | {album_type} |".format(
                    index=index,
                    name=escape_markdown_cell(track.get("name", "")),
                    artist=escape_markdown_cell(artist_text),
                    album=escape_markdown_cell(album_title),
                    album_type=escape_markdown_cell(album_type),
                )
            )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print(f"[*] Song info document saved to {output_path}")
        return output_path
    except Exception as e:
        print(f"Error exporting song info document: {e}")
        return None

def get_release_artist_text(track):
    artists = track.get("artists") or []
    if isinstance(artists, list):
        return " / ".join(str(item) for item in artists)
    return str(artists)

def get_release_type(track):
    return track.get("normalized_release_type") or track.get("album_type") or "未知"

def get_release_title(track):
    return track.get("release_title") or track.get("album") or track.get("name") or ""

def format_release_percent(count, total):
    if not total:
        return "0%"
    value = count * 100 / total
    if abs(value - round(value)) < 0.05:
        return f"{round(value)}%"
    return f"{value:.1f}%"

def run_release_report_export(project_file=None, song_info_path=None, output_path=None):
    print("--- Exporting Weekly Release Report ---")

    input_file = resolve_project_file(project_file)
    if not os.path.exists(input_file):
        print(f"Error: project file not found: {input_file}")
        return None

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        playlist_id = data.get("playlist_id") or os.path.splitext(os.path.basename(input_file))[0]
        playlist_name = data.get("playlist_name") or "本周"
        if not output_path:
            output_path = os.path.join("output", f"{playlist_id}_release_report.md")

        tracks = data.get("tracks", [])
        total = len(tracks)
        counts = {}
        for track in tracks:
            release_type = get_release_type(track)
            counts[release_type] = counts.get(release_type, 0) + 1

        album_count = counts.get("专辑", 0)
        ep_count = counts.get("EP", 0)
        live_count = counts.get("现场", 0)
        single_count = counts.get("单曲", 0)
        album_percent = format_release_percent(album_count, total)
        single_percent = format_release_percent(single_count, total)

        if album_count > single_count:
            headline_hook = "全长专辑集中释放"
            trend_sentence = f"专辑成为本周最突出的发行规格，占比约 {album_percent}（共 {album_count} 张），发行重心明显向长碟内容倾斜。"
        elif album_count == single_count and album_count > 0:
            headline_hook = "专辑与单曲并行，长碟产出保持高位"
            trend_sentence = f"专辑与单曲数量并列，二者各占约 {album_percent}（各 {album_count} 组），说明本周既有即时单曲更新，也有完整作品集中释出。"
        else:
            headline_hook = "单曲仍然活跃，专辑产出同步抬升"
            trend_sentence = f"单曲占比约 {single_percent}（共 {single_count} 首），仍是主要发行形态；专辑占比约 {album_percent}（共 {album_count} 张），长碟输出也保持存在感。"

        classic_names = []
        for name in ["阿修罗乐队", "马帮乐队", "鲸鱼马戏团", "TONICK"]:
            if any(name in get_release_artist_text(track) for track in tracks):
                classic_names.append(name)
        indie_names = []
        for name in ["黑甜一枕", "春日悬浮", "Trickymon脆可梦", "电气樱桃", "出入平安", "深度睡眠乐队", "幻鸦"]:
            if any(name in get_release_artist_text(track) for track in tracks):
                indie_names.append(name)

        type_order = {"专辑": 0, "EP": 1, "现场": 2, "单曲": 3}
        sorted_tracks = sorted(
            tracks,
            key=lambda item: (
                type_order.get(get_release_type(item), 99),
                get_release_artist_text(item),
                get_release_title(item),
            ),
        )

        lines = [
            "# 本周发行情况",
            "",
            f"- 统计周期：{escape_markdown_cell(playlist_name)}",
            f"- 数据来源：`{song_info_path or input_file}`",
            "",
            "## 核心标题 (Headline)",
            "",
            f"本周（{playlist_name}）华语摇滚{headline_hook}。本期共收录 {total} 组发行，其中专辑 {album_count} 张、EP {ep_count} 张、现场 {live_count} 张、单曲 {single_count} 首。",
            "",
            "## 趋势综述 (Market Trend)",
            "",
            trend_sentence,
            f"从结构上看，专辑占比约 {album_percent}，单曲占比约 {single_percent}，EP 与现场录音共同补充了本周的发行层次；相较常见的新歌单曲驱动节奏，本周完整作品的比重更值得关注。",
            "",
            "## 重点推介 (Highlights)",
            "",
            f"经典回归：{('、'.join(classic_names) if classic_names else '本周未识别出明显老牌回归条目')}等成熟力量继续贡献发行动作。",
            f"新锐活跃：{('、'.join(indie_names) if indie_names else '本周独立新锐发行较为分散')}等独立/新锐乐队保持输出频率。",
            "",
            "## 发行详情清单 (Release Details)",
            "",
            "🔴本周发行详情：",
        ]

        for track in sorted_tracks:
            lines.append(
                f"·{get_release_artist_text(track)}发行[{get_release_type(track)}] 《{get_release_title(track)}》"
            )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print(f"[*] Weekly release report saved to {output_path}")
        return output_path
    except Exception as e:
        print(f"Error exporting weekly release report: {e}")
        return None

def run_publish_copy_export(project_file=None, artifact_dir=None, output_auto=None, output_editable=None):
    print("--- Exporting Publish Copy Documents ---")

    input_file = resolve_project_file(project_file)
    if not os.path.exists(input_file):
        print(f"Error: project file not found: {input_file}")
        return None

    try:
        from song_discovery.publish_copy import generate_publish_copy, write_publish_copy_artifacts

        copy_text = generate_publish_copy(input_file)
        target_dir = artifact_dir or os.path.dirname(output_auto or output_editable or input_file) or "output"
        os.makedirs(target_dir, exist_ok=True)

        auto_path, editable_path = write_publish_copy_artifacts(target_dir, copy_text)
        print(f"[*] Publish copy auto draft saved to {auto_path}")
        print(f"[*] Publish copy editable draft saved to {editable_path}")
        return str(editable_path)
    except Exception as e:
        print(f"Error exporting publish copy documents: {e}")
        return None

def run_renderer(project_file=None, output_path=None, intro_path=None):
    print("--- Running Core Renderer ---")

    input_file = resolve_project_file(project_file)

    print(f"Using input file: {input_file}")

    cmd = [sys.executable, "src/renderer/renderer.py", input_file]
    target_path = output_path or os.path.join("output", "netease_weekly_final_compilation.mp4")
    if output_path:
        cmd.extend(["--output-path", output_path])
    if intro_path:
        cmd.extend(["--intro-path", intro_path])
    subprocess.run(cmd, check=True)
    if not os.path.isfile(target_path) or os.path.getsize(target_path) == 0:
        raise RuntimeError(f"Final video was not created: {target_path}")
    return target_path

def run_track_demo(project_file="researched_playlist.json", track_index=0, output_path=None, segment_source="preferred"):
    print("--- Running Track Demo Renderer ---")
    cmd = [sys.executable, "src/renderer/renderer.py", project_file, "--track-demo", "--track-index", str(track_index)]
    if output_path:
        cmd.extend(["--output-path", output_path])
    if segment_source:
        cmd.extend(["--segment-source", segment_source])
    subprocess.run(cmd)

def run_extractor(input_file="raw_playlist.json", output_file="simplified_playlist.json"):
    print("--- Extracting Simplified Info ---")

    if os.path.exists(input_file):
        try:
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
        except Exception as e:
            print(f"Error extracting info: {e}")
    else:
        print(f"Error: {input_file} not found.")

def _artifact_paths(artifact_dir, prepared_path):
    if not artifact_dir:
        return {
            "intro": os.path.join("output", "intro.mp4"),
            "song_info": None,
            "release_report": None,
            "publish_copy_auto": None,
            "publish_copy": None,
            "final_video": os.path.join("output", "netease_weekly_final_compilation.mp4"),
        }
    os.makedirs(artifact_dir, exist_ok=True)
    return {
        "intro": os.path.join(artifact_dir, "intro.mp4"),
        "song_info": os.path.join(artifact_dir, "song_info.md"),
        "release_report": os.path.join(artifact_dir, "release_report.md"),
        "publish_copy_auto": os.path.join(artifact_dir, "publish_copy.auto.md"),
        "publish_copy": os.path.join(artifact_dir, "publish_copy.md"),
        "final_video": os.path.join(artifact_dir, "final_video.mp4"),
    }


def run_pipeline_from_raw(raw_path="raw_playlist.json", prepared_path="researched_playlist.json", voice_ref_path=None, voice_ref_text=None, force_tts=False, artifact_dir=None):
    print(f"\n{'='*50}")
    print(f"🚀 RUNNING PIPELINE FROM RAW PLAYLIST: {raw_path}")
    print(f"{'='*50}\n")

    if not os.path.exists(raw_path):
        print(f"[!] Error: Raw playlist file not found: {raw_path}")
        return False

    # 1.5 Extract simplified info
    run_extractor(raw_path, os.path.join(os.path.dirname(raw_path) or ".", "simplified_playlist.json"))

    # 2. Research prepare
    run_research_prepare(input_path=raw_path, output_path=prepared_path)

    # 2.5 Chorus candidates (preferred, fallback-safe)
    run_chorus_candidates(
        input_path=prepared_path,
        output_path=prepared_path,
        clip_length_sec=15.0,
    )

    # 3. Radio copy + TTS
    resolved_voice_ref_path = resolve_default_voice_ref_path(voice_ref_path)
    if resolved_voice_ref_path:
        print(f"[*] Using radio voice reference: {resolved_voice_ref_path}")
        run_research_radio(
            input_path=prepared_path,
            output_path=prepared_path,
            voice_ref_path=resolved_voice_ref_path,
            voice_ref_text=voice_ref_text,
            force_tts=force_tts,
        )
    else:
        print("[!] No voice reference asset found. Skipping radio TTS generation.")
        if artifact_dir:
            raise RuntimeError("No voice reference asset found for managed video workflow")

    artifacts = _artifact_paths(artifact_dir, prepared_path)

    # 4. Intro
    run_intro(project_file=prepared_path, output_path=artifacts["intro"])

    # 5. Export song info and weekly release report
    song_info_path = run_song_info_export(prepared_path, output_path=artifacts["song_info"])
    if not song_info_path:
        raise RuntimeError("Song info export failed")
    report_path = run_release_report_export(prepared_path, song_info_path=song_info_path, output_path=artifacts["release_report"])
    if not report_path:
        raise RuntimeError("Release report export failed")
    publish_copy_path = run_publish_copy_export(
        prepared_path,
        artifact_dir=artifact_dir,
        output_auto=artifacts["publish_copy_auto"],
        output_editable=artifacts["publish_copy"],
    )
    if not publish_copy_path:
        raise RuntimeError("Publish copy export failed")

    # 6. Render
    run_renderer(prepared_path, output_path=artifacts["final_video"], intro_path=artifacts["intro"])
    return True


def run_pipeline_from_researched(prepared_path="researched_playlist.json", voice_ref_path=None, voice_ref_text=None, force_tts=False, artifact_dir=None):
    """Resume after research preparation without crawling or rebuilding research data."""
    print(f"\n{'='*50}")
    print(f"RESUMING PIPELINE FROM RESEARCHED PLAYLIST: {prepared_path}")
    print(f"{'='*50}\n")

    if not os.path.exists(prepared_path):
        print(f"[!] Error: Researched playlist file not found: {prepared_path}")
        return False

    resolved_voice_ref_path = resolve_default_voice_ref_path(voice_ref_path)
    if not resolved_voice_ref_path:
        print("[!] No voice reference asset found. Cannot resume TTS generation.")
        return False

    print(f"[*] Using radio voice reference: {resolved_voice_ref_path}")
    run_research_radio(
        input_path=prepared_path,
        output_path=prepared_path,
        voice_ref_path=resolved_voice_ref_path,
        voice_ref_text=voice_ref_text,
        force_tts=force_tts,
    )
    artifacts = _artifact_paths(artifact_dir, prepared_path)
    run_intro(project_file=prepared_path, output_path=artifacts["intro"])
    song_info_path = run_song_info_export(prepared_path, output_path=artifacts["song_info"])
    if not song_info_path:
        raise RuntimeError("Song info export failed")
    report_path = run_release_report_export(prepared_path, song_info_path=song_info_path, output_path=artifacts["release_report"])
    if not report_path:
        raise RuntimeError("Release report export failed")
    publish_copy_path = run_publish_copy_export(
        prepared_path,
        artifact_dir=artifact_dir,
        output_auto=artifacts["publish_copy_auto"],
        output_editable=artifacts["publish_copy"],
    )
    if not publish_copy_path:
        raise RuntimeError("Publish copy export failed")
    run_renderer(prepared_path, output_path=artifacts["final_video"], intro_path=artifacts["intro"])
    return True

def run_full_pipeline(url, voice_ref_path=None, voice_ref_text=None, force_tts=False, raw_path="raw_playlist.json", prepared_path="researched_playlist.json"):
    print(f"\n{'='*50}")
    print(f"🚀 STARTING FULL PIPELINE")
    print(f"{'='*50}\n")

    start_time = time.time()
    # 1. Crawl
    if not run_crawler(url, output_path=raw_path):
        print("[!] Crawler failed. Stopping full pipeline to avoid rendering stale playlist data.")
        return

    run_pipeline_from_raw(
        raw_path=raw_path,
        prepared_path=prepared_path,
        voice_ref_path=voice_ref_path,
        voice_ref_text=voice_ref_text,
        force_tts=force_tts,
    )

    end_time = time.time()
    duration = end_time - start_time

    print(f"\n{'='*50}")
    print(f"✅ FULL PIPELINE COMPLETED IN {duration:.2f}s")
    print(f"{'='*50}\n")

def main():
    parser = argparse.ArgumentParser(description="NetEase Music Weekly Clipper Orchestrator")
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Crawl
    crawl_parser = subparsers.add_parser("crawl", help="Crawl playlist data")
    crawl_parser.add_argument("url", nargs="?", default="https://music.163.com/playlist?id=17684977343", help="Playlist URL")
    crawl_parser.add_argument("output_path", nargs="?", default=None, help="Optional output JSON path")

    # Research
    prepare_parser = subparsers.add_parser("research_prepare", help="Prepare structured release facts")
    prepare_parser.add_argument("input_path", nargs="?", default="raw_playlist.json")
    prepare_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")

    chorus_parser = subparsers.add_parser("chorus_candidates", help="Generate pychorus candidate windows")
    chorus_parser.add_argument("input_path", nargs="?", default="researched_playlist.json")
    chorus_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")
    chorus_parser.add_argument("--clip-length", dest="clip_length_sec", type=float, default=15.0)
    chorus_parser.add_argument("--track-index", dest="track_index", type=int, default=None)
    chorus_parser.add_argument("--track-id", dest="track_id", type=int, default=None)
    chorus_parser.add_argument("--overwrite", action="store_true")

    radio_parser = subparsers.add_parser("research_radio", help="Generate radio copy and optional TTS")
    radio_parser.add_argument("input_path", nargs="?", default="researched_playlist.json")
    radio_parser.add_argument("output_path", nargs="?", default="researched_playlist.json")
    radio_parser.add_argument("--voice-ref", dest="voice_ref_path", default=None)
    radio_parser.add_argument("--voice-ref-text", dest="voice_ref_text", default=None)
    radio_parser.add_argument("--track-index", dest="track_index", type=int, default=None)
    radio_parser.add_argument("--track-id", dest="track_id", type=int, default=None)
    radio_parser.add_argument("--force-tts", action="store_true")

    # Edit
    subparsers.add_parser("edit", help="Launch visual editor")

    # Intro
    intro_parser = subparsers.add_parser("intro", help="Render intro video")
    intro_parser.add_argument("project_file", nargs="?", default=None)
    intro_parser.add_argument("--voice-path", dest="voice_path", default=None)
    intro_parser.add_argument("--bgm-path", dest="bgm_path", default=None)
    intro_parser.add_argument("--output-path", dest="output_path", default=None)

    # Render
    render_parser = subparsers.add_parser("render", help="Render final compilation")
    render_parser.add_argument("project_file", nargs="?", default=None)

    song_info_parser = subparsers.add_parser("song_info", help="Export a simple song info document")
    song_info_parser.add_argument("project_file", nargs="?", default=None)
    song_info_parser.add_argument("--output-path", dest="output_path", default=None)

    release_report_parser = subparsers.add_parser("release_report", help="Export a weekly release summary document")
    release_report_parser.add_argument("project_file", nargs="?", default=None)
    release_report_parser.add_argument("--song-info-path", dest="song_info_path", default=None)
    release_report_parser.add_argument("--output-path", dest="output_path", default=None)

    publish_copy_parser = subparsers.add_parser("publish_copy", help="Export publish copy documents (auto and editable drafts)")
    publish_copy_parser.add_argument("project_file", nargs="?", default=None)
    publish_copy_parser.add_argument("--artifact-dir", dest="artifact_dir", default=None)

    track_demo_parser = subparsers.add_parser("track_demo", help="Render a single track demo")
    track_demo_parser.add_argument("project_file", nargs="?", default="researched_playlist.json")
    track_demo_parser.add_argument("--track-index", dest="track_index", type=int, default=0)
    track_demo_parser.add_argument("--output-path", dest="output_path", default=None)
    track_demo_parser.add_argument("--segment-source", dest="segment_source", default="preferred")

    # Extract
    subparsers.add_parser("extract", help="Extract simplified playlist info")

    # Full
    full_parser = subparsers.add_parser("full", help="Run full pipeline (crawl -> research -> intro -> render)")
    full_parser.add_argument("url", nargs="?", help="Playlist URL")
    full_parser.add_argument("--voice-ref", dest="voice_ref_path", default=None)
    full_parser.add_argument("--voice-ref-text", dest="voice_ref_text", default=None)
    full_parser.add_argument("--force-tts", action="store_true")
    full_parser.add_argument("--raw-output", dest="raw_output", default="raw_playlist.json")
    full_parser.add_argument("--project-output", dest="project_output", default="researched_playlist.json")

    # From Raw (skip crawl)
    from_raw_parser = subparsers.add_parser("from_raw", help="Run pipeline from existing raw_playlist.json")
    from_raw_parser.add_argument("--raw-path", dest="raw_path", default="raw_playlist.json")
    from_raw_parser.add_argument("--project-output", dest="project_output", default="researched_playlist.json")
    from_raw_parser.add_argument("--voice-ref", dest="voice_ref_path", default=None)
    from_raw_parser.add_argument("--voice-ref-text", dest="voice_ref_text", default=None)
    from_raw_parser.add_argument("--force-tts", action="store_true")
    from_raw_parser.add_argument("--artifact-dir", dest="artifact_dir", default=None)

    resume_parser = subparsers.add_parser("resume_researched", help="Resume at radio TTS from an existing researched playlist")
    resume_parser.add_argument("--project", dest="project_file", default="researched_playlist.json")
    resume_parser.add_argument("--voice-ref", dest="voice_ref_path", default=None)
    resume_parser.add_argument("--voice-ref-text", dest="voice_ref_text", default=None)
    resume_parser.add_argument("--force-tts", action="store_true")
    resume_parser.add_argument("--artifact-dir", dest="artifact_dir", default=None)

    args = parser.parse_args()

    if args.action == "crawl":
        run_crawler(getattr(args, 'url', None), getattr(args, 'output_path', None))
    elif args.action == "research_prepare":
        run_research_prepare(getattr(args, 'input_path', "raw_playlist.json"), getattr(args, 'output_path', "researched_playlist.json"))
    elif args.action == "chorus_candidates":
        run_chorus_candidates(
            input_path=getattr(args, 'input_path', "researched_playlist.json"),
            output_path=getattr(args, 'output_path', "researched_playlist.json"),
            clip_length_sec=getattr(args, 'clip_length_sec', 15.0),
            track_index=getattr(args, 'track_index', None),
            track_id=getattr(args, 'track_id', None),
            overwrite=getattr(args, 'overwrite', False),
        )
    elif args.action == "research_radio":
        run_research_radio(
            input_path=getattr(args, 'input_path', "researched_playlist.json"),
            output_path=getattr(args, 'output_path', "researched_playlist.json"),
            voice_ref_path=getattr(args, 'voice_ref_path', None),
            voice_ref_text=getattr(args, 'voice_ref_text', None),
            track_index=getattr(args, 'track_index', None),
            track_id=getattr(args, 'track_id', None),
            force_tts=getattr(args, 'force_tts', False),
        )
    elif args.action == "edit":
        run_editor()
    elif args.action == "intro":
        run_intro(
            project_file=getattr(args, 'project_file', None),
            voice_path=getattr(args, 'voice_path', None),
            bgm_path=getattr(args, 'bgm_path', None),
            output_path=getattr(args, 'output_path', None),
        )
    elif args.action == "render":
        project_file = getattr(args, 'project_file', None)
        song_info_path = run_song_info_export(project_file)
        run_release_report_export(project_file, song_info_path=song_info_path)
        run_publish_copy_export(project_file)
        run_renderer(project_file)
    elif args.action == "song_info":
        run_song_info_export(
            project_file=getattr(args, 'project_file', None),
            output_path=getattr(args, 'output_path', None),
        )
    elif args.action == "release_report":
        run_release_report_export(
            project_file=getattr(args, 'project_file', None),
            song_info_path=getattr(args, 'song_info_path', None),
            output_path=getattr(args, 'output_path', None),
        )
    elif args.action == "publish_copy":
        run_publish_copy_export(
            project_file=getattr(args, 'project_file', None),
            artifact_dir=getattr(args, 'artifact_dir', None),
        )
    elif args.action == "track_demo":
        run_track_demo(
            project_file=getattr(args, 'project_file', "researched_playlist.json"),
            track_index=getattr(args, 'track_index', 0),
            output_path=getattr(args, 'output_path', None),
            segment_source=getattr(args, 'segment_source', "highlight"),
        )
    elif args.action == "extract":
        run_extractor()
    elif args.action == "full":
        url = getattr(args, 'url', None)
        if not url:
            print("\n" + "="*40)
            print("   NetEase Weekly Clipper - 一键生成")
            print("="*40)
            url = input("\n请输入网易云音乐歌单链接: ")
        run_full_pipeline(
            url,
            voice_ref_path=getattr(args, 'voice_ref_path', None),
            voice_ref_text=getattr(args, 'voice_ref_text', None),
            force_tts=getattr(args, 'force_tts', False),
            raw_path=getattr(args, 'raw_output', "raw_playlist.json"),
            prepared_path=getattr(args, 'project_output', "researched_playlist.json"),
        )
    elif args.action == "from_raw":
        run_pipeline_from_raw(
            raw_path=getattr(args, 'raw_path', 'raw_playlist.json'),
            prepared_path=getattr(args, 'project_output', 'researched_playlist.json'),
            voice_ref_path=getattr(args, 'voice_ref_path', None),
            voice_ref_text=getattr(args, 'voice_ref_text', None),
            force_tts=getattr(args, 'force_tts', False),
            artifact_dir=getattr(args, 'artifact_dir', None),
        )
    elif args.action == "resume_researched":
        if not run_pipeline_from_researched(
            prepared_path=getattr(args, 'project_file', 'researched_playlist.json'),
            voice_ref_path=getattr(args, 'voice_ref_path', None),
            voice_ref_text=getattr(args, 'voice_ref_text', None),
            force_tts=getattr(args, 'force_tts', False),
            artifact_dir=getattr(args, 'artifact_dir', None),
        ):
            raise SystemExit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
