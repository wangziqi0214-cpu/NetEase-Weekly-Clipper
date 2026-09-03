/**
 * Diagnostic panel logic for KKBOX Session Sidecar
 */

const BRIDGE_URL = "http://127.0.0.1:8765";

async function checkBridge() {
  try {
    const res = await fetch(`${BRIDGE_URL}/health`, { method: "GET" });
    return res.ok;
  } catch {
    return false;
  }
}

async function sendPopupHeartbeat(job) {
  try {
    await fetch(`${BRIDGE_URL}/heartbeat/kkbox`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sidecar_version: "1.3.7",
        status: (job && job.status) || "idle",
        current_job_id: job ? job.job_id : null,
        cumulative_ingested: job ? job.cumulative_ingested || 0 : 0,
        error: job ? job.error || null : null,
      }),
    });
  } catch {
    // Bridge status below will report the connection failure.
  }
}

function updateUI(job, isBridgeUp) {
  const badge = document.getElementById("bridge-badge");
  if (isBridgeUp) {
    badge.className = "badge badge-online";
    badge.textContent = "桥接正常 (8765)";
  } else {
    badge.className = "badge badge-offline";
    badge.textContent = "桥接离线";
  }

  const statusText = document.getElementById("status-text");
  const stageDesc = document.getElementById("stage-desc");
  const errorDesc = document.getElementById("error-desc");
  const resumeBadge = document.getElementById("resume-badge");

  if (!job) {
    statusText.textContent = "Sidecar 就绪 (等待命令)";
    stageDesc.textContent = "正在后台轮询本地桥接任务队列";
    errorDesc.textContent = "";
    resumeBadge.style.display = "none";
    return;
  }

  resumeBadge.style.display = job.is_resumed ? "inline-block" : "none";

  const statusMap = {
    init: "初始化",
    running: "采集同步中...",
    completed: "最近同步完成",
    success_with_warnings: "最近同步完成 (含部分警告)",
    no_data: "未采集到新发行",
    paused_bridge_offline: "已暂停 (桥接离线)",
    auth_required: "⚠️ 需在浏览器登录 KKBOX",
    geoblocked: "⚠️ 访问受限 (需开启台/港节点)",
    error: "采集异常",
  };

  statusText.textContent = statusMap[job.status] || job.status || "Sidecar 空闲就绪";
  stageDesc.textContent = job.stage ? `当前阶段: ${job.stage}` : "";
  errorDesc.textContent = job.error || "";

  document.getElementById("processed-count").textContent = (job.processed_album_urls && job.processed_album_urls.length) || 0;
  document.getElementById("pending-count").textContent = (job.pending_album_urls && job.pending_album_urls.length) || 0;
  document.getElementById("ingested-count").textContent = job.cumulative_ingested || 0;
  document.getElementById("failed-count").textContent = (job.failed_album_urls && job.failed_album_urls.length) || 0;

  if (job.updated_at) {
    const d = new Date(job.updated_at);
    document.getElementById("last-time").textContent = d.toLocaleTimeString();
  }
}

async function refresh() {
  // Opening or manually refreshing the popup also wakes the background worker,
  // so bridge status is refreshed immediately after extension reloads.
  try {
    await chrome.runtime.sendMessage({ type: "kkbox_sidecar_wake" });
  } catch {
    // The diagnostic UI can still show its locally stored job state.
  }
  chrome.storage.local.get(["current_job"], (result) => {
    sendPopupHeartbeat(result.current_job).finally(async () => {
      const isBridgeUp = await checkBridge();
      updateUI(result.current_job, isBridgeUp);
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  refresh();
  document.getElementById("btn-check").addEventListener("click", refresh);
});
