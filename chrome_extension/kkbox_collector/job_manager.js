/**
 * Checkpoint and Job Queue Manager for KKBOX Chrome Extension.
 * Handles state persistence, batch work items, resumption, and queue manipulation.
 * Pure logic module testable in Node.js.
 */

function createInitialJobState(jobId) {
  const now = new Date().toISOString();
  return {
    job_id: jobId || `job_${Date.now()}`,
    status: "running",
    stage: "init", // init -> discovering_routes -> crawling_listings -> extracting_albums -> completed
    pending_listing_urls: [],
    processed_listing_urls: [],
    failed_listing_urls: [],
    pending_album_urls: [],
    processed_album_urls: [],
    failed_album_urls: [],
    retry_album_urls: [],
    album_hints_by_url: {},
    cumulative_ingested: 0,
    cap_reached: false,
    dropped_album_count: 0,
    warnings: [],
    created_at: now,
    updated_at: now,
    resumed_count: 0,
    error: null,
  };
}

function shouldResumeJob(jobState) {
  if (!jobState || !jobState.job_id) return false;
  // Resumable if running, paused due to bridge offline, auth_required, or geoblocked
  const resumableStatuses = [
    "running",
    "paused_bridge_offline",
    "auth_required",
    "geoblocked",
  ];

  if (resumableStatuses.includes(jobState.status)) {
    const hasPendingWork =
      (jobState.pending_listing_urls && jobState.pending_listing_urls.length > 0) ||
      (jobState.pending_album_urls && jobState.pending_album_urls.length > 0) ||
      (jobState.retry_album_urls && jobState.retry_album_urls.length > 0) ||
      jobState.stage === "discovering_routes";
    return Boolean(hasPendingWork);
  }
  return false;
}

function addListingUrls(jobState, urls) {
  const seen = new Set([
    ...(jobState.pending_listing_urls || []),
    ...(jobState.processed_listing_urls || []),
    ...(jobState.failed_listing_urls || []),
  ]);
  const newUrls = [];
  for (const u of (urls || [])) {
    if (u && !seen.has(u)) {
      seen.add(u);
      newUrls.push(u);
    }
  }
  jobState.pending_listing_urls = [...(jobState.pending_listing_urls || []), ...newUrls];
  jobState.updated_at = new Date().toISOString();
  return newUrls.length;
}

function addAlbumUrls(jobState, urls, maxCap = 500) {
  const seen = new Set([
    ...(jobState.pending_album_urls || []),
    ...(jobState.processed_album_urls || []),
    ...(jobState.failed_album_urls || []),
    ...(jobState.retry_album_urls || []),
  ]);

  const currentTotal = seen.size;
  const availableSlots = Math.max(0, maxCap - currentTotal);

  const candidateUrls = [];
  for (const u of (urls || [])) {
    if (u && !seen.has(u)) {
      seen.add(u);
      candidateUrls.push(u);
    }
  }

  const toAdd = candidateUrls.slice(0, availableSlots);
  const droppedCount = candidateUrls.length - toAdd.length;

  if (droppedCount > 0) {
    jobState.cap_reached = true;
    jobState.dropped_album_count = (jobState.dropped_album_count || 0) + droppedCount;
  }

  jobState.pending_album_urls = [...(jobState.pending_album_urls || []), ...toAdd];
  jobState.updated_at = new Date().toISOString();
  return toAdd.length;
}

function addAlbumCards(jobState, cards, maxCap = 500) {
  if (!jobState.album_hints_by_url || typeof jobState.album_hints_by_url !== "object") {
    jobState.album_hints_by_url = {};
  }
  const normalized = [];
  for (const card of (cards || [])) {
    if (!card || !card.url) continue;
    normalized.push(card.url);
    const prior = jobState.album_hints_by_url[card.url] || {};
    jobState.album_hints_by_url[card.url] = {
      title: String(card.title || prior.title || "").trim(),
      artist: String(card.artist || prior.artist || "").trim(),
    };
  }
  return addAlbumUrls(jobState, normalized, maxCap);
}

/**
 * Returns a batch of work items with immutable provenance: { url: string, is_retry: boolean }
 */
function getNextAlbumBatch(jobState, batchSize = 10) {
  const retryUrls = (jobState.retry_album_urls || []).splice(0, batchSize);
  const remainingSlots = batchSize - retryUrls.length;
  const pendingUrls = remainingSlots > 0 ? (jobState.pending_album_urls || []).splice(0, remainingSlots) : [];

  const makeWorkItem = (url, isRetry) => {
    const item = { url, is_retry: isRetry };
    const hint = (jobState.album_hints_by_url || {})[url];
    if (hint && (hint.title || hint.artist)) item.hint = hint;
    return item;
  };
  const workItems = [
    ...retryUrls.map(url => makeWorkItem(url, true)),
    ...pendingUrls.map(url => makeWorkItem(url, false)),
  ];

  jobState.updated_at = new Date().toISOString();
  return workItems;
}

function recordBatchSuccess(jobState, successfulUrls, ingestedCount) {
  const seenProcessed = new Set(jobState.processed_album_urls || []);
  for (const u of (successfulUrls || [])) {
    if (u && !seenProcessed.has(u)) {
      seenProcessed.add(u);
      jobState.processed_album_urls.push(u);
    }
  }
  jobState.cumulative_ingested = (jobState.cumulative_ingested || 0) + (ingestedCount || 0);
  jobState.updated_at = new Date().toISOString();
}

/**
 * Record an album failure.
 * If is_retry is false (1st attempt), stages into retry_album_urls once.
 * If is_retry is true (2nd attempt), records permanently into failed_album_urls and never requeues.
 */
function recordAlbumFailure(jobState, url, isRetryAttempt = false) {
  if (!url) return;

  if (!isRetryAttempt) {
    // 1st attempt failure -> stage for retry if not already in retry/processed/failed
    const alreadyProcessed = (jobState.processed_album_urls || []).includes(url);
    const alreadyFailed = (jobState.failed_album_urls || []).includes(url);
    const alreadyRetry = (jobState.retry_album_urls || []).includes(url);

    if (!alreadyProcessed && !alreadyFailed && !alreadyRetry) {
      jobState.retry_album_urls.push(url);
    }
  } else {
    // 2nd attempt failure -> permanent failure
    if (!jobState.failed_album_urls.includes(url)) {
      jobState.failed_album_urls.push(url);
    }
    // Ensure removed from retry list
    jobState.retry_album_urls = (jobState.retry_album_urls || []).filter(u => u !== url);
  }
  jobState.updated_at = new Date().toISOString();
}

/**
 * Restore unsent or interrupted work items to queues.
 * Dedupes across BOTH pending and retry queues, processed and failed sets.
 */
function restoreUnsentBatch(jobState, workItems) {
  const pendingSet = new Set(jobState.pending_album_urls || []);
  const retrySet = new Set(jobState.retry_album_urls || []);
  const processedSet = new Set(jobState.processed_album_urls || []);
  const failedSet = new Set(jobState.failed_album_urls || []);

  const toRestorePending = [];
  const toRestoreRetry = [];

  for (const item of (workItems || [])) {
    const url = typeof item === "string" ? item : item.url;
    const isRetry = typeof item === "object" ? Boolean(item.is_retry) : false;

    if (!url) continue;
    // If already finalized or already queued in either queue, do not duplicate
    if (processedSet.has(url) || failedSet.has(url) || pendingSet.has(url) || retrySet.has(url)) {
      continue;
    }

    if (isRetry) {
      retrySet.add(url);
      toRestoreRetry.push(url);
    } else {
      pendingSet.add(url);
      toRestorePending.push(url);
    }
  }

  jobState.pending_album_urls = [...toRestorePending, ...(jobState.pending_album_urls || [])];
  jobState.retry_album_urls = [...toRestoreRetry, ...(jobState.retry_album_urls || [])];
  jobState.updated_at = new Date().toISOString();
}

/**
 * Restore a single listing URL back to pending on pause/interruption.
 */
function restoreListingUrl(jobState, url) {
  if (!url) return;
  jobState.processed_listing_urls = (jobState.processed_listing_urls || []).filter(u => u !== url);
  if (!jobState.pending_listing_urls.includes(url)) {
    jobState.pending_listing_urls.unshift(url);
  }
  jobState.updated_at = new Date().toISOString();
}

/**
 * Record a permanent failure for a listing page.
 */
function recordListingFailure(jobState, url) {
  if (!url) return;
  jobState.processed_listing_urls = (jobState.processed_listing_urls || []).filter(u => u !== url);
  if (!jobState.failed_listing_urls.includes(url)) {
    jobState.failed_listing_urls.push(url);
  }
  jobState.updated_at = new Date().toISOString();
}

/**
 * Compute the terminal status and structured warnings for a completed job.
 */
function finalizeJobCompletion(jobState) {
  jobState.stage = "completed";
  jobState.warnings = [];

  const processedCount = (jobState.processed_album_urls || []).length;
  const ingestedCount = jobState.cumulative_ingested || 0;
  const failedAlbumCount = (jobState.failed_album_urls || []).length;
  const failedListingCount = (jobState.failed_listing_urls || []).length;

  if (processedCount === 0 && ingestedCount === 0) {
    jobState.status = "no_data";
    jobState.error = "未发现任何有效专辑或曲目。请确认已在 Chrome 中登录 play.kkbox.com 并开启台湾/香港节点。";
    jobState.updated_at = new Date().toISOString();
    return;
  }

  if (failedAlbumCount > 0) {
    jobState.warnings.push(`${failedAlbumCount} 张专辑多次解析失败`);
  }
  if (failedListingCount > 0) {
    jobState.warnings.push(`${failedListingCount} 个分类列表加载失败`);
  }
  if (jobState.cap_reached) {
    jobState.warnings.push(`已触发 500 张上限，截断了 ${jobState.dropped_album_count || 0} 张多余专辑`);
  }

  if (jobState.warnings.length > 0) {
    jobState.status = "success_with_warnings";
    jobState.error = jobState.warnings.join("; ");
  } else {
    jobState.status = "success";
    jobState.error = null;
  }

  jobState.updated_at = new Date().toISOString();
}

/**
 * Resolves the active chrome.storage.local area or an injected mock storage.
 */
function getStorage(storageArea) {
  if (storageArea) return storageArea;
  if (typeof chrome !== "undefined" && chrome && chrome.storage && chrome.storage.local) {
    return chrome.storage.local;
  }
  return null;
}

/**
 * Reads data from chrome.storage.local asynchronously.
 * Returns an object containing the requested keys or the entire storage.
 * If storage is unavailable, resolves safely to empty object {}.
 */
function getStorageData(keys = null, storageArea = null) {
  const storage = getStorage(storageArea);
  if (!storage) {
    return Promise.resolve({});
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    try {
      const query = keys !== undefined ? keys : null;
      const res = storage.get(query, (result) => {
        if (settled) return;
        settled = true;
        if (typeof chrome !== "undefined" && chrome && chrome.runtime && chrome.runtime.lastError) {
          return reject(new Error(chrome.runtime.lastError.message));
        }
        resolve(result || {});
      });
      if (res && typeof res.then === "function") {
        res.then(
          (val) => {
            if (!settled) {
              settled = true;
              resolve(val || {});
            }
          },
          (err) => {
            if (!settled) {
              settled = true;
              reject(err);
            }
          }
        );
      }
    } catch (err) {
      if (!settled) {
        settled = true;
        reject(err);
      }
    }
  });
}

/**
 * Persists the current job state to chrome.storage.local with updated_at timestamp.
 * Returns a Promise resolving to the saved jobState.
 */
function saveJobState(jobState, storageArea = null) {
  const storage = getStorage(storageArea);
  if (jobState && typeof jobState === "object") {
    jobState.updated_at = new Date().toISOString();
  }
  if (!storage) {
    return Promise.resolve(jobState || null);
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    try {
      const res = storage.set({ current_job: jobState || null }, () => {
        if (settled) return;
        settled = true;
        if (typeof chrome !== "undefined" && chrome && chrome.runtime && chrome.runtime.lastError) {
          return reject(new Error(chrome.runtime.lastError.message));
        }
        resolve(jobState || null);
      });
      if (res && typeof res.then === "function") {
        res.then(
          () => {
            if (!settled) {
              settled = true;
              resolve(jobState || null);
            }
          },
          (err) => {
            if (!settled) {
              settled = true;
              reject(err);
            }
          }
        );
      }
    } catch (err) {
      if (!settled) {
        settled = true;
        reject(err);
      }
    }
  });
}

/**
 * Retrieves the existing job from storage or initializes a fresh job state.
 * If the existing job is resumable (shouldResumeJob returns true), increments resumed_count,
 * updates updated_at, marks is_resumed, saves to storage, and returns it.
 * If not resumable or no job exists, creates a fresh job via createInitialJobState,
 * saves to storage, and returns it.
 */
async function getOrInitJob(storageArea = null) {
  const stored = await getStorageData(["current_job"], storageArea);
  const existingJob = stored && stored.current_job;

  if (existingJob && shouldResumeJob(existingJob)) {
    existingJob.resumed_count = (existingJob.resumed_count || 0) + 1;
    existingJob.is_resumed = true;
    await saveJobState(existingJob, storageArea);
    return existingJob;
  }

  const newJob = createInitialJobState();
  await saveJobState(newJob, storageArea);
  return newJob;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    createInitialJobState,
    shouldResumeJob,
    addListingUrls,
    addAlbumUrls,
    addAlbumCards,
    getNextAlbumBatch,
    recordBatchSuccess,
    recordAlbumFailure,
    restoreUnsentBatch,
    restoreListingUrl,
    recordListingFailure,
    finalizeJobCompletion,
    getStorage,
    getStorageData,
    saveJobState,
    getOrInitJob,
  };
}
