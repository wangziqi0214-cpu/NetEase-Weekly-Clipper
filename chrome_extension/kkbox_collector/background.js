/**
 * KKBOX Web Player Extension - Invisible Session Sidecar Service Worker
 *
 * Functions purely as a session sidecar:
 * 1. Periodically sends heartbeat & polls 127.0.0.1:8765 for idempotent collection commands.
 * 2. On command claim, launches resumable DOM extraction using existing user browser session.
 * 3. Reports progress & status back to the local bridge.
 * 4. Never exposes or transmits user credentials or cookies.
 */

// Import pure extraction / job management logic
importScripts("source_config.js", "extractors.js", "job_manager.js");

const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
const POLL_ALARM_NAME = "kkbox_bridge_poll_alarm";
const RETRY_ALARM_NAME = "kkbox_retry_alarm";
const POLL_INTERVAL_MINUTES = 0.5; // ~30 seconds alarm fallback
const RETRY_MINUTES = 1440; // 24 hours retry for geoblocked / auth_required

const MAX_ALBUMS_CAP = 500;
const BATCH_SIZE = 10;
const CONCURRENCY_LIMIT = 2;

let isCollectorRunning = false;
let offscreenPort = null;
let activeCommandId = null;

// -----------------------------------------------------------------------------
// Bridge Connectivity & Heartbeat / Command Polling
// -----------------------------------------------------------------------------
async function checkBridgeHealth() {
  try {
    const res = await fetch(`${DEFAULT_BRIDGE_URL}/health`, { method: "GET" });
    return res.ok;
  } catch {
    return false;
  }
}

async function sendHeartbeat() {
  try {
    const stored = await getStorageData();
    const job = stored.current_job;
    let status = "idle";
    if (isCollectorRunning) {
      status = "crawling";
    } else if (job) {
      status = job.status || "idle";
    }

    await fetch(`${DEFAULT_BRIDGE_URL}/heartbeat/kkbox`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sidecar_version: "1.3.7",
        status: status,
        current_job_id: job ? job.job_id : null,
        active_command_id: activeCommandId,
        cumulative_ingested: job ? job.cumulative_ingested : 0,
        error: job ? job.error : null,
      }),
    });
  } catch (err) {
    // Ignore bridge offline during background poll
  }
}

async function pollAndClaimCommand() {
  if (isCollectorRunning) {
    await sendHeartbeat();
    return;
  }

  try {
    const res = await fetch(`${DEFAULT_BRIDGE_URL}/commands/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sidecar_id: "chrome_extension_sidecar" }),
    });

    if (!res.ok) return;
    const data = await res.json();
    if (data && data.command && data.command.command_id) {
      if (data.command.action !== "collect_kkbox") {
        await reportCommandStatus(
          data.command.command_id,
          "failed",
          null,
          `Unsupported sidecar action: ${data.command.action || "missing"}`,
        );
        await sendHeartbeat();
        return;
      }
      console.log("[Sidecar] Claimed collection command:", data.command.command_id);
      activeCommandId = data.command.command_id;
      runCollector(data.command.command_id);
    } else {
      await sendHeartbeat();
    }
  } catch (err) {
    // Bridge offline
  }
}

async function reportCommandStatus(commandId, status, progress = null, error = null) {
  if (!commandId) return;
  try {
    await fetch(`${DEFAULT_BRIDGE_URL}/commands/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: commandId,
        status: status,
        progress: progress,
        error: error,
      }),
    });
  } catch (err) {
    console.warn("[Sidecar] Failed to report command status:", err);
  }
}

// -----------------------------------------------------------------------------
// Offscreen Keepalive Management
// -----------------------------------------------------------------------------
async function ensureOffscreenDocument() {
  if (offscreenPort) return;
  try {
    const existingContexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
    });
    if (!existingContexts || existingContexts.length === 0) {
      await chrome.offscreen.createDocument({
        url: "offscreen.html",
        reasons: ["DOM_SCRAPING"],
        justification: "Maintain MV3 service worker keepalive heartbeat during active crawl batch ingestion",
      });
    }
  } catch (err) {
    console.warn("[Offscreen] createDocument note:", err.message);
  }
}

async function closeOffscreenDocument() {
  if (offscreenPort) {
    try {
      offscreenPort.disconnect();
    } catch {}
    offscreenPort = null;
  }
  try {
    const existing = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
    });
    if (existing && existing.length > 0) {
      await chrome.offscreen.closeDocument();
    }
  } catch {}
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "kkbox_offscreen_keepalive" || port.name === "offscreen-keepalive") {
    offscreenPort = port;
    port.onMessage.addListener(() => {
      // Echo keepalive heartbeat
      try {
        port.postMessage({ type: "heartbeat_ack", timestamp: Date.now() });
      } catch {}
    });
    port.onDisconnect.addListener(() => {
      offscreenPort = null;
    });
  }
});

// -----------------------------------------------------------------------------
// Tab Extraction Helper (Reuses Inactive Tabs)
// -----------------------------------------------------------------------------
async function extractFromTab(url, action, context = {}) {
  let tabId = null;
  try {
    const tab = await chrome.tabs.create({ url, active: false });
    tabId = tab.id;

    await new Promise((resolve) => {
      const listener = (tId, changeInfo) => {
        if (tId === tabId && changeInfo.status === "complete") {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, 15000);
    });

    // Wait 1.2s for SPA hydration
    await new Promise((r) => setTimeout(r, 1200));

    const response = await chrome.tabs.sendMessage(tabId, { action: action, ...context });
    return response || { status: "empty" };
  } catch (err) {
    console.warn(`[Tab] Error extracting ${action} from ${url}:`, err.message);
    return { status: "error", error: err.message };
  } finally {
    if (tabId) {
      try {
        await chrome.tabs.remove(tabId);
      } catch {}
    }
  }
}

async function mapConcurrent(items, limit, fn) {
  const results = [];
  const executing = [];
  for (const item of items) {
    const p = Promise.resolve().then(() => fn(item));
    results.push(p);
    if (limit <= items.length) {
      const e = p.then(() => executing.splice(executing.indexOf(e), 1));
      executing.push(e);
      if (executing.length >= limit) {
        await Promise.race(executing);
      }
    }
  }
  return Promise.all(results);
}

// -----------------------------------------------------------------------------
// Core Collector Orchestration Loop
// -----------------------------------------------------------------------------
async function runCollector(commandId = null) {
  if (isCollectorRunning) return;
  isCollectorRunning = true;
  if (commandId) activeCommandId = commandId;

  await ensureOffscreenDocument();

  try {
    const isBridgeUp = await checkBridgeHealth();
    if (!isBridgeUp) {
      console.warn("[Background] Bridge offline at startup. Pausing job.");
      const stored = await getStorageData();
      if (stored.current_job) {
        stored.current_job.status = "paused_bridge_offline";
        stored.current_job.error = "本地 Python 桥接服务离线，已保留进度待恢复";
        await saveJobState(stored.current_job);
      }
      if (activeCommandId) {
        await reportCommandStatus(activeCommandId, "paused", null, "Bridge offline");
      }
      return;
    }

    let job = await getOrInitJob();
    if (job.status === "completed" || job.status === "no_data") {
      job = createInitialJobState();
      await saveJobState(job);
    }

    job.status = "running";
    job.error = null;
    await saveJobState(job);

    if (activeCommandId) {
      await reportCommandStatus(activeCommandId, "running", { stage: job.stage });
    }

    // Stage 1: Seed the exact user-approved KKBOX category listings.
    if (job.stage === "init" || job.stage === "discovering_routes") {
      job.stage = "discovering_routes";
      await saveJobState(job);

      addListingUrls(job, KKBOX_FIXED_NEW_RELEASE_URLS);
      job.stage = "crawling_listings";
      await saveJobState(job);
    }

    // Stage 2: Crawl Listings
    if (job.stage === "crawling_listings") {
      while (job.pending_listing_urls && job.pending_listing_urls.length > 0) {
        const isUp = await checkBridgeHealth();
        if (!isUp) {
          job.status = "paused_bridge_offline";
          job.error = "本地 Python 桥接服务离线，已保留当前采集进度";
          await saveJobState(job);
          await closeOffscreenDocument();
          return;
        }

        const listUrl = job.pending_listing_urls.shift();
        job.processed_listing_urls.push(listUrl);

        const res = await extractFromTab(listUrl, "extract_listing");
        if (res.status === "auth_required" || res.status === "geoblocked") {
          restoreListingUrl(job, listUrl);
          job.status = res.status;
          job.error = res.status === "auth_required" ? "需登录 KKBOX" : "地区受限";
          await saveJobState(job);
          if (activeCommandId) {
            await reportCommandStatus(activeCommandId, "failed", null, job.error);
            activeCommandId = null;
          }
          await closeOffscreenDocument();
          chrome.alarms.create(RETRY_ALARM_NAME, { delayInMinutes: RETRY_MINUTES });
          return;
        }

        if (res && Array.isArray(res.album_cards) && res.album_cards.length > 0) {
          addAlbumCards(job, res.album_cards, MAX_ALBUMS_CAP);
        } else if (res && Array.isArray(res.album_links)) {
          addAlbumUrls(job, res.album_links, MAX_ALBUMS_CAP);
        } else if (res.status === "failed" || res.status === "error" || res.status === "timeout") {
          recordListingFailure(job, listUrl);
        }

        await saveJobState(job);
        await new Promise((r) => setTimeout(r, 300));
      }

      job.stage = "extracting_albums";
      await saveJobState(job);
    }

    // Stage 3: Extract Album Details & Ingest in Batches (10 albums / batch)
    if (job.stage === "extracting_albums") {
      while (
        (job.pending_album_urls && job.pending_album_urls.length > 0) ||
        (job.retry_album_urls && job.retry_album_urls.length > 0)
      ) {
        const isUp = await checkBridgeHealth();
        if (!isUp) {
          job.status = "paused_bridge_offline";
          job.error = "本地 Python 桥接服务离线，已保留批次进度待恢复";
          await saveJobState(job);
          await closeOffscreenDocument();
          return;
        }

        const workItems = getNextAlbumBatch(job, BATCH_SIZE);
        if (workItems.length === 0) break;

        const batchReleases = [];
        const batchSuccessItems = [];
        let pauseCondition = null;

        await mapConcurrent(workItems, CONCURRENCY_LIMIT, async (item) => {
          if (pauseCondition) return;

          const res = await extractFromTab(item.url, "extract_album_detail", { hint: item.hint || {} });
          if (res.status === "auth_required" || res.status === "geoblocked") {
            pauseCondition = res.status;
            return;
          }

          const hasRealReleaseTitle = res && res.data && res.data.title && !/^(kkbox|kkbox web player|let's music - kkbox)$/i.test(res.data.title.trim());
          const releaseArtist = res && res.data && Array.isArray(res.data.artists) && res.data.artists[0] && res.data.artists[0].name;
          const hasRealReleaseArtist = releaseArtist && !/^(kkbox artist|unknown|unknown artist)$/i.test(releaseArtist.trim());
          const hasTrackArtists = res && res.data && Array.isArray(res.data.tracks) && res.data.tracks.every((track) =>
            Array.isArray(track.artists) && track.artists.some((artist) => artist && artist.name && !/^(kkbox artist|unknown|unknown artist)$/i.test(artist.name.trim()))
          );
          if (res && res.status === "album_detail" && res.data && hasRealReleaseTitle && (hasRealReleaseArtist || hasTrackArtists) && res.data.tracks && res.data.tracks.length > 0) {
            batchReleases.push(res.data);
            batchSuccessItems.push(item);
          } else {
            recordAlbumFailure(job, item.url, item.is_retry);
          }
          await new Promise((r) => setTimeout(r, 200));
        });

        if (pauseCondition) {
          restoreUnsentBatch(job, workItems);
          job.status = pauseCondition;
          job.error = pauseCondition === "auth_required" ? "需登录 KKBOX" : "地区受限";
          await saveJobState(job);
          if (activeCommandId) {
            await reportCommandStatus(activeCommandId, "failed", null, job.error);
            activeCommandId = null;
          }
          await closeOffscreenDocument();
          chrome.alarms.create(RETRY_ALARM_NAME, { delayInMinutes: RETRY_MINUTES });
          return;
        }

        if (batchReleases.length > 0) {
          try {
            const resp = await fetch(`${DEFAULT_BRIDGE_URL}/ingest/kkbox`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ releases: batchReleases }),
            });

            if (!resp.ok) {
              const errText = await resp.text();
              throw new Error(`HTTP ${resp.status}: ${errText}`);
            }

            recordBatchSuccess(job, batchSuccessItems.map((i) => i.url), batchReleases.length);
          } catch (fetchErr) {
            restoreUnsentBatch(job, batchSuccessItems);
            job.status = "paused_bridge_offline";
            job.error = `数据同步至本地桥接失败 (${fetchErr.message})，已暂存进度`;
            await saveJobState(job);
            await closeOffscreenDocument();
            return;
          }
        }

        await saveJobState(job);

        if (activeCommandId) {
          await reportCommandStatus(activeCommandId, "running", {
            processed: job.processed_album_urls.length,
            pending: job.pending_album_urls.length,
            ingested: job.cumulative_ingested,
          });
        }
        await new Promise((r) => setTimeout(r, 300));
      }

      finalizeJobCompletion(job);
      await saveJobState(job);

      if (activeCommandId) {
        const commandStatus = (job.status === "success" || job.status === "completed" || job.status === "success_with_warnings" || job.status === "no_data")
          ? "completed"
          : "failed";
        await reportCommandStatus(activeCommandId, commandStatus, {
          ingested: job.cumulative_ingested,
          warnings: job.warnings.length,
          job_status: job.status,
        });
        activeCommandId = null;
      }
      console.log(`[Sidecar] Crawl job ${job.job_id} finished with status=${job.status}`);
    }
  } catch (uncaughtErr) {
    console.error("[Sidecar] Uncaught error in runCollector:", uncaughtErr);
    const stored = await getStorageData();
    if (stored.current_job) {
      stored.current_job.status = "error";
      stored.current_job.error = uncaughtErr.message || String(uncaughtErr);
      await saveJobState(stored.current_job);
    }
    if (activeCommandId) {
      await reportCommandStatus(activeCommandId, "failed", null, uncaughtErr.message);
      activeCommandId = null;
    }
  } finally {
    isCollectorRunning = false;
    await closeOffscreenDocument();
  }
}

// -----------------------------------------------------------------------------
// Alarm & Runtime Listeners
// -----------------------------------------------------------------------------
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM_NAME) {
    pollAndClaimCommand();
  } else if (alarm.name === RETRY_ALARM_NAME) {
    runCollector();
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
  pollAndClaimCommand();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
  pollAndClaimCommand();
});

// Opening the popup reliably wakes an MV3 service worker. Let it request an
// immediate heartbeat instead of waiting for Chrome's alarm scheduler, which
// can be delayed after a developer-mode extension reload.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "kkbox_sidecar_wake") return false;

  pollAndClaimCommand()
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

// Developer-mode reloads do not consistently fire onInstalled/onStartup and
// may discard the prior alarm. Re-arm on every service-worker evaluation so
// the sidecar always resumes heartbeats and command polling by itself.
chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
pollAndClaimCommand();

// Periodic in-memory poll loop while worker is active
setInterval(() => {
  pollAndClaimCommand();
}, 15000);
