#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${LAUNCH_AGENTS_DIR:-${HOME}/Library/LaunchAgents}"
TARGET_PLIST="${TARGET_DIR}/com.netease.weekly.clipper.plist"
USER_UID="$(id -u)"
SERVICE_NAME="gui/${USER_UID}/com.netease.weekly.clipper"

echo "============================================================"
echo "[LaunchAgent Uninstaller] NetEase Weekly Clipper Supervisor"
echo "============================================================"

# 1. Stop and bootout service if active
if launchctl print "${SERVICE_NAME}" >/dev/null 2>&1; then
    echo "Stopping and unregistering ${SERVICE_NAME}..."
    launchctl bootout "${SERVICE_NAME}" || true
else
    echo "Service is not currently active in launchctl."
fi

# 2. Remove narrowly-scoped plist file only
if [[ -f "${TARGET_PLIST}" ]]; then
    echo "Removing plist: ${TARGET_PLIST}"
    rm -f "${TARGET_PLIST}"
    echo "✅ Plist file removed."
else
    echo "Plist file does not exist: ${TARGET_PLIST}"
fi

echo "============================================================"
echo "✅ LaunchAgent uninstalled successfully."
echo "============================================================"
