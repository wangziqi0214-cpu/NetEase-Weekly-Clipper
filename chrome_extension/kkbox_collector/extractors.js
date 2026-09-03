/**
 * KKBOX Web Player (play.kkbox.com) Extraction Helpers.
 * Utilizes verified SPA route patterns (/discover/new-releases, /discover/categories),
 * semantic HTML, Meta/OpenGraph tags, JSON-LD, and keyword matching.
 * NEVER extracts cookies, auth headers, tokens, localStorage, or sessionStorage.
 */

const TARGET_CHINESE_KEYWORDS = [
  "華語", "华语", "粵語", "粤语", "獨立", "独立", "搖滾", "摇滚",
  "Indie", "Rock", "Mandarin", "Cantonese"
];

const NON_TARGET_KEYWORDS = [
  "西洋", "日語", "日语", "韓語", "韩语", "J-Pop", "K-Pop", "Western", "古典", "爵士", "兒童", "儿童"
];

const ALBUM_URL_PATTERN = /\/album\/([a-zA-Z0-9_-]+)/;
const TRACK_URL_PATTERN = /\/(?:track|song)\/([a-zA-Z0-9_-]+)/;
const SONG_URL_PATTERN = TRACK_URL_PATTERN; // Backward compatibility
const CHART_URL_PATTERN = /(\/charts|\/ranking|\/top|kma\.kkbox\.com|charts\/api|billboard|hit-fm|榜)/i;

function isGenericPlayerTitle(value) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  return !normalized || normalized === "kkbox" || normalized === "kkbox web player" || normalized === "let's music - kkbox";
}

/**
 * Check if the page indicates authentication is required (redirect to /login or login prompt).
 * @param {Document|string} docOrHtml
 * @param {string} currentUrl
 * @returns {boolean}
 */
function isAuthRequired(docOrHtml, currentUrl = "") {
  if (currentUrl && (currentUrl.includes("/login") || currentUrl.includes("redirect="))) {
    return true;
  }
  const text = typeof docOrHtml === "string" ? docOrHtml : (docOrHtml.body ? docOrHtml.body.innerText || "" : "");
  if (text.includes("登入 KKBOX") || text.includes("立即登入") || text.includes("Log in to KKBOX")) {
    return true;
  }
  if (typeof docOrHtml !== "string" && docOrHtml.querySelector) {
    if (docOrHtml.querySelector("a[href*='/login'], button[data-testid='login-button']")) {
      // If the main view is blocked by login redirect or login container
      if (docOrHtml.querySelector("form[action*='login'], div[class*='login'], div[class*='Login']")) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Check if the page indicates region / geo-blocking.
 * @param {Document|string} docOrHtml
 * @returns {boolean}
 */
function isGeoBlocked(docOrHtml) {
  const text = typeof docOrHtml === "string" ? docOrHtml : (docOrHtml.body ? docOrHtml.body.innerText || "" : "");
  const patterns = [
    "地區限制", "地区限制", "地區不支援", "地区不支持",
    "Not available in your country", "Not available in your region",
    "服務僅提供", "服务仅提供", "服務未在您所在的地區", "服务未在您所在的地区"
  ];
  return patterns.some(p => text.includes(p));
}

/**
 * Check if a URL is a chart/ranking route (charts must NEVER be followed for new releases).
 * @param {string} url
 * @returns {boolean}
 */
function isChartUrl(url) {
  if (!url) return false;
  return CHART_URL_PATTERN.test(url);
}

/**
 * Discover subroutes from play.kkbox.com/discover/new-releases available_types subnavigation.
 * E.g. /discover/new-releases/mandarin, /discover/new-releases/cantonese, /discover/new-releases/indie
 * @param {Document|string} docOrHtml
 * @param {string} baseUrl
 * @returns {string[]} Unique subroute URLs
 */
function extractNewReleaseSubroutes(docOrHtml, baseUrl = "https://play.kkbox.com") {
  const subroutes = new Set();
  const pattern = /\/discover\/new-releases\/([a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)?)/gi;

  if (typeof docOrHtml === "string") {
    let match;
    while ((match = pattern.exec(docOrHtml)) !== null) {
      const path = match[0];
      if (!isChartUrl(path)) {
        try {
          subroutes.add(new URL(path, baseUrl).href);
        } catch (e) {}
      }
    }
    return Array.from(subroutes);
  }

  const links = docOrHtml.querySelectorAll("a[href*='/discover/new-releases/']");
  for (const a of links) {
    const href = a.getAttribute("href") || "";
    if (href && !isChartUrl(href)) {
      try {
        subroutes.add(new URL(href, baseUrl).href);
      } catch (e) {}
    }
  }

  return Array.from(subroutes);
}

/**
 * Discover target category new-release routes from play.kkbox.com/discover/categories.
 * Filters by high-recall Chinese keywords for 华语/粤语/独立/摇滚 and excludes charts.
 * @param {Document|string} docOrHtml
 * @param {string} baseUrl
 * @returns {string[]} Target category /new-releases URLs
 */
function extractTargetCategoryRoutes(docOrHtml, baseUrl = "https://play.kkbox.com") {
  const categoryRoutes = new Set();

  if (typeof docOrHtml === "string") {
    // Regex parsing for string HTML (Node.js test friendly)
    const catRegex = /href=["']([^"']*\/discover\/categories\/([a-zA-Z0-9_-]+)[^"']*)["'][^>]*>([\s\S]*?)<\/a>/gi;
    let match;
    while ((match = catRegex.exec(docOrHtml)) !== null) {
      const href = match[1];
      const catId = match[2];
      const innerText = match[3].replace(/<[^>]+>/g, " ").trim();

      if (isChartUrl(href) || isChartUrl(innerText)) continue;

      const isTarget = TARGET_CHINESE_KEYWORDS.some(kw => innerText.includes(kw));
      const isNonTarget = NON_TARGET_KEYWORDS.some(kw => innerText.includes(kw));

      if (isTarget && !isNonTarget) {
        try {
          const catNewReleasesUrl = new URL(`/discover/categories/${catId}/new-releases`, baseUrl).href;
          categoryRoutes.add(catNewReleasesUrl);
        } catch (e) {}
      }
    }
    return Array.from(categoryRoutes);
  }

  // DOM based parsing
  const links = docOrHtml.querySelectorAll("a[href*='/discover/categories/']");
  for (const a of links) {
    const href = a.getAttribute("href") || "";
    if (!href || isChartUrl(href)) continue;

    const visibleText = (a.innerText || a.textContent || "").trim();
    const titleAttr = (a.getAttribute("title") || "").trim();
    const ariaLabel = (a.getAttribute("aria-label") || "").trim();
    const combinedDesc = `${visibleText} ${titleAttr} ${ariaLabel}`;

    if (isChartUrl(combinedDesc)) continue;

    const isTarget = TARGET_CHINESE_KEYWORDS.some(kw => combinedDesc.includes(kw));
    const isNonTarget = NON_TARGET_KEYWORDS.some(kw => combinedDesc.includes(kw));

    if (isTarget && !isNonTarget) {
      const match = href.match(/\/discover\/categories\/([a-zA-Z0-9_-]+)/);
      if (match) {
        const catId = match[1];
        try {
          const catNewReleasesUrl = new URL(`/discover/categories/${catId}/new-releases`, baseUrl).href;
          categoryRoutes.add(catNewReleasesUrl);
        } catch (e) {}
      }
    }
  }

  return Array.from(categoryRoutes);
}

/**
 * Discover KKBOX album links from a listing / new-releases page.
 * Strictly excludes chart links.
 * @param {Document|string} docOrHtml
 * @param {string} baseUrl
 * @returns {string[]} Array of unique album URLs
 */
function extractAlbumLinks(docOrHtml, baseUrl = "https://play.kkbox.com") {
  return extractAlbumCards(docOrHtml, baseUrl).map((card) => card.url);
}

/**
 * Extract album URLs together with the title/artist already rendered on each
 * new-release card. Current KKBOX cards use hashed CSS-module classes whose
 * stable portions are `_title_` and `_subtitle_`.
 */
function extractAlbumCards(docOrHtml, baseUrl = "https://play.kkbox.com") {
  const cardsByUrl = new Map();

  const addCard = (href, title = "", artist = "") => {
    if (!href || isChartUrl(href)) return;
    const match = href.match(ALBUM_URL_PATTERN);
    if (!match) return;
    try {
      const url = new URL(`/album/${match[1]}`, baseUrl).href;
      const existing = cardsByUrl.get(url) || { url, title: "", artist: "" };
      if (!existing.title && title) existing.title = String(title).trim();
      if (!existing.artist && artist) existing.artist = String(artist).trim();
      cardsByUrl.set(url, existing);
    } catch (e) {}
  };

  if (typeof docOrHtml === "string") {
    const anchorRegex = /<a\b([^>]*href=["']([^"']*\/album\/[a-zA-Z0-9_-]+[^"']*)["'][^>]*)>([\s\S]*?)<\/a>/gi;
    let match;
    while ((match = anchorRegex.exec(docOrHtml)) !== null) {
      const attrs = match[1] || "";
      const inner = match[3] || "";
      const titleMatch = inner.match(/<[^>]*class=["'][^"']*(?:_title_|\btitle\b)[^"']*["'][^>]*>([\s\S]*?)<\//i);
      const artistMatch = inner.match(/<[^>]*class=["'][^"']*(?:_subtitle_|\bsubtitle\b|artist)[^"']*["'][^>]*>([\s\S]*?)<\//i);
      const attrTitle = attrs.match(/(?:title|aria-label)=["']([^"']+)["']/i);
      const clean = (value) => String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      addCard(match[2], clean(titleMatch && titleMatch[1]) || clean(attrTitle && attrTitle[1]), clean(artistMatch && artistMatch[1]));
    }
    return Array.from(cardsByUrl.values());
  }

  for (const anchor of docOrHtml.querySelectorAll("a[href*='/album/']")) {
    const href = anchor.getAttribute("href") || "";
    const titleEl = anchor.querySelector(
      "p[class*='_title_'], [data-testid*='title'], [class*='album-title']"
    );
    const artistEl = anchor.querySelector(
      "p[class*='_subtitle_'], [data-testid*='artist'], [class*='album-artist']"
    );
    addCard(
      href,
      (titleEl && (titleEl.innerText || titleEl.textContent)) || anchor.getAttribute("title") || "",
      (artistEl && (artistEl.innerText || artistEl.textContent)) || ""
    );
  }
  return Array.from(cardsByUrl.values());
}

function legacyExtractAlbumLinks(docOrHtml, baseUrl = "https://play.kkbox.com") {
  const albumUrls = new Set();

  if (typeof docOrHtml === "string") {
    const hrefRegex = /href=["']([^"']+)["']/gi;
    let match;
    while ((match = hrefRegex.exec(docOrHtml)) !== null) {
      const href = match[1];
      if (isChartUrl(href)) continue;
      const albMatch = href.match(ALBUM_URL_PATTERN);
      if (albMatch) {
        try {
          const cleanPath = `/album/${albMatch[1]}`;
          const absUrl = new URL(cleanPath, baseUrl).href;
          albumUrls.add(absUrl);
        } catch (e) {}
      }
    }
    return Array.from(albumUrls);
  }

  const links = docOrHtml.querySelectorAll("a[href]");
  for (const a of links) {
    const href = a.getAttribute("href") || "";
    if (!href || isChartUrl(href)) continue;

    const albMatch = href.match(ALBUM_URL_PATTERN);
    if (albMatch) {
      try {
        const cleanPath = `/album/${albMatch[1]}`;
        const absUrl = new URL(cleanPath, baseUrl).href;
        albumUrls.add(absUrl);
      } catch (e) {}
    }
  }

  return Array.from(albumUrls);
}

/**
 * Extract full album metadata and ordered track list from an album page.
 * Conservative release-type inference: 1 track => single; explicit Single/EP tags => single/ep; otherwise album.
 * Release date is preserved if available; if missing, returns empty string "" (never today).
 * @param {Document|string} docOrHtml
 * @param {string} pageUrl
 * @returns {object|null} Release object
 */
function extractAlbumDetail(docOrHtml, pageUrl, listingHint = {}) {
  const urlMatch = pageUrl.match(ALBUM_URL_PATTERN);
  const albumId = urlMatch ? urlMatch[1] : "";

  let title = "";
  let artistName = "";
  let releaseDate = "";
  let explicitTypeMarker = "";
  const tracks = [];

  if (typeof docOrHtml === "string") {
    // 1. JSON-LD check
    const jsonLdMatch = docOrHtml.match(/<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/i);
    if (jsonLdMatch) {
      try {
        const data = JSON.parse(jsonLdMatch[1]);
        if (data["@type"] === "MusicAlbum" || data["@type"] === "Album" || data["@type"] === "MusicRelease") {
          title = data.name || "";
          artistName = (data.byArtist && data.byArtist.name) || (data.author && data.author.name) || "";
          releaseDate = data.datePublished || data.dateCreated || "";
          if (Array.isArray(data.track || data.tracks)) {
            const trks = data.track || data.tracks;
            trks.forEach((t, idx) => {
              const tidMatch = (t.url || "").match(TRACK_URL_PATTERN);
              tracks.push({
                source_id: tidMatch ? tidMatch[1] : `trk_${idx + 1}`,
                title: t.name || `曲目 ${idx + 1}`,
                track_number: idx + 1,
                duration_ms: t.duration ? parseIsoDuration(t.duration) : null,
                source_url: t.url || "",
              });
            });
          }
        }
      } catch (e) {}
    }

    // 2. OpenGraph / Meta tags
    if (!title) {
      const ogTitle = docOrHtml.match(/<meta[^>]*property=["']og:title["'][^>]*content=["']([^"']*)["']/i);
      if (ogTitle) title = ogTitle[1].replace(/ - KKBOX.*$/i, "").trim();
    }
    if (isGenericPlayerTitle(title)) {
      const titleTag = docOrHtml.match(/<title>([^<]*)<\/title>/i);
      if (titleTag) title = titleTag[1].replace(/ - KKBOX.*$/i, "").trim();
    }

    if (!artistName) {
      const ogArtist = docOrHtml.match(/<meta[^>]*property=["'](?:music:musician|author)["'][^>]*content=["']([^"']*)["']/i);
      if (ogArtist) artistName = ogArtist[1].trim();
    }

    if (!releaseDate) {
      const ogDate = docOrHtml.match(/<meta[^>]*property=["'](?:music:release_date|release_date)["'][^>]*content=["']([^"']*)["']/i);
      if (ogDate) releaseDate = ogDate[1].trim();
    }

    // Fallback track links in string
    if (tracks.length === 0) {
      const trackHrefRegex = /<a\s+[^>]*href=["']([^"']*\/(?:track|song)\/([a-zA-Z0-9_-]+)[^"']*)["'][^>]*>([\s\S]*?)<\/a>/gi;
      let sMatch;
      let idx = 1;
      const seenIds = new Set();
      while ((sMatch = trackHrefRegex.exec(docOrHtml)) !== null) {
        const sUrl = sMatch[1];
        const sId = sMatch[2];
        const sTitle = sMatch[3].replace(/<[^>]+>/g, " ").trim();
        if (sId && !seenIds.has(sId) && sTitle) {
          seenIds.add(sId);
          tracks.push({
            source_id: sId,
            title: sTitle,
            track_number: idx++,
            duration_ms: null,
            source_url: sUrl.startsWith("http") ? sUrl : `https://play.kkbox.com${sUrl}`,
          });
        }
      }
    }
  } else {
    // DOM based extraction
    // 1. JSON-LD check
    const jsonLdScripts = docOrHtml.querySelectorAll("script[type='application/ld+json']");
    for (const script of jsonLdScripts) {
      try {
        const data = JSON.parse(script.textContent || "{}");
        if (data["@type"] === "MusicAlbum" || data["@type"] === "Album" || data["@type"] === "MusicRelease") {
          title = data.name || title;
          artistName = (data.byArtist && data.byArtist.name) || (data.author && data.author.name) || artistName;
          releaseDate = data.datePublished || data.dateCreated || releaseDate;
          if (Array.isArray(data.track || data.tracks)) {
            const trks = data.track || data.tracks;
            trks.forEach((t, idx) => {
              const tidMatch = (t.url || "").match(TRACK_URL_PATTERN);
              tracks.push({
                source_id: tidMatch ? tidMatch[1] : `trk_${idx + 1}`,
                title: t.name || `曲目 ${idx + 1}`,
                track_number: idx + 1,
                duration_ms: t.duration ? parseIsoDuration(t.duration) : null,
                source_url: t.url || "",
              });
            });
          }
        }
      } catch (e) {}
    }

    // 2. OpenGraph / Meta tags
    if (!title) {
      const ogTitle = docOrHtml.querySelector("meta[property='og:title']");
      if (ogTitle) title = (ogTitle.getAttribute("content") || "").replace(/ - KKBOX.*$/i, "").trim();
    }
    if (isGenericPlayerTitle(title)) {
      title = (docOrHtml.title || "").replace(/ - KKBOX.*$/i, "").trim();
    }

    if (!artistName) {
      const ogArtist = docOrHtml.querySelector("meta[property='music:musician'], meta[name='author']");
      if (ogArtist) artistName = ogArtist.getAttribute("content") || "";
    }
    if (!artistName) {
      const creator = docOrHtml.querySelector(
        "[class*='_creator_'] a[href*='/artist/'], [class*='_creator_'], [data-testid*='artist']"
      );
      if (creator) artistName = (creator.innerText || creator.textContent || "").trim();
    }

    if (!releaseDate) {
      const ogDate = docOrHtml.querySelector("meta[property='music:release_date'], meta[name='release_date']");
      if (ogDate) releaseDate = ogDate.getAttribute("content") || "";
    }

    // Semantic album tags / badges
    const badgeElements = docOrHtml.querySelectorAll("[class*='badge'], [class*='type'], [class*='tag'], span, div");
    for (const el of badgeElements) {
      const txt = (el.innerText || el.textContent || "").trim();
      if (txt === "單曲" || txt === "单曲" || txt === "Single") {
        explicitTypeMarker = "single";
        break;
      } else if (txt === "EP" || txt === "迷你專輯" || txt === "迷你专辑") {
        explicitTypeMarker = "ep";
        break;
      }
    }

    // 3. Track links from DOM
    if (tracks.length === 0) {
      const songLinks = docOrHtml.querySelectorAll("a[href*='/track/'], a[href*='/song/']");
      const seenIds = new Set();
      let idx = 1;
      for (const a of songLinks) {
        const href = a.getAttribute("href") || "";
        const sMatch = href.match(TRACK_URL_PATTERN);
        if (!sMatch) continue;
        const sId = sMatch[1];
        const row = typeof a.closest === "function" ? a.closest("tr") : null;
        const titleEl = row && row.querySelector(
          "[class*='track-name'] a[href*='/track/'], [class*='track-name'] a[href*='/song/'], [class*='track-name']"
        );
        const artistEl = row && row.querySelector(
          "[class*='artist-name'] a, [class*='artist-name'], a[href*='/artist/']"
        );
        const sTitle = ((titleEl && (titleEl.innerText || titleEl.textContent)) || a.innerText || a.textContent || "").trim();
        const trackArtist = ((artistEl && (artistEl.innerText || artistEl.textContent)) || artistName || listingHint.artist || "").trim();
        if (sId && !seenIds.has(sId) && sTitle) {
          seenIds.add(sId);
          tracks.push({
            source_id: sId,
            title: sTitle,
            track_number: idx++,
            duration_ms: null,
            source_url: a.href || (href.startsWith("http") ? href : `https://play.kkbox.com${href}`),
            artists: trackArtist ? [{ name: trackArtist }] : [],
          });
        }
      }
    }
  }

  // The new-release listing is currently more reliable than album OpenGraph
  // metadata. Use its structured card values only when album-page extraction
  // did not produce the field.
  if (isGenericPlayerTitle(title) && listingHint && listingHint.title) title = String(listingHint.title).trim();
  if (!artistName && listingHint && listingHint.artist) artistName = String(listingHint.artist).trim();

  // Conservative release type inference
  let releaseType = "album";
  if (tracks.length === 1) {
    releaseType = "single";
  } else if (
    explicitTypeMarker === "single" ||
    (title && (/\b(Single|Maxi Single)\b/i.test(title) || title.includes("单曲") || title.includes("單曲")))
  ) {
    releaseType = "single";
  } else if (
    explicitTypeMarker === "ep" ||
    (title && (/\bEP\b/i.test(title) || title.includes("迷你專輯") || title.includes("迷你专辑")))
  ) {
    releaseType = "ep";
  } else {
    releaseType = "album";
  }

  return {
    platform: "kkbox",
    source_id: albumId,
    title: isGenericPlayerTitle(title) ? `KKBOX Album ${albumId}` : title,
    artists: [{ name: artistName || "KKBOX Artist" }],
    album_title: isGenericPlayerTitle(title) ? `KKBOX Album ${albumId}` : title,
    release_type: releaseType,
    release_date: releaseDate || "",
    track_count: tracks.length,
    source_url: pageUrl,
    tracks: tracks,
  };
}

function parseIsoDuration(isoDuration) {
  if (!isoDuration || typeof isoDuration !== "string") return null;
  const match = isoDuration.match(/PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?/i);
  if (!match) return null;
  const mins = parseInt(match[1] || "0", 10);
  const secs = parseFloat(match[2] || "0");
  return Math.round((mins * 60 + secs) * 1000);
}

// Export for Node.js test environment or Browser Content Script
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    isAuthRequired,
    isGeoBlocked,
    isChartUrl,
    extractNewReleaseSubroutes,
    extractTargetCategoryRoutes,
    extractAlbumCards,
    extractAlbumLinks,
    extractAlbumDetail,
    parseIsoDuration,
    TARGET_CHINESE_KEYWORDS,
    NON_TARGET_KEYWORDS,
    TRACK_URL_PATTERN,
    SONG_URL_PATTERN,
  };
}
