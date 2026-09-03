"""Validation runner that collects and validates releases from all supported platforms."""

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from song_discovery.collectors.kkbox import KKBOXCollector
from song_discovery.collectors.netease import NetEaseCollector
from song_discovery.collectors.qq import QQMusicCollector
from song_discovery.exceptions import AuthenticationError, ServiceUnavailableError
from song_discovery.models import Platform, Release, Track
from song_discovery.selection import get_selection_rule_label, select_track_from_release


class ValidationRunner:
    """Runs data collection and generates verification summary files."""

    def __init__(
        self,
        output_dir: Optional[str] = None,
        limit: int = 5,
        netease_url: Optional[str] = None,
    ):
        if output_dir:
            self.output_dir = output_dir
        else:
            self.output_dir = os.path.join(tempfile.gettempdir(), "song_discovery_validation")
        self.limit = limit
        self.netease_url = netease_url
        os.makedirs(self.output_dir, exist_ok=True)

    def _process_releases(self, platform_name: str, releases: List[Release]) -> Dict[str, Any]:
        items = []
        for rel in releases:
            selected_track: Optional[Track] = None
            selection_rule = ""
            try:
                selected_track = select_track_from_release(rel)
                selection_rule = get_selection_rule_label(rel)
            except Exception as exc:
                selection_rule = f"failed: {exc}"

            items.append({
                "release": rel.to_dict(include_raw=False),
                "selected_track": selected_track.to_dict(include_raw=False) if selected_track else None,
                "selection_rule": selection_rule,
            })

        out_path = os.path.join(self.output_dir, f"{platform_name}_releases.json")
        res = {
            "platform": platform_name,
            "status": "success",
            "releases_count": len(releases),
            "items": items,
            "saved_to": out_path,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        return res

    def run_qq(self, limit: Optional[int] = None) -> Dict[str, Any]:
        lmt = limit if limit is not None else self.limit
        collector = QQMusicCollector()
        try:
            releases = collector.collect_new_releases(limit=lmt)
            return self._process_releases(Platform.QQ.value, releases)
        except Exception as exc:
            res = {
                "platform": Platform.QQ.value,
                "status": "failed",
                "reason": str(exc),
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "qq_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res

    def run_netease(self, limit: Optional[int] = None, base_url: Optional[str] = None) -> Dict[str, Any]:
        lmt = limit if limit is not None else self.limit
        b_url = base_url or self.netease_url
        collector = NetEaseCollector(base_url=b_url)
        try:
            releases = collector.collect_new_releases(limit=lmt)
            res = self._process_releases(Platform.NETEASE.value, releases)
            res["base_url"] = collector.base_url
            return res
        except ServiceUnavailableError as exc:
            res = {
                "platform": Platform.NETEASE.value,
                "status": "failed",
                "reason": f"Service unavailable at {collector.base_url}: {exc}",
                "base_url": collector.base_url,
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "netease_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res
        except Exception as exc:
            res = {
                "platform": Platform.NETEASE.value,
                "status": "failed",
                "reason": str(exc),
                "base_url": collector.base_url,
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "netease_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res

    def run_kkbox(self, limit: Optional[int] = None) -> Dict[str, Any]:
        lmt = limit if limit is not None else self.limit
        collector = KKBOXCollector()
        if not collector.has_credentials:
            res = {
                "platform": Platform.KKBOX.value,
                "status": "skipped",
                "source_mode": "awaiting_extension_bridge",
                "reason": "Missing KKBOX_CLIENT_ID or KKBOX_CLIENT_SECRET environment variables. Use Chrome extension + bridge (127.0.0.1:8765) for web ingestion.",
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "kkbox_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res

        try:
            releases = collector.collect_new_releases(limit=lmt)
            res = self._process_releases(Platform.KKBOX.value, releases)
            res["source_mode"] = collector.source_mode
            return res
        except AuthenticationError as exc:
            res = {
                "platform": Platform.KKBOX.value,
                "status": "skipped",
                "source_mode": "awaiting_extension_bridge",
                "reason": f"Authentication skipped or failed: {exc}",
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "kkbox_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res
        except Exception as exc:
            res = {
                "platform": Platform.KKBOX.value,
                "status": "failed",
                "source_mode": collector.source_mode,
                "reason": str(exc),
                "releases_count": 0,
                "items": [],
            }
            out_path = os.path.join(self.output_dir, "kkbox_releases.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            return res

    def execute(self, platform: str = "all") -> Dict[str, Any]:
        target_platforms = (
            [Platform.QQ.value, Platform.NETEASE.value, Platform.KKBOX.value]
            if platform == "all"
            else [platform]
        )

        results = {}
        for p in target_platforms:
            if p == Platform.QQ.value:
                results[p] = self.run_qq()
            elif p == Platform.NETEASE.value:
                results[p] = self.run_netease()
            elif p == Platform.KKBOX.value:
                results[p] = self.run_kkbox()

        summary = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "platforms": results,
        }

        # Write summary JSON
        summary_json = os.path.join(self.output_dir, "summary.json")
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # Write summary Markdown
        summary_md = os.path.join(self.output_dir, "summary.md")
        with open(summary_md, "w", encoding="utf-8") as f:
            f.write("# Song Discovery Validation Summary\n\n")
            f.write(f"- Generated At: {summary['timestamp']}\n\n")
            for p_name, p_data in results.items():
                f.write(f"## Platform: {p_name.upper()}\n")
                f.write(f"- Status: {p_data.get('status')}\n")
                f.write(f"- Mode: {p_data.get('source_mode', 'default')}\n")
                f.write(f"- Releases Count: {p_data.get('releases_count', 0)}\n")
                if p_data.get("reason"):
                    f.write(f"- Note: {p_data['reason']}\n")
                f.write("\n")

        return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-platform Song Discovery Validator CLI")
    parser.add_argument("--platform", choices=["qq", "netease", "kkbox", "all"], default="all", help="Target platform (default: all)")
    parser.add_argument("--limit", type=int, default=5, help="Number of releases to fetch per category/area (default: 5)")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to save validation JSON files")
    parser.add_argument("--netease-url", type=str, default=None, help="Base URL for local NetEase API")

    args = parser.parse_args()
    runner = ValidationRunner(output_dir=args.output_dir, limit=args.limit, netease_url=args.netease_url)
    runner.execute(platform=args.platform)


if __name__ == "__main__":
    main()
