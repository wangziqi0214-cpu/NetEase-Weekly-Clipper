/**
 * KKBOX Web Player Content Script.
 * Runs in play.kkbox.com context to perform DOM settling, incremental infinite scroll,
 * and safe metadata extraction.
 * NEVER inspects or transmits cookies, headers, tokens, or storage.
 */

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clickSemanticLoadMore() {
  const candidates = document.querySelectorAll("button, [role='button'], a");
  for (const el of candidates) {
    const text = (el.innerText || el.textContent || "").trim();
    if (
      text.includes("載入更多") ||
      text.includes("加载更多") ||
      text.includes("查看更多") ||
      text.includes("Load more") ||
      text.includes("Show more")
    ) {
      if (el.offsetParent !== null && typeof el.click === "function") {
        try {
          el.click();
          return true;
        } catch (e) {}
      }
    }
  }
  return false;
}

/**
 * Incrementally scroll and settle dynamic InfiniteLoader listings.
 */
async function settleListingPage(maxRounds = 16, idleRoundsThreshold = 3) {
  let prevCount = 0;
  let idleRounds = 0;

  for (let r = 0; r < maxRounds; r++) {
    // 1. Check if auth or geoblock intercepted
    if (isAuthRequired(document, window.location.href) || isGeoBlocked(document)) {
      break;
    }

    // 2. Count current album links
    const currentAlbumLinks = extractAlbumLinks(document, window.location.origin);
    const currentCount = currentAlbumLinks.length;

    if (currentCount > prevCount) {
      prevCount = currentCount;
      idleRounds = 0;
    } else {
      idleRounds++;
      if (idleRounds >= idleRoundsThreshold && currentCount > 0) {
        // Settled: no new items loaded for several rounds
        break;
      }
    }

    // 3. Trigger semantic load more button if present
    clickSemanticLoadMore();

    // 4. Incremental scroll down
    window.scrollBy(0, 1200);
    await sleep(400);
  }

  // Scroll back to top
  window.scrollTo(0, 0);
  await sleep(200);
}

/**
 * Wait briefly for album details to hydrate.
 */
async function settleAlbumPage(maxWaitMs = 2500) {
  const startTime = Date.now();
  while (Date.now() - startTime < maxWaitMs) {
    if (isAuthRequired(document, window.location.href) || isGeoBlocked(document)) {
      break;
    }
    const songLinks = document.querySelectorAll("a[href*='/track/'], a[href*='/song/']");
    const jsonLd = document.querySelector("script[type='application/ld+json']");
    if (songLinks.length > 0 || jsonLd) {
      break;
    }
    await sleep(250);
  }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  (async () => {
    const url = window.location.href;

    if (isAuthRequired(document, url)) {
      sendResponse({ status: "auth_required", url: url });
      return;
    }

    if (isGeoBlocked(document)) {
      sendResponse({ status: "geoblocked", url: url });
      return;
    }

    if (request.action === "extract_root_routes") {
      // Primary root: discover subroutes + categories + direct album links
      await settleListingPage(10, 2);
      const subroutes = extractNewReleaseSubroutes(document, window.location.origin);
      const albumLinks = extractAlbumLinks(document, window.location.origin);
      const albumCards = extractAlbumCards(document, window.location.origin);
      sendResponse({
        status: "root_discovery",
        subroutes: subroutes,
        album_links: albumLinks,
        album_cards: albumCards,
      });
      return;
    }

    if (request.action === "extract_categories") {
      // Categories root: discover target category new-release subroutes
      await settleListingPage(10, 2);
      const catRoutes = extractTargetCategoryRoutes(document, window.location.origin);
      sendResponse({
        status: "category_discovery",
        category_routes: catRoutes,
      });
      return;
    }

    if (request.action === "extract_listing") {
      // Subroute or Category New-Releases listing
      await settleListingPage(16, 3);
      const albumLinks = extractAlbumLinks(document, window.location.origin);
      const albumCards = extractAlbumCards(document, window.location.origin);
      sendResponse({
        status: "listing_extraction",
        album_links: albumLinks,
        album_cards: albumCards,
      });
      return;
    }

    if (request.action === "extract_album_detail") {
      // Album detail page
      await settleAlbumPage(2500);
      const albumData = extractAlbumDetail(document, url, request.hint || {});
      sendResponse({
        status: "album_detail",
        data: albumData,
      });
      return;
    }

    sendResponse({ status: "unknown_action" });
  })();

  return true; // Keep async message channel open
});
