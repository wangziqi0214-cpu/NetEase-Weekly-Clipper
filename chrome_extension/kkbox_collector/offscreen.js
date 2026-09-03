/**
 * Offscreen keepalive document.
 * Keeps an open runtime Port with periodic heartbeats while a crawl job is active
 * to prevent premature MV3 service worker termination during multi-batch crawling.
 */

let keepAlivePort = null;
let heartbeatTimer = null;

function connectKeepAlive() {
  try {
    keepAlivePort = chrome.runtime.connect({ name: "offscreen-keepalive" });
    keepAlivePort.onDisconnect.addListener(() => {
      keepAlivePort = null;
    });

    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (keepAlivePort) {
        try {
          keepAlivePort.postMessage({ type: "heartbeat", timestamp: Date.now() });
        } catch (e) {}
      }
    }, 20000);
  } catch (err) {
    console.warn("[Offscreen] Keepalive connection error:", err);
  }
}

connectKeepAlive();
