/**
 * Regression tests for KKBOX Session Sidecar polling, command claim, and heartbeat schema
 */

const assert = require("assert");

console.log("[TEST] Running KKBOX Sidecar polling & heartbeat schema tests...");

function formatHeartbeatPayload(status, job, activeCommandId) {
  return {
    sidecar_version: "1.3.2",
    status: status || "idle",
    current_job_id: job ? job.job_id : null,
    active_command_id: activeCommandId || null,
    cumulative_ingested: job ? job.cumulative_ingested : 0,
    error: job ? job.error : null,
  };
}

function formatReportPayload(commandId, status, progress, error) {
  return {
    command_id: commandId,
    status: status,
    progress: progress || null,
    error: error || null,
  };
}

// 1. Heartbeat payload schema validation
const sampleJob = {
  job_id: "kk_job_123",
  status: "running",
  cumulative_ingested: 42,
  error: null,
};
const hb = formatHeartbeatPayload("crawling", sampleJob, "cmd_999");
assert.strictEqual(hb.sidecar_version, "1.3.2");
assert.strictEqual(hb.status, "crawling");
assert.strictEqual(hb.current_job_id, "kk_job_123");
assert.strictEqual(hb.active_command_id, "cmd_999");
assert.strictEqual(hb.cumulative_ingested, 42);
assert.strictEqual(hb.error, null);
console.log("✔ formatHeartbeatPayload passed");

// 2. Command report payload schema validation
const report = formatReportPayload("cmd_999", "completed", { ingested: 42, warnings: 0 });
assert.strictEqual(report.command_id, "cmd_999");
assert.strictEqual(report.status, "completed");
assert.strictEqual(report.progress.ingested, 42);
console.log("✔ formatReportPayload passed");

// 3. Command claim processing logic
function processClaimResponse(claimResp) {
  if (!claimResp || !claimResp.command || !claimResp.command.command_id) {
    return { shouldStart: false, commandId: null };
  }
  return {
    shouldStart: claimResp.command.action === "collect_kkbox",
    commandId: claimResp.command.command_id,
  };
}

const noCmd = processClaimResponse({ status: "ok", command: null });
assert.strictEqual(noCmd.shouldStart, false);

const validCmd = processClaimResponse({
  status: "ok",
  command: { command_id: "cmd_abc", action: "collect_kkbox" },
});
assert.strictEqual(validCmd.shouldStart, true);
assert.strictEqual(validCmd.commandId, "cmd_abc");
console.log("✔ processClaimResponse passed");

console.log("\n[SUCCESS] All KKBOX Sidecar polling schema tests passed!");
