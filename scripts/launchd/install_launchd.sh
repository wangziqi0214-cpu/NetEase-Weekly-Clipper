#!/usr/bin/env bash
set -euo pipefail

# 1. Resolve Project Directory & Template
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMPLATE_FILE="${SCRIPT_DIR}/com.netease.weekly.clipper.plist.template"
TARGET_DIR="${LAUNCH_AGENTS_DIR:-${HOME}/Library/LaunchAgents}"
TARGET_PLIST="${TARGET_DIR}/com.netease.weekly.clipper.plist"
LOGS_DIR="${PROJECT_DIR}/output/logs"

echo "============================================================"
echo "[LaunchAgent Installer] NetEase Weekly Clipper Supervisor"
echo "Project Directory : ${PROJECT_DIR}"
echo "Target Plist      : ${TARGET_PLIST}"
echo "============================================================"

# 2. Resolve Python Executable (.venv311 -> .venv -> active python3)
PYTHON_EXEC=""
if [[ -x "${PROJECT_DIR}/.venv311/bin/python" ]]; then
    PYTHON_EXEC="${PROJECT_DIR}/.venv311/bin/python"
elif [[ -x "${PROJECT_DIR}/.venv/bin/python" ]]; then
    PYTHON_EXEC="${PROJECT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="$(command -v python3)"
else
    echo "ERROR: Python 3 executable not found!" >&2
    exit 1
fi
echo "Resolved Python   : ${PYTHON_EXEC}"

# 3. Create Logs Directory & Target Directory
mkdir -p "${LOGS_DIR}"
mkdir -p "${TARGET_DIR}"

# 4. Generate Plist from Template
sed \
    -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
    -e "s|{{PYTHON_EXEC}}|${PYTHON_EXEC}|g" \
    "${TEMPLATE_FILE}" > "${TARGET_PLIST}"

# 5. Validate Plist Syntax
echo "Validating plist syntax with plutil..."
plutil -lint "${TARGET_PLIST}"
echo "Plist validation successful."

if [[ "${1:-}" == "--validate-only" || "${1:-}" == "--dry-run" ]]; then
    echo "[DRY-RUN] Plist generated and validated at ${TARGET_PLIST}. Skipping launchctl registration."
    exit 0
fi

# 6. Idempotent Launchctl Registration
USER_UID="$(id -u)"
SERVICE_NAME="gui/${USER_UID}/com.netease.weekly.clipper"

echo "Bootstrapping service with launchctl..."
# Stop and bootout prior instance if already registered
if launchctl print "${SERVICE_NAME}" >/dev/null 2>&1; then
    echo "Stopping existing instance..."
    launchctl bootout "${SERVICE_NAME}" || true
    sleep 1
fi

launchctl bootstrap "gui/${USER_UID}" "${TARGET_PLIST}"
launchctl kickstart -k "${SERVICE_NAME}" || true

echo "============================================================"
echo "✅ LaunchAgent installed and started successfully!"
echo "Service label : com.netease.weekly.clipper"
echo "Review UI     : http://127.0.0.1:8502"
echo "KKBOX Bridge  : http://127.0.0.1:8765"
echo "Logs          : ${LOGS_DIR}"
echo "============================================================"
