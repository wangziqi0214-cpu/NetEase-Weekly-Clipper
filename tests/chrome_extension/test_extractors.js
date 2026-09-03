/**
 * Pure Node.js unit tests for KKBOX Web Player (play.kkbox.com) extractors.
 * Covers current /track/<id> schema, legacy /song/<id> backward compatibility,
 * JSON-LD, string regex extraction, and DOM extraction.
 * Run with: node tests/chrome_extension/test_extractors.js
 */

const assert = require("assert");
const {
  isAuthRequired,
  isGeoBlocked,
  isChartUrl,
  extractNewReleaseSubroutes,
  extractTargetCategoryRoutes,
  extractAlbumCards,
  extractAlbumLinks,
  extractAlbumDetail,
  parseIsoDuration,
  TRACK_URL_PATTERN,
  SONG_URL_PATTERN,
} = require("../../chrome_extension/kkbox_collector/extractors.js");

console.log("[TEST] Running KKBOX Chrome Extension Web Player Extractor tests...");

// 1. Test Auth Required & Login Redirect Detection
{
  assert.strictEqual(
    isAuthRequired("", "https://play.kkbox.com/login?redirect=%2Fdiscover%2Ffeatured"),
    true,
    "Should detect /login redirect URL"
  );
  assert.strictEqual(
    isAuthRequired("<html><body><div>请先登入 KKBOX 畅听百万歌曲</div></body></html>", "https://play.kkbox.com/discover/featured"),
    true,
    "Should detect login prompt in body text"
  );
  assert.strictEqual(
    isAuthRequired("<html><body><div>最新發行華語專輯清單</div></body></html>", "https://play.kkbox.com/discover/new-releases"),
    false,
    "Authenticated normal page should not be auth_required"
  );
  console.log("✔ Auth Required & Login Redirect detection passed");
}

// 2. Test Geo-blocking detection
{
  const normalHtml = "<html><body><div>最新發行華語專輯清單</div></body></html>";
  const blockedHtml1 = "<html><body><div>抱歉，本服務僅提供台灣及香港地區使用 (地區限制)</div></body></html>";
  const blockedHtml2 = "<html><body><div>Not available in your country or region</div></body></html>";

  assert.strictEqual(isGeoBlocked(normalHtml), false, "Normal page should not be geoblocked");
  assert.strictEqual(isGeoBlocked(blockedHtml1), true, "Chinese geoblock pattern should be detected");
  assert.strictEqual(isGeoBlocked(blockedHtml2), true, "English geoblock pattern should be detected");
  console.log("✔ Geo-blocking detection passed");
}

// 3. Test Chart URL filtering (charts must NEVER enter new releases ingestion)
{
  assert.strictEqual(isChartUrl("https://kma.kkbox.com/charts/daily"), true);
  assert.strictEqual(isChartUrl("https://play.kkbox.com/charts/hot"), true);
  assert.strictEqual(isChartUrl("/discover/rankings"), true);
  assert.strictEqual(isChartUrl("https://play.kkbox.com/discover/new-releases/mandarin"), false);
  assert.strictEqual(isChartUrl("https://play.kkbox.com/album/alb_12345"), false);
  console.log("✔ Chart URL exclusion passed");
}

// 4. Test New Releases Subroutes discovery
{
  const newReleasesHtml = `
    <html>
      <body>
        <nav class="sub-nav">
          <a href="/discover/new-releases/mandarin">華語</a>
          <a href="/discover/new-releases/cantonese">粵語</a>
          <a href="/discover/new-releases/indie/rock">獨立搖滾</a>
          <a href="/charts/newrelease">新歌榜（應忽略）</a>
        </nav>
      </body>
    </html>
  `;

  const subroutes = extractNewReleaseSubroutes(newReleasesHtml, "https://play.kkbox.com");
  assert.strictEqual(subroutes.length, 3, "Should discover 3 valid new-release subroutes");
  assert.ok(subroutes.some(u => u.includes("/discover/new-releases/mandarin")));
  assert.ok(subroutes.some(u => u.includes("/discover/new-releases/cantonese")));
  assert.ok(subroutes.some(u => u.includes("/discover/new-releases/indie/rock")));
  assert.ok(!subroutes.some(u => u.includes("/charts")), "Should not include chart subroutes");
  console.log("✔ New Releases Subroutes discovery passed");
}

// 5. Test Target Categories Route discovery
{
  const categoriesHtml = `
    <html>
      <body>
        <div class="category-grid">
          <a href="/discover/categories/cat_mandarin">華語流行</a>
          <a href="/discover/categories/cat_canton">粵語經典</a>
          <a href="/discover/categories/cat_indie">獨立/Indie Rock</a>
          <a href="/discover/categories/cat_western">西洋流行（應忽略）</a>
          <a href="/discover/categories/cat_jpop">日語J-Pop（應忽略）</a>
          <a href="/discover/categories/cat_charts">排行榜（應忽略）</a>
        </div>
      </body>
    </html>
  `;

  const catRoutes = extractTargetCategoryRoutes(categoriesHtml, "https://play.kkbox.com");
  assert.strictEqual(catRoutes.length, 3, "Should extract 3 target category routes");
  assert.ok(catRoutes.some(u => u.includes("/discover/categories/cat_mandarin/new-releases")));
  assert.ok(catRoutes.some(u => u.includes("/discover/categories/cat_canton/new-releases")));
  assert.ok(catRoutes.some(u => u.includes("/discover/categories/cat_indie/new-releases")));
  assert.ok(!catRoutes.some(u => u.includes("western")));
  assert.ok(!catRoutes.some(u => u.includes("jpop")));
  console.log("✔ Target Category Routes discovery passed");
}

// 6. Test Album Links extraction (with both /track/ and /song/ single links ignored)
{
  const listingHtml = `
    <html>
      <body>
        <div class="album-grid">
          <a href="/album/alb_tw_001" title="夏夜大碟">夏夜大碟</a>
          <a href="https://play.kkbox.com/album/alb_tw_002" aria-label="風聲EP">風聲EP</a>
          <a href="/track/track_001">單曲鏈接（列表頁忽略）</a>
          <a href="/song/song_001">舊單曲鏈接（列表頁忽略）</a>
          <a href="/album/alb_tw_001">重複鏈接</a>
          <a href="/charts/album/alb_003">榜單鏈接（應忽略）</a>
        </div>
      </body>
    </html>
  `;

  const links = extractAlbumLinks(listingHtml, "https://play.kkbox.com");
  assert.strictEqual(links.length, 2, "Should extract 2 unique album URLs");
  assert.ok(links.some(l => l.includes("alb_tw_001")));
  assert.ok(links.some(l => l.includes("alb_tw_002")));
  console.log("✔ Album Links extraction passed");
}

// 6B. Current KKBOX card markup carries both album title and artist.
{
  const listingHtml = `
    <a class="card" href="/album/OpjeJjIQB1A89N0NB8">
      <p class="_title_wtztu_28">Kay Tse I</p>
      <p class="_subtitle_wtztu_42">謝安琪 (Kay Tse)</p>
    </a>`;
  const cards = extractAlbumCards(listingHtml, "https://play.kkbox.com");
  assert.deepStrictEqual(cards, [{
    url: "https://play.kkbox.com/album/OpjeJjIQB1A89N0NB8",
    title: "Kay Tse I",
    artist: "謝安琪 (Kay Tse)",
  }]);
  console.log("✔ Current KKBOX album-card title/artist extraction passed");
}

// 7. Test URL Pattern matching (/track/ & /song/)
{
  assert.ok(TRACK_URL_PATTERN.test("/track/trk_12345"));
  assert.ok(TRACK_URL_PATTERN.test("https://play.kkbox.com/track/trk-abc_99"));
  assert.ok(TRACK_URL_PATTERN.test("/song/song_12345"));
  assert.ok(TRACK_URL_PATTERN.test("https://play.kkbox.com/song/song-xyz"));

  const m1 = "/track/trk_live_01".match(TRACK_URL_PATTERN);
  assert.strictEqual(m1[1], "trk_live_01");

  const m2 = "/song/legacy_02".match(SONG_URL_PATTERN);
  assert.strictEqual(m2[1], "legacy_02");
  console.log("✔ TRACK_URL_PATTERN & SONG_URL_PATTERN regex validation passed");
}

// 8. Test Current Real /track/ Structure in JSON-LD
{
  const albumHtml = `
    <!DOCTYPE html>
    <html>
      <head>
        <title>夏夜風聲 - 刺蝟樂隊 - KKBOX</title>
        <meta property="og:title" content="夏夜風聲 - 刺蝟樂隊 - KKBOX">
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "MusicAlbum",
          "name": "夏夜風聲",
          "byArtist": {
            "@type": "MusicGroup",
            "name": "刺蝟樂隊"
          },
          "datePublished": "2026-08-01",
          "track": [
            {
              "@type": "MusicRecording",
              "name": "夏夜序曲",
              "duration": "PT3M30S",
              "url": "https://play.kkbox.com/track/trk_real_101"
            },
            {
              "@type": "MusicRecording",
              "name": "風聲呼嘯",
              "duration": "PT4M15S",
              "url": "https://play.kkbox.com/track/trk_real_102"
            },
            {
              "@type": "MusicRecording",
              "name": "破曉之光",
              "duration": "PT5M00S",
              "url": "https://play.kkbox.com/track/trk_real_103"
            }
          ]
        }
        </script>
      </head>
      <body></body>
    </html>
  `;

  const album = extractAlbumDetail(albumHtml, "https://play.kkbox.com/album/alb_9988");
  assert.strictEqual(album.source_id, "alb_9988");
  assert.strictEqual(album.title, "夏夜風聲");
  assert.strictEqual(album.artists[0].name, "刺蝟樂隊");
  assert.strictEqual(album.release_date, "2026-08-01");
  assert.strictEqual(album.release_type, "album", "3-track without marker must default to album");
  assert.strictEqual(album.tracks.length, 3);

  // Validate track ordering, source_id, title, duration, and URL
  assert.deepStrictEqual(album.tracks[0], {
    source_id: "trk_real_101",
    title: "夏夜序曲",
    track_number: 1,
    duration_ms: 210000,
    source_url: "https://play.kkbox.com/track/trk_real_101",
  });
  assert.deepStrictEqual(album.tracks[1], {
    source_id: "trk_real_102",
    title: "風聲呼嘯",
    track_number: 2,
    duration_ms: 255000,
    source_url: "https://play.kkbox.com/track/trk_real_102",
  });
  assert.deepStrictEqual(album.tracks[2], {
    source_id: "trk_real_103",
    title: "破曉之光",
    track_number: 3,
    duration_ms: 300000,
    source_url: "https://play.kkbox.com/track/trk_real_103",
  });
  console.log("✔ Real /track/ JSON-LD extraction, track order & properties passed");
}

// 9. Test String HTML Fallback with /track/ and Nested Tags
{
  // A. Single Track
  const singleHtml = `
    <!DOCTYPE html>
    <html>
      <head>
        <title>孤獨朋克 - 新褲子 - KKBOX</title>
        <meta property="og:title" content="孤獨朋克 - 新褲子 - KKBOX">
        <meta property="music:musician" content="新褲子">
      </head>
      <body>
        <div class="track-list">
          <a class="track-row" href="/track/trk_single_001">
            <span class="track-num">1</span>
            <span class="track-name">孤獨朋克</span>
          </a>
        </div>
      </body>
    </html>
  `;

  const single = extractAlbumDetail(singleHtml, "https://play.kkbox.com/album/alb_single_01");
  assert.strictEqual(single.release_type, "single", "1 track must infer single");
  assert.strictEqual(single.release_date, "", "Missing release date must be empty string");
  assert.strictEqual(single.tracks.length, 1);
  assert.strictEqual(single.tracks[0].source_id, "trk_single_001");
  assert.ok(single.tracks[0].title.includes("孤獨朋克"));
  assert.strictEqual(single.tracks[0].track_number, 1);
  assert.strictEqual(single.tracks[0].source_url, "https://play.kkbox.com/track/trk_single_001");

  // B. 2-track EP with EP title
  const epHtml = `
    <!DOCTYPE html>
    <html>
      <head>
        <title>夏日回憶 EP - 橘子海 - KKBOX</title>
        <meta property="og:title" content="夏日回憶 EP - 橘子海 - KKBOX">
        <meta property="music:musician" content="橘子海">
        <meta property="music:release_date" content="2026-08-15">
      </head>
      <body>
        <a href="/track/trk_ep_01">夏日</a>
        <a href="/track/trk_ep_02">回憶</a>
      </body>
    </html>
  `;

  const ep = extractAlbumDetail(epHtml, "https://play.kkbox.com/album/alb_ep_01");
  assert.strictEqual(ep.release_type, "ep", "Title with EP marker must infer ep");
  assert.strictEqual(ep.tracks.length, 2);
  assert.strictEqual(ep.tracks[0].source_id, "trk_ep_01");
  assert.strictEqual(ep.tracks[0].title, "夏日");
  assert.strictEqual(ep.tracks[0].track_number, 1);
  assert.strictEqual(ep.tracks[0].source_url, "https://play.kkbox.com/track/trk_ep_01");
  assert.strictEqual(ep.tracks[1].source_id, "trk_ep_02");
  assert.strictEqual(ep.tracks[1].title, "回憶");
  assert.strictEqual(ep.tracks[1].track_number, 2);
  assert.strictEqual(ep.tracks[1].source_url, "https://play.kkbox.com/track/trk_ep_02");
  console.log("✔ String HTML Fallback with /track/ & nested elements passed");
}

// 10. Test DOM Extraction with /track/ Elements
{
  function createMockTrackElement(href, text) {
    return {
      getAttribute(attr) {
        if (attr === "href") return href;
        return null;
      },
      href: href.startsWith("http") ? href : `https://play.kkbox.com${href}`,
      innerText: text,
      textContent: text,
    };
  }

  const mockDoc = {
    title: "海風吹過 - KKBOX",
    querySelector(selector) {
      if (selector.includes("og:title")) return { getAttribute: () => "海風吹過 - KKBOX" };
      if (selector.includes("music:musician")) return { getAttribute: () => "萬能青年旅店" };
      if (selector.includes("music:release_date")) return { getAttribute: () => "2026-08-20" };
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "script[type='application/ld+json']") return [];
      if (selector.includes("[class*='badge']") || selector.includes("span")) return [];
      if (selector.includes("/track/") || selector.includes("/song/")) {
        return [
          createMockTrackElement("/track/dom_trk_01", "第一樂章：海風"),
          createMockTrackElement("/track/dom_trk_02", "第二樂章：夜航"),
          createMockTrackElement("/track/dom_trk_03", "第三樂章：燈塔"),
        ];
      }
      return [];
    },
  };

  const domAlbum = extractAlbumDetail(mockDoc, "https://play.kkbox.com/album/alb_dom_99");
  assert.strictEqual(domAlbum.source_id, "alb_dom_99");
  assert.strictEqual(domAlbum.title, "海風吹過");
  assert.strictEqual(domAlbum.artists[0].name, "萬能青年旅店");
  assert.strictEqual(domAlbum.release_date, "2026-08-20");
  assert.strictEqual(domAlbum.release_type, "album");
  assert.strictEqual(domAlbum.tracks.length, 3);

  assert.strictEqual(domAlbum.tracks[0].source_id, "dom_trk_01");
  assert.strictEqual(domAlbum.tracks[0].title, "第一樂章：海風");
  assert.strictEqual(domAlbum.tracks[0].track_number, 1);
  assert.strictEqual(domAlbum.tracks[0].source_url, "https://play.kkbox.com/track/dom_trk_01");

  assert.strictEqual(domAlbum.tracks[1].source_id, "dom_trk_02");
  assert.strictEqual(domAlbum.tracks[1].title, "第二樂章：夜航");
  assert.strictEqual(domAlbum.tracks[1].track_number, 2);
  assert.strictEqual(domAlbum.tracks[1].source_url, "https://play.kkbox.com/track/dom_trk_02");

  assert.strictEqual(domAlbum.tracks[2].source_id, "dom_trk_03");
  assert.strictEqual(domAlbum.tracks[2].title, "第三樂章：燈塔");
  assert.strictEqual(domAlbum.tracks[2].track_number, 3);
  assert.strictEqual(domAlbum.tracks[2].source_url, "https://play.kkbox.com/track/dom_trk_03");
  console.log("✔ DOM extraction with /track/ structure & ordered tracks passed");
}

// 10B. Listing metadata fills current album pages that omit OpenGraph artist.
{
  const currentShellHtml = `
    <html><head><title>Kay Tse I - KKBOX Web Player</title></head>
    <body><a href="/track/current_01">第一首歌</a></body></html>`;
  const album = extractAlbumDetail(
    currentShellHtml,
    "https://play.kkbox.com/album/OpjeJjIQB1A89N0NB8",
    { title: "Kay Tse I", artist: "謝安琪 (Kay Tse)" }
  );
  assert.strictEqual(album.title, "Kay Tse I");
  assert.strictEqual(album.artists[0].name, "謝安琪 (Kay Tse)");
  assert.strictEqual(album.tracks[0].title, "第一首歌");
  console.log("✔ Listing hint fallback for missing album artist passed");
}

// 10C. Generic KKBOX og:title must not override the real document title.
{
  const html = `
    <html><head>
      <title>Kay Tse I - KKBOX Web Player</title>
      <meta property="og:title" content="KKBOX Web Player">
      <meta property="music:musician" content="謝安琪">
    </head><body><a href="/track/current_02">節外生枝</a></body></html>`;
  const album = extractAlbumDetail(html, "https://play.kkbox.com/album/title_regression");
  assert.strictEqual(album.title, "Kay Tse I");
  assert.strictEqual(album.album_title, "Kay Tse I");
  console.log("✔ Generic KKBOX OpenGraph title rejection passed");
}

// 11. Test Backward Compatibility with Legacy /song/ URLs
{
  const legacyHtml = `
    <!DOCTYPE html>
    <html>
      <head>
        <title>經典老歌 - 各路藝人 - KKBOX</title>
        <meta property="og:title" content="經典老歌 - 各路藝人 - KKBOX">
        <meta property="music:musician" content="各路藝人">
      </head>
      <body>
        <a href="/song/legacy_001">舊歌一號</a>
        <a href="/song/legacy_002">舊歌二號</a>
      </body>
    </html>
  `;

  const legacyAlbum = extractAlbumDetail(legacyHtml, "https://play.kkbox.com/album/alb_legacy_01");
  assert.strictEqual(legacyAlbum.tracks.length, 2);
  assert.strictEqual(legacyAlbum.tracks[0].source_id, "legacy_001");
  assert.strictEqual(legacyAlbum.tracks[0].title, "舊歌一號");
  assert.strictEqual(legacyAlbum.tracks[0].source_url, "https://play.kkbox.com/song/legacy_001");
  assert.strictEqual(legacyAlbum.tracks[1].source_id, "legacy_002");
  assert.strictEqual(legacyAlbum.tracks[1].title, "舊歌二號");
  assert.strictEqual(legacyAlbum.tracks[1].source_url, "https://play.kkbox.com/song/legacy_002");
  console.log("✔ Legacy /song/ backward compatibility passed");
}

// 12. Test Duration ISO parser
{
  assert.strictEqual(parseIsoDuration("PT3M30S"), 210000);
  assert.strictEqual(parseIsoDuration("PT4M"), 240000);
  assert.strictEqual(parseIsoDuration("PT45S"), 45000);
  assert.strictEqual(parseIsoDuration(null), null);
  console.log("✔ Duration ISO parser passed");
}

console.log("\n[SUCCESS] All KKBOX Web Player Extension Extractor tests passed!\n");
