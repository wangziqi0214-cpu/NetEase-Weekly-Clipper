"""Command Line Interface for Song Discovery, Publisher, KKBOX Bridge & Always-on Daemon."""

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

from song_discovery.bridge import KKBOXBridgeServer, global_command_manager
from song_discovery.cookie_loader import NetEaseAuth
from song_discovery.db import DiscoveryDB
from song_discovery.netease_service import NetEaseServiceManager
from song_discovery.orchestrator import DiscoveryOrchestrator
from song_discovery.models import REJECTION_REASON_LABELS
from song_discovery.preference_learner import PreferenceLearner
from song_discovery.publisher import NetEasePublisher
from song_discovery.review_helpers import (
    get_platforms_overview,
    schedule_refresh_command,
    train_preference_model_from_db,
)
from song_discovery.supervisor import DiscoverySupervisor, SupervisorLockError


def cmd_collect(args: argparse.Namespace) -> None:
    svc_url = args.netease_url or "http://localhost:3000"
    auto_start = not args.no_auto_start

    if auto_start and (args.platform in ("netease", "all")):
        svc_mgr = NetEaseServiceManager(base_url=svc_url, auto_start=auto_start)
        try:
            svc_mgr.start()
        except Exception as exc:
            print(f"[SERVICE WARNING] Could not auto-start NetEase API service: {exc}")

    db = DiscoveryDB(db_path=args.db_path)
    orchestrator = DiscoveryOrchestrator(db=db)

    print("=" * 60)
    print(f"[SONG DISCOVERY] Starting collection for platform='{args.platform}'")
    print(f"Database target: {args.db_path}")
    print("=" * 60)

    summary = orchestrator.run_discovery(
        platform=args.platform,
        limit=args.limit,
        netease_url=svc_url,
        netease_pages=args.netease_pages,
        netease_page_size=args.netease_page_size,
    )

    # If platform is all or kkbox and credentials were not present, enqueue sidecar command
    if args.platform in ("all", "kkbox"):
        kkbox_info = summary.get("platforms", {}).get("kkbox", {})
        if kkbox_info.get("status") in ("skipped", "awaiting_bridge"):
            cmd = global_command_manager.enqueue_command(action="collect_kkbox")
            summary["platforms"]["kkbox"] = {
                "status": "managed_via_sidecar",
                "command_id": cmd.get("command_id"),
                "candidates_count": db.get_stats().get("platforms", {}).get("kkbox", 0),
                "message": "Enqueued collection task for KKBOX Session Sidecar.",
            }

    print("\n" + "=" * 60)
    print(f"[COLLECTION SUMMARY] Run ID: {summary.get('run_id')}")
    print(f"Total Releases Processed   : {summary.get('total_releases')}")
    print(f"Total Candidates Upserted  : {summary.get('total_candidates')}")
    print("-" * 60)
    for plat, pinfo in summary.get("platforms", {}).items():
        status = pinfo.get("status", "unknown")
        rel_cnt = pinfo.get("releases_count", 0)
        cand_cnt = pinfo.get("candidates_count", 0)
        warn_cnt = pinfo.get("warning_count", 0)
        if status == "managed_via_sidecar":
            print(f"  • {plat.upper():<8}: Status=MANAGED_VIA_SIDECAR (Enqueued for Sidecar) Candidates={cand_cnt:<4}")
        else:
            print(f"  • {plat.upper():<8}: Status={status.upper():<12} Releases={rel_cnt:<4} Candidates={cand_cnt:<4} Warnings={warn_cnt}")
        if pinfo.get("warnings"):
            for w in pinfo["warnings"]:
                print(f"      ⚠️ Warning: {w.get('album_title', 'Album')} ({w.get('album_id')}) - {w.get('error')}")
    print("=" * 60)


def cmd_status(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    state = {}
    if os.path.exists(args.state_path):
        try:
            with open(args.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    overview = get_platforms_overview(db=db, state=state)

    print("=" * 65)
    print("      NetEase Weekly Clipper - 3-Platform Data Source Status")
    print("=" * 65)
    for plat_key in ("qq", "netease", "kkbox"):
        p = overview["platforms"][plat_key]
        print(f"• {p['display_name']:<26}: {p['health']}")
        print(f"    - Candidates: {p['candidates_count']} tracks")
        print(f"    - Last Run  : {p['last_run_time']}")
        if p.get("error"):
            print(f"    - Error     : {p['error']}")
    print("-" * 65)
    print(f"Total Unique Candidates: {overview['total_candidates']} | Pending Review: {overview['pending_count']} | Approved: {overview['approved_count']}")
    print("=" * 65)


def cmd_export(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    orchestrator = DiscoveryOrchestrator(db=db)

    print(f"[EXPORT] Exporting candidates (status='{args.status}') to {args.output}...")
    res = orchestrator.export_candidates(status=args.status, output_file=args.output)
    print(f"[SUCCESS] Exported {res['count']} candidates to {res['output_file']}")


def cmd_stats(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    stats = db.get_stats()

    print("=" * 50)
    print(f"Song Discovery Database Stats ({args.db_path})")
    print("=" * 50)
    print(f"Total Releases Processed : {stats.get('total_releases', 0)}")
    print(f"Total Unique Candidates  : {stats.get('total_candidates', 0)}")
    print("-" * 50)
    print(f"  • Pending Review       : {stats.get('pending', 0)}")
    print(f"  • Approved             : {stats.get('approved', 0)}")
    print(f"  • Rejected             : {stats.get('rejected', 0)}")
    print(f"  • Deferred             : {stats.get('deferred', 0)}")
    print("-" * 50)
    print("Candidates by Platform:")
    for plat, cnt in stats.get("platforms", {}).items():
        print(f"  • {plat.upper():<8}: {cnt}")
    print("=" * 50)


def cmd_login_status(args: argparse.Namespace) -> None:
    svc_url = args.netease_url or "http://localhost:3000"
    auto_start = not args.no_auto_start

    svc_mgr = NetEaseServiceManager(base_url=svc_url, auto_start=auto_start)
    if auto_start:
        try:
            svc_mgr.start()
        except Exception as exc:
            print(f"[SERVICE WARNING] Could not auto-start NetEase API service: {exc}")

    auth = NetEaseAuth(base_url=svc_url)
    status = auth.get_login_status(cookie_file=args.cookie_file)

    print("=" * 50)
    print("NetEase Cloud Music Login Status")
    print("=" * 50)
    print(f"Logged In    : {'YES' if status.get('is_logged_in') else 'NO'}")
    if status.get("is_logged_in"):
        print(f"User ID      : {status.get('user_id')}")
        print(f"Nickname     : {status.get('nickname')}")
        print(f"VIP Type     : {status.get('vip_type')}")
    else:
        print(f"Details      : {status.get('error') or 'Session is anonymous or cookie is invalid/expired.'}")
    print("=" * 50)


def cmd_publish(args: argparse.Namespace) -> None:
    svc_url = args.netease_url or "http://localhost:3000"
    auto_start = not args.no_auto_start

    svc_mgr = NetEaseServiceManager(base_url=svc_url, auto_start=auto_start)
    if auto_start:
        try:
            svc_mgr.start()
        except Exception as exc:
            print(f"[SERVICE WARNING] Could not auto-start NetEase API service: {exc}")

    db = DiscoveryDB(db_path=args.db_path)
    publisher = NetEasePublisher(base_url=svc_url, db=db)

    print("=" * 60)
    print(f"[NETEASE PUBLISHER] Mode: {'DRY-RUN (Simulated)' if args.dry_run else 'LIVE PUBLISH'}")
    print(f"Playlist Target : '{args.playlist_name}' (ID: {args.playlist_id or 'Auto-Create'})")
    print(f"Cookie File     : {args.cookie_file}")
    print("=" * 60)

    try:
        res = publisher.publish_approved(
            playlist_name=args.playlist_name,
            playlist_id=args.playlist_id,
            cookie_file=args.cookie_file,
            dry_run=args.dry_run,
        )

        print("\n" + "=" * 60)
        print(f"[PUBLISH RESULT] Status: {res.get('status').upper()}")
        print(f"Total Approved Candidates : {res.get('total_approved')}")
        print(f"  - Direct NetEase Songs  : {res.get('direct_netease_count')}")
        print(f"  - Confidently Matched   : {res.get('matched_count')}")
        print(f"  - Ambiguous (Skipped)   : {res.get('ambiguous_count')}")
        print(f"  - Unmatched (Skipped)   : {res.get('unmatched_count')}")
        print(f"Tracks To Add to Playlist : {res.get('added_count', res.get('ready_to_add_count', 0))}")
        if not args.dry_run:
            print(f"Playlist URL              : {res.get('playlist_url')}")
            print(f"Publication ID            : {res.get('publication_id')}")
        print("=" * 60)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            print(f"[SAVED SUMMARY] Publish summary saved to: {args.output}")

    except Exception as exc:
        print(f"\n[PUBLISH ERROR] Failed to complete publish workflow: {exc}")
        sys.exit(1)


def cmd_bridge(args: argparse.Namespace) -> None:
    print("=" * 60)
    print("[KKBOX BRIDGE] Starting local ingestion HTTP bridge for Chrome extension")
    print(f"Host: {args.host} | Port: {args.port}")
    print(f"Database: {args.db_path}")
    print("=" * 60)

    db = DiscoveryDB(db_path=args.db_path)
    bridge_server = KKBOXBridgeServer(host=args.host, port=args.port, db=db)

    print(f"[READY] Listening on http://{args.host}:{args.port}/")
    print(f"  - Health Check: http://{args.host}:{args.port}/health")
    print(f"  - Summary     : http://{args.host}:{args.port}/status/summary")
    print(f"  - Claim Cmd   : POST http://{args.host}:{args.port}/commands/claim")
    print(f"  - Report Cmd  : POST http://{args.host}:{args.port}/commands/report")
    print(f"  - Heartbeat   : POST http://{args.host}:{args.port}/heartbeat/kkbox")
    print(f"  - Ingestion   : POST http://{args.host}:{args.port}/ingest/kkbox")
    print("Press Ctrl+C to stop the bridge.\n")

    try:
        bridge_server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOPPING] Shutting down bridge server...")
        bridge_server.shutdown()
        print("[STOPPED] Bridge server stopped.")


def cmd_daemon(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )

    interval_sec = args.interval_seconds if args.interval_seconds else args.interval_days * 24 * 3600.0

    print("=" * 60)
    print("[SONG DISCOVERY DAEMON] Starting local unattended supervisor")
    print(f"  • KKBOX Bridge : http://{args.bridge_host}:{args.bridge_port}/")
    print(f"  • Review UI    : http://{args.review_host}:{args.review_port}/")
    schedule_label = (
        f"daily at {args.daily_hour:02d}:00 Asia/Shanghai"
        if args.daily_hour is not None and not args.natural_week
        else f"every {args.interval_days:.1f} days ({interval_sec:.0f}s)"
    )
    print(f"  • Discovery    : QQ + NetEase {schedule_label}")
    print(f"  • Database     : {args.db_path}")
    print(f"  • Notifications: {'Disabled' if args.no_notifications else 'Enabled (macOS Desktop)'}")
    print("=" * 60)

    supervisor = DiscoverySupervisor(
        db_path=args.db_path,
        bridge_host=args.bridge_host,
        bridge_port=args.bridge_port,
        review_host=args.review_host,
        review_port=args.review_port,
        interval_seconds=interval_sec,
        align_natural_week=args.natural_week,
        daily_run_hour=args.daily_hour,
        run_on_start=not args.no_run_on_start,
        startup_debounce_seconds=args.startup_debounce_minutes * 60.0,
        cookie_file=args.cookie_file,
        netease_url=args.netease_url,
        auto_start_netease=not args.no_auto_start,
        enable_notifications=not args.no_notifications,
        debounce_seconds=args.debounce_seconds,
        state_path=args.state_path,
        lock_path=args.lock_path,
    )

    try:
        supervisor.run_forever(run_once=args.once)
    except SupervisorLockError as lock_err:
        print(f"\n[DAEMON ERROR] {lock_err}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[DAEMON] Stopping supervisor on KeyboardInterrupt...")


def cmd_feedback_stats(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    learner = PreferenceLearner()
    feedbacks = db.get_feedbacks()
    stats = db.get_feedback_stats()
    criteria = learner.check_activation_criteria(feedbacks)
    active_model = db.get_active_model_version()

    print("=" * 60)
    print(f"Personalized Preference Feedback Stats ({args.db_path})")
    print("=" * 60)
    print(f"Total Feedback Records : {stats.get('total', 0)}")
    print(f"  • Approved (Positive): {stats.get('approved', 0)}")
    print(f"  • Rejected (Negative): {stats.get('rejected', 0)}")
    print(f"  • Deferred           : {stats.get('deferred', 0)}")
    print("-" * 60)
    print(f"Learner Activation Status: {'ACTIVE' if criteria['can_activate'] else 'COLD_START'}")
    print(f"  • {criteria['message']}")
    if active_model:
        print(f"Active Model Version   : {active_model.get('version_id')}")
        metrics = active_model.get("metrics", {})
        if metrics:
            print(f"  - Eval Recall (Gate) : {metrics.get('eval_recall', 0.0):.1%}")
            print(f"  - Eval Precision     : {metrics.get('eval_precision', 0.0):.1%}")
            print(f"  - Features Learned   : {metrics.get('learned_features_count', 0)}")
    else:
        print("Active Model Version   : None (Cold start default scoring)")
    print("-" * 60)
    if stats.get("reasons"):
        print("Rejection Reasons Breakdown:")
        for r_code, cnt in stats["reasons"].items():
            lbl = REJECTION_REASON_LABELS.get(r_code, r_code)
            print(f"  • {lbl:<16} ({r_code}): {cnt}")
    print("=" * 60)


def cmd_train_model(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    print(f"[MODEL LEARNER] Training preference model from feedback in {args.db_path}...")
    model, summary = train_preference_model_from_db(db=db, version_id=args.version_id, cv_folds=args.cv_folds)

    if not summary.get("success"):
        print(f"\n[TRAIN FAILED] {summary.get('criteria', {}).get('message', 'Insufficient samples')}")
        sys.exit(1)

    metrics = summary.get("metrics", {})
    passed = summary.get("recall_gate_passed", False)
    print("\n" + "=" * 60)
    print(f"[TRAIN SUCCESS] Model Version: {summary.get('version_id')}")
    print(f"Recall Safety Gate Passed: {'YES (>=95%)' if passed else 'NO (FAILED GATE)'}")
    print(f"  • Grouped CV Recall    : {metrics.get('eval_recall', 0.0):.1%}")
    print(f"  • Grouped CV Precision : {metrics.get('eval_precision', 0.0):.1%}")
    print(f"  • Grouped CV F1 Score  : {metrics.get('eval_f1', 0.0):.4f}")
    print(f"  • Features Extracted   : {metrics.get('learned_features_count', 0)}")
    print(f"  • Total Labeled Items  : {metrics.get('total_samples', 0)}")
    print("=" * 60)
    if not summary.get("activated"):
        print("[NOT ACTIVATED] Candidate model failed a safety/comparison gate; active model is unchanged.")
        sys.exit(2)


def cmd_rollback_model(args: argparse.Namespace) -> None:
    db = DiscoveryDB(db_path=args.db_path)
    success = db.rollback_model_version(args.version_id)
    if success:
        print(f"[SUCCESS] Active model rolled back to: {args.version_id}")
    else:
        print(f"[ERROR] Version '{args.version_id}' not found in model registry.")
        sys.exit(1)


def main():
    # Load local credentials only for actual CLI/daemon execution. Keeping this
    # out of module import avoids leaking workstation configuration into tests
    # and library consumers that merely import song_discovery.cli.
    load_dotenv()
    parser = argparse.ArgumentParser(description="Multi-platform Song Discovery Orchestrator, Publisher, Bridge & Daemon CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 1. Collect command
    collect_parser = subparsers.add_parser("collect", help="Collect releases, select tracks, score, and persist to SQLite")
    collect_parser.add_argument("--platform", choices=["qq", "netease", "kkbox", "all"], default="all", help="Target platform (default: all)")
    collect_parser.add_argument("--limit", type=int, default=10, help="Releases limit for QQ and KKBOX per area/category (default: 10)")
    collect_parser.add_argument("--netease-pages", type=int, default=15, help="Number of pages to collect for NetEase new albums (default: 15)")
    collect_parser.add_argument("--netease-page-size", type=int, default=20, help="Page size for NetEase new albums (default: 20)")
    collect_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")
    collect_parser.add_argument("--netease-url", type=str, default=None, help="Base URL for local NetEase API (default: http://localhost:3000)")
    collect_parser.add_argument("--no-auto-start", action="store_true", help="Opt-out of auto-starting local NetEase API service")

    # 2. Status command
    status_parser = subparsers.add_parser("status", help="Show 3-platform adapter health and discovery status")
    status_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path")
    status_parser.add_argument("--state-path", type=str, default="output/supervisor_state.json", help="Supervisor state file path")

    # 3. Export command
    export_parser = subparsers.add_parser("export", help="Export candidates to JSON")
    export_parser.add_argument("--status", choices=["pending", "approved", "rejected", "deferred", "all"], default="approved", help="Candidate review status filter (default: approved)")
    export_parser.add_argument("--output", type=str, default="output/candidates_export.json", help="Output JSON file path")
    export_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")

    # 4. Stats command
    stats_parser = subparsers.add_parser("stats", help="Show database overview statistics")
    stats_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")

    # 5. Login status command
    login_parser = subparsers.add_parser("login-status", help="Check NetEase Cloud Music login session")
    login_parser.add_argument("--cookie-file", type=str, default="cookie.txt", help="Path to Netscape cookie file (default: cookie.txt)")
    login_parser.add_argument("--netease-url", type=str, default="http://localhost:3000", help="Base URL for local NetEase API")
    login_parser.add_argument("--no-auto-start", action="store_true", help="Opt-out of auto-starting local NetEase API service")

    # 6. Publish command
    publish_parser = subparsers.add_parser("publish", help="Publish approved candidates to NetEase Cloud Music playlist")
    publish_parser.add_argument("--playlist-name", type=str, default="华语新歌周刊", help="Name of playlist to create or find (default: 华语新歌周刊)")
    publish_parser.add_argument("--playlist-id", type=str, default=None, help="Existing playlist ID to append to")
    publish_parser.add_argument("--cookie-file", type=str, default="cookie.txt", help="Path to Netscape cookie file (default: cookie.txt)")
    publish_parser.add_argument("--dry-run", action="store_true", help="Preview matches & plan without mutating NetEase")
    publish_parser.add_argument("--output", type=str, default=None, help="Optional path to write publication JSON summary")
    publish_parser.add_argument("--netease-url", type=str, default="http://localhost:3000", help="Base URL for local NetEase API")
    publish_parser.add_argument("--no-auto-start", action="store_true", help="Opt-out of auto-starting local NetEase API service")
    publish_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")

    # 7. Bridge command
    bridge_parser = subparsers.add_parser("bridge", help="Run local HTTP ingestion bridge for KKBOX Chrome extension")
    bridge_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    bridge_parser.add_argument("--port", type=int, default=8765, help="Port number (default: 8765)")
    bridge_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")

    # 8. Daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Run always-on supervisor daemon (bridge + review UI + scheduled discovery)")
    daemon_parser.add_argument("--interval-days", type=float, default=1.0, help="Interval in days between scheduled discovery runs (default: 1.0)")
    daemon_parser.add_argument("--interval-seconds", type=float, default=None, help="Override interval in seconds")
    daemon_parser.add_argument("--bridge-host", type=str, default="127.0.0.1", help="KKBOX bridge host (default: 127.0.0.1)")
    daemon_parser.add_argument("--bridge-port", type=int, default=8765, help="KKBOX bridge port (default: 8765)")
    daemon_parser.add_argument("--review-host", type=str, default="127.0.0.1", help="Review UI host (default: 127.0.0.1)")
    daemon_parser.add_argument("--review-port", type=int, default=8502, help="Review UI port (default: 8502)")
    daemon_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path (default: output/discovery.db)")
    daemon_parser.add_argument("--cookie-file", type=str, default="cookie.txt", help="Path to Netscape cookie file (default: cookie.txt)")
    daemon_parser.add_argument("--netease-url", type=str, default=None, help="Base URL for local NetEase API (default: http://localhost:3000)")
    daemon_parser.add_argument("--no-auto-start", action="store_true", help="Opt-out of auto-starting local NetEase API service")
    daemon_parser.add_argument("--no-notifications", action="store_true", help="Disable macOS desktop notifications")
    daemon_parser.add_argument("--debounce-seconds", type=float, default=60.0, help="Notification debounce quiet window in seconds (default: 60.0)")
    daemon_parser.add_argument("--natural-week", action="store_true", help="Use Tuesday/Friday natural-week checkpoints instead of the daily interval")
    daemon_parser.add_argument("--daily-hour", type=int, default=1, choices=range(24), metavar="0-23", help="Run scheduled discovery at this hour in Asia/Shanghai (default: 1)")
    daemon_parser.add_argument("--no-run-on-start", action="store_true", help="Do not run discovery immediately when the daemon starts")
    daemon_parser.add_argument("--startup-debounce-minutes", type=float, default=30.0, help="Suppress repeated startup collection during rapid crash restarts (default: 30 minutes)")
    daemon_parser.add_argument("--state-path", type=str, default="output/supervisor_state.json", help="Supervisor persistent state file")
    daemon_parser.add_argument("--lock-path", type=str, default="output/supervisor.lock", help="Supervisor PID lock file")
    daemon_parser.add_argument("--once", action="store_true", help="Run a single pass and exit (for smoke testing)")

    # 9. Feedback stats command
    fb_parser = subparsers.add_parser("feedback-stats", help="Show editorial feedback counts and model activation status")
    fb_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path")

    # 10. Train model command
    train_parser = subparsers.add_parser("train-model", help="Train and register personalized preference model from SQLite feedback")
    train_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path")
    train_parser.add_argument("--version-id", type=str, default=None, help="Optional version identifier")
    train_parser.add_argument("--cv-folds", type=int, default=5, help="Number of Grouped CV folds (default: 5)")

    # 11. Rollback model command
    rb_parser = subparsers.add_parser("rollback-model", help="Roll back active preference model to a specified version")
    rb_parser.add_argument("--version-id", type=str, required=True, help="Target model version identifier to activate")
    rb_parser.add_argument("--db-path", type=str, default="output/discovery.db", help="SQLite DB path")

    args = parser.parse_args()
    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "login-status":
        cmd_login_status(args)
    elif args.command == "publish":
        cmd_publish(args)
    elif args.command == "bridge":
        cmd_bridge(args)
    elif args.command == "daemon":
        cmd_daemon(args)
    elif args.command == "feedback-stats":
        cmd_feedback_stats(args)
    elif args.command == "train-model":
        cmd_train_model(args)
    elif args.command == "rollback-model":
        cmd_rollback_model(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
