/**
 * Pure Node.js unit tests for KKBOX Extension Job & Checkpoint Manager.
 * Includes regression tests for resume states, mixed-batch pause restoration,
 * failure categorization, and 500 cap warning tracking.
 * Run with: node tests/chrome_extension/test_job_manager.js
 */

const assert = require("assert");
const { KKBOX_FIXED_NEW_RELEASE_URLS } = require("../../chrome_extension/kkbox_collector/source_config.js");
const {
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
} = require("../../chrome_extension/kkbox_collector/job_manager.js");

console.log("[TEST] Running KKBOX Job Manager & Checkpoint regression tests...");

// 0. Collection scope is the exact three user-approved category pages.
{
  assert.deepStrictEqual([...KKBOX_FIXED_NEW_RELEASE_URLS], [
    "https://play.kkbox.com/discover/categories/GtN6qomUYBvqtWeRP8/new-releases",
    "https://play.kkbox.com/discover/categories/1ZQwmFTaLE4p7BG-Ua/new-releases",
    "https://play.kkbox.com/discover/categories/Okrqvkq-3_sNf4ForP/new-releases",
  ]);
  console.log("✔ Fixed KKBOX category source scope passed");
}

// 1. Initial State Creation
{
  const job = createInitialJobState("test_job_001");
  assert.strictEqual(job.job_id, "test_job_001");
  assert.strictEqual(job.status, "running");
  assert.strictEqual(job.stage, "init");
  assert.strictEqual(job.cumulative_ingested, 0);
  assert.strictEqual(job.cap_reached, false);
  assert.strictEqual(job.dropped_album_count, 0);
  assert.deepStrictEqual(job.failed_listing_urls, []);
  console.log("✔ createInitialJobState passed");
}

// 2. Resumption Logic (including auth_required and geoblocked)
{
  const runningWithWork = {
    job_id: "j1",
    status: "running",
    stage: "extracting_albums",
    pending_album_urls: ["https://play.kkbox.com/album/1"],
  };
  assert.strictEqual(shouldResumeJob(runningWithWork), true, "Running job with pending albums should resume");

  const pausedBridge = {
    job_id: "j2",
    status: "paused_bridge_offline",
    stage: "crawling_listings",
    pending_listing_urls: ["https://play.kkbox.com/discover/categories/c1/new-releases"],
  };
  assert.strictEqual(shouldResumeJob(pausedBridge), true, "Paused bridge job with pending listings should resume");

  const authPaused = {
    job_id: "j_auth",
    status: "auth_required",
    stage: "extracting_albums",
    pending_album_urls: ["https://play.kkbox.com/album/auth_1"],
  };
  assert.strictEqual(shouldResumeJob(authPaused), true, "Auth required job with pending items should resume after user login");

  const geoblockedPaused = {
    job_id: "j_geo",
    status: "geoblocked",
    stage: "discovering_routes",
    pending_listing_urls: [],
  };
  assert.strictEqual(shouldResumeJob(geoblockedPaused), true, "Geoblocked job at discovering_routes should resume after proxy switch");

  const completedJob = {
    job_id: "j3",
    status: "success",
    stage: "completed",
    pending_album_urls: [],
  };
  assert.strictEqual(shouldResumeJob(completedJob), false, "Completed job should not resume");
  console.log("✔ shouldResumeJob with auth_required and geoblocked resumption passed");
}

// 3. Queue Operations, Deduplication, and Safety Cap Warning Tracking
{
  const job = createInitialJobState();
  const addedListings = addListingUrls(job, [
    "https://play.kkbox.com/discover/new-releases",
    "https://play.kkbox.com/discover/categories/c1/new-releases",
    "https://play.kkbox.com/discover/new-releases", // Duplicate
  ]);
  assert.strictEqual(addedListings, 2, "Should deduplicate listing URLs");
  assert.strictEqual(job.pending_listing_urls.length, 2);

  // Album capping with cap_reached & dropped_album_count tracking
  const addedAlbums = addAlbumUrls(
    job,
    ["https://play.kkbox.com/album/1", "https://play.kkbox.com/album/2", "https://play.kkbox.com/album/3", "https://play.kkbox.com/album/4"],
    2 // cap at 2
  );
  assert.strictEqual(addedAlbums, 2, "Should add 2 albums");
  assert.strictEqual(job.pending_album_urls.length, 2);
  assert.strictEqual(job.cap_reached, true);
  assert.strictEqual(job.dropped_album_count, 2);
  console.log("✔ Queue operations, deduplication & safety cap warning tracking passed");
}

// 3B. Album metadata hints survive checkpoint batching.
{
  const job = createInitialJobState();
  addAlbumCards(job, [{
    url: "https://play.kkbox.com/album/hinted_1",
    title: "新專輯",
    artist: "新樂隊",
  }]);
  const [item] = getNextAlbumBatch(job, 1);
  assert.deepStrictEqual(item, {
    url: "https://play.kkbox.com/album/hinted_1",
    is_retry: false,
    hint: { title: "新專輯", artist: "新樂隊" },
  });
  console.log("✔ Album title/artist hint persistence passed");
}

// 4. Regression Test: Infinite Retry Bug & Work Item Provenance
{
  const job = createInitialJobState();
  job.pending_album_urls = ["https://play.kkbox.com/album/fresh_1"];
  job.retry_album_urls = ["https://play.kkbox.com/album/retry_1"];

  // Batch returns work items with provenance { url, is_retry }
  const batch = getNextAlbumBatch(job, 2);
  assert.strictEqual(batch.length, 2);
  assert.deepStrictEqual(batch[0], { url: "https://play.kkbox.com/album/retry_1", is_retry: true });
  assert.deepStrictEqual(batch[1], { url: "https://play.kkbox.com/album/fresh_1", is_retry: false });

  // 1st failure on fresh_1 -> moves to retry_album_urls
  recordAlbumFailure(job, batch[1].url, batch[1].is_retry);
  assert.strictEqual(job.retry_album_urls.includes("https://play.kkbox.com/album/fresh_1"), true);
  assert.strictEqual(job.failed_album_urls.includes("https://play.kkbox.com/album/fresh_1"), false);

  // 2nd failure on retry_1 -> MUST become permanent failure in failed_album_urls and NOT in retry_album_urls
  recordAlbumFailure(job, batch[0].url, batch[0].is_retry);
  assert.strictEqual(job.failed_album_urls.includes("https://play.kkbox.com/album/retry_1"), true);
  assert.strictEqual(job.retry_album_urls.includes("https://play.kkbox.com/album/retry_1"), false);

  // When next batch is pulled, failed retry is NOT in the queue
  const nextBatch = getNextAlbumBatch(job, 10);
  assert.strictEqual(nextBatch.length, 1);
  assert.strictEqual(nextBatch[0].url, "https://play.kkbox.com/album/fresh_1");
  assert.strictEqual(nextBatch[0].is_retry, true);

  // Failing fresh_1 on its 2nd attempt also moves permanently to failed_album_urls
  recordAlbumFailure(job, nextBatch[0].url, nextBatch[0].is_retry);
  assert.strictEqual(job.failed_album_urls.length, 2);
  assert.strictEqual(job.retry_album_urls.length, 0);
  assert.strictEqual(job.pending_album_urls.length, 0);
  console.log("✔ Regression Test: Work item provenance & terminal retry bounds passed");
}

// 5. Regression Test: Mixed Batch Pause Restoration
{
  const job = createInitialJobState();

  // Simulate a batch of 3 items
  const workItems = [
    { url: "https://play.kkbox.com/album/item_extracted_success", is_retry: false },
    { url: "https://play.kkbox.com/album/item_failed_1st_attempt", is_retry: false },
    { url: "https://play.kkbox.com/album/item_auth_interrupted", is_retry: true },
  ];

  // Item 2 failed 1st attempt before pause -> placed in retry_album_urls
  recordAlbumFailure(job, workItems[1].url, workItems[1].is_retry);
  assert.strictEqual(job.retry_album_urls.includes("https://play.kkbox.com/album/item_failed_1st_attempt"), true);

  // Auth pause occurs: restore workItems
  restoreUnsentBatch(job, workItems);

  // Item 1 (successful but unsent) should be restored to pending_album_urls
  assert.strictEqual(job.pending_album_urls.includes("https://play.kkbox.com/album/item_extracted_success"), true);
  // Item 2 should NOT be duplicated in pending_album_urls because it's already in retry_album_urls
  assert.strictEqual(job.pending_album_urls.includes("https://play.kkbox.com/album/item_failed_1st_attempt"), false);
  assert.strictEqual(job.retry_album_urls.filter(u => u === "https://play.kkbox.com/album/item_failed_1st_attempt").length, 1);
  // Item 3 (interrupted retry item) should be restored to retry_album_urls
  assert.strictEqual(job.retry_album_urls.includes("https://play.kkbox.com/album/item_auth_interrupted"), true);
  console.log("✔ Regression Test: Mixed batch pause restoration passed");
}

// 6. Regression Test: Finalize Job Completion Statuses
{
  // A. Zero album -> no_data
  const emptyJob = createInitialJobState();
  finalizeJobCompletion(emptyJob);
  assert.strictEqual(emptyJob.status, "no_data");

  // B. Clean success
  const cleanJob = createInitialJobState();
  cleanJob.processed_album_urls = ["https://play.kkbox.com/album/1"];
  cleanJob.cumulative_ingested = 10;
  finalizeJobCompletion(cleanJob);
  assert.strictEqual(cleanJob.status, "success");

  // C. Success with permanent album failures
  const albumFailJob = createInitialJobState();
  albumFailJob.processed_album_urls = ["https://play.kkbox.com/album/1"];
  albumFailJob.failed_album_urls = ["https://play.kkbox.com/album/fail_01"];
  albumFailJob.cumulative_ingested = 5;
  finalizeJobCompletion(albumFailJob);
  assert.strictEqual(albumFailJob.status, "success_with_warnings");
  assert.ok(albumFailJob.warnings.some(w => w.includes("专辑多次解析失败")));

  // D. Success with listing failures & cap reached
  const mixedWarnJob = createInitialJobState();
  mixedWarnJob.processed_album_urls = ["https://play.kkbox.com/album/1"];
  mixedWarnJob.failed_listing_urls = ["https://play.kkbox.com/discover/categories/bad_cat"];
  mixedWarnJob.cap_reached = true;
  mixedWarnJob.dropped_album_count = 15;
  mixedWarnJob.cumulative_ingested = 8;
  finalizeJobCompletion(mixedWarnJob);
  assert.strictEqual(mixedWarnJob.status, "success_with_warnings");
  assert.ok(mixedWarnJob.warnings.some(w => w.includes("分类列表加载失败")));
  assert.ok(mixedWarnJob.warnings.some(w => w.includes("500 张上限")));
  console.log("✔ Regression Test: Finalize Job Completion statuses passed");
}

// 7. Symbol Presence & Integrity Check (Preventing Missing Definitions)
{
  assert.strictEqual(typeof getStorage, "function", "getStorage must be exported");
  assert.strictEqual(typeof getStorageData, "function", "getStorageData must be exported");
  assert.strictEqual(typeof saveJobState, "function", "saveJobState must be exported");
  assert.strictEqual(typeof getOrInitJob, "function", "getOrInitJob must be exported");
  console.log("✔ Symbol Presence Check passed (getStorageData, saveJobState, getOrInitJob, getStorage defined)");
}

// 8. Storage Adapter & Job Resumption Async Suite
(async function runStorageAdapterTests() {
  function createMockStorage(initialData = {}) {
    const store = { ...initialData };
    return {
      get(keys, cb) {
        if (!keys) {
          cb({ ...store });
          return;
        }
        if (typeof keys === "string") {
          cb({ [keys]: store[keys] });
          return;
        }
        if (Array.isArray(keys)) {
          const res = {};
          keys.forEach((k) => {
            if (k in store) res[k] = store[k];
          });
          cb(res);
          return;
        }
        cb({ ...store });
      },
      set(obj, cb) {
        Object.assign(store, obj);
        if (cb) cb();
      },
      _raw: store,
    };
  }

  // A. Empty storage read & safe fallback
  {
    const mock = createMockStorage({});
    const data = await getStorageData(null, mock);
    assert.deepStrictEqual(data, {}, "Empty storage should return empty object");

    const noStorageData = await getStorageData(null, null);
    assert.deepStrictEqual(noStorageData, {}, "Null storage area should safely resolve to {}");
  }

  // B. saveJobState persistence & updated_at updating
  {
    const mock = createMockStorage({});
    const job = createInitialJobState("job_save_test");
    const oldUpdatedAt = "2020-01-01T00:00:00.000Z";
    job.updated_at = oldUpdatedAt;

    const saved = await saveJobState(job, mock);
    assert.strictEqual(saved.job_id, "job_save_test");
    assert.notStrictEqual(saved.updated_at, oldUpdatedAt, "saveJobState must update updated_at timestamp");
    assert.deepStrictEqual(mock._raw.current_job.job_id, "job_save_test");
    assert.strictEqual(mock._raw.current_job.updated_at, saved.updated_at);
  }

  // C. getOrInitJob on empty storage -> creates initial job state & saves
  {
    const mock = createMockStorage({});
    const job = await getOrInitJob(mock);
    assert.ok(job.job_id, "New job must have a job_id");
    assert.strictEqual(job.status, "running");
    assert.strictEqual(job.stage, "init");
    assert.strictEqual(job.resumed_count, 0);
    assert.strictEqual(mock._raw.current_job.job_id, job.job_id);
    assert.strictEqual(mock._raw.current_job.resumed_count, 0);
  }

  // D. getOrInitJob on non-resumable completed/no_data job -> creates fresh job state
  {
    const completedJob = createInitialJobState("job_done");
    completedJob.status = "completed";
    completedJob.stage = "completed";
    const mock = createMockStorage({ current_job: completedJob });

    const job = await getOrInitJob(mock);
    assert.notStrictEqual(job.job_id, "job_done", "Completed job must be replaced with fresh job");
    assert.strictEqual(job.status, "running");
    assert.strictEqual(job.stage, "init");
    assert.strictEqual(job.resumed_count, 0);
    assert.strictEqual(mock._raw.current_job.job_id, job.job_id);
  }

  // E. getOrInitJob on resumable job -> resumes, increments resumed_count & sets is_resumed
  {
    const pausedJob = createInitialJobState("job_resumable_1");
    pausedJob.status = "paused_bridge_offline";
    pausedJob.stage = "extracting_albums";
    pausedJob.pending_album_urls = ["https://play.kkbox.com/album/101"];
    pausedJob.resumed_count = 0;
    const mock = createMockStorage({ current_job: pausedJob });

    const job1 = await getOrInitJob(mock);
    assert.strictEqual(job1.job_id, "job_resumable_1");
    assert.strictEqual(job1.resumed_count, 1, "First resume must increment resumed_count to 1");
    assert.strictEqual(job1.is_resumed, true);
    assert.strictEqual(mock._raw.current_job.resumed_count, 1);
    assert.strictEqual(mock._raw.current_job.is_resumed, true);

    // Second resume on same job
    const job2 = await getOrInitJob(mock);
    assert.strictEqual(job2.job_id, "job_resumable_1");
    assert.strictEqual(job2.resumed_count, 2, "Second resume must increment resumed_count to 2");
    assert.strictEqual(mock._raw.current_job.resumed_count, 2);
  }

  // F. Bridge offline startup catch & runCollector uncaught error path simulation
  {
    const activeJob = createInitialJobState("job_bridge_offline");
    activeJob.status = "running";
    activeJob.pending_listing_urls = ["https://play.kkbox.com/discover/new-releases"];
    const mock = createMockStorage({ current_job: activeJob });

    // Simulate startup bridge offline path in background.js
    const storedOffline = await getStorageData(null, mock);
    assert.ok(storedOffline.current_job);
    storedOffline.current_job.status = "paused_bridge_offline";
    storedOffline.current_job.error = "本地 Python 桥接服务离线，已保留进度待恢复";
    await saveJobState(storedOffline.current_job, mock);

    assert.strictEqual(mock._raw.current_job.status, "paused_bridge_offline");
    assert.strictEqual(mock._raw.current_job.error, "本地 Python 桥接服务离线，已保留进度待恢复");

    // Simulate uncaught error catch path in background.js
    const storedError = await getStorageData(null, mock);
    assert.ok(storedError.current_job);
    storedError.current_job.status = "error";
    storedError.current_job.error = "Uncaught test error";
    await saveJobState(storedError.current_job, mock);

    assert.strictEqual(mock._raw.current_job.status, "error");
    assert.strictEqual(mock._raw.current_job.error, "Uncaught test error");
  }

  console.log("✔ Storage Adapter, getOrInitJob, resumption & bridge offline simulation passed");
  console.log("\n[SUCCESS] All KKBOX Job Manager regression tests passed!\n");
})().catch((err) => {
  console.error("[FAIL] Storage adapter test error:", err);
  process.exit(1);
});
