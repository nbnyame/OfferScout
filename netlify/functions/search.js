const fetch = require("node-fetch");
const cheerio = require("cheerio");

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
};

const TIMEOUT = 7000;

// ── Relevance filter ──

const STOP_WORDS = new Set([
  "the","a","an","and","or","for","of","in","on","to","with","is","by","at",
  "from","as","it","be","this","that","are","was","were","been","has","have",
  "had","do","does","did","but","not","so","if","no","all","my","your","our",
  "its","his","her","new","up","out","one","two","set","get","can","will",
  "just","into",
]);

function isRelevant(title, query) {
  if (!title || !query) return false;
  const titleLower = title.toLowerCase();
  const titleWords = new Set(titleLower.split(/\W+/).filter(Boolean));
  let keywords = query
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w && w.length > 2 && !STOP_WORDS.has(w));
  if (!keywords.length)
    keywords = query
      .toLowerCase()
      .split(/\W+/)
      .filter((w) => w && w.length > 1);
  if (!keywords.length) return true;

  function kwMatch(kw) {
    if (titleLower.includes(kw)) return true;
    for (const tw of titleWords) {
      if (tw.length >= 3 && kw.length >= 3) {
        const shared = Math.min(kw.length, tw.length);
        if (
          kw.slice(0, shared) === tw.slice(0, shared) &&
          Math.abs(kw.length - tw.length) <= 3
        )
          return true;
      }
    }
    return false;
  }

  const matches = keywords.filter(kwMatch).length;
  return keywords.length <= 3
    ? matches === keywords.length
    : matches >= Math.max(2, Math.floor(keywords.length * 0.6));
}

// ── Scrapers ──

async function fetchText(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  try {
    const r = await fetch(url, { headers: HEADERS, signal: controller.signal });
    if (!r.ok) return null;
    return await r.text();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function scrapeAmazon(query) {
  try {
    const html = await fetchText(
      `https://www.amazon.com/s?k=${encodeURIComponent(query)}`
    );
    if (!html) return null;
    const $ = cheerio.load(html);
    for (const el of $('div[data-component-type="s-search-result"]').toArray()) {
      const titleEl = $(el).find("h2");
      const priceWhole = $(el).find("span.a-price-whole");
      const priceFrac = $(el).find("span.a-price-fraction");
      const linkEl = $(el).find("a.a-link-normal[href]");
      if (titleEl.length && priceWhole.length && linkEl.length) {
        const title = titleEl.text().trim();
        if (!isRelevant(title, query)) continue;
        const whole = priceWhole.text().trim().replace(/,/g, "").replace(/\.$/, "");
        const frac = priceFrac.length ? priceFrac.text().trim() : "00";
        const price = parseFloat(`${whole}.${frac}`);
        let href = linkEl.attr("href");
        const link = href.startsWith("http")
          ? href
          : `https://www.amazon.com${href}`;
        return { title, price, url: link, source: "Amazon" };
      }
    }
  } catch {}
  return null;
}

async function scrapeNewegg(query) {
  try {
    const html = await fetchText(
      `https://www.newegg.com/p/pl?d=${encodeURIComponent(query)}`
    );
    if (!html) return null;
    const $ = cheerio.load(html);
    for (const el of $("div.item-cell").toArray()) {
      const titleEl = $(el).find("a.item-title");
      const priceEl = $(el).find("li.price-current");
      if (titleEl.length && priceEl.length) {
        const title = titleEl.text().trim();
        if (!isRelevant(title, query)) continue;
        const m = priceEl.text().trim().match(/([\d,]+\.\d{2})/);
        if (m) {
          const price = parseFloat(m[1].replace(/,/g, ""));
          let href = titleEl.attr("href") || "";
          const link = href.startsWith("http")
            ? href
            : `https://www.newegg.com${href}`;
          return { title, price, url: link, source: "Newegg" };
        }
      }
    }
  } catch {}
  return null;
}

async function scrapeEbay(query) {
  try {
    const url = `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(query)}`;
    const html = await fetchText(url);
    if (!html) return null;
    const $ = cheerio.load(html);
    for (const el of $("div.s-item__info").toArray()) {
      const titleEl =
        $(el).find("div.s-item__title").first() ||
        $(el).find("span[role=heading]").first();
      const priceEl = $(el).find("span.s-item__price").first();
      if (!titleEl.length || !priceEl.length) continue;
      const title = titleEl.text().trim();
      if (!title || title === "Shop on eBay") continue;
      if (!isRelevant(title, query)) continue;
      const m = priceEl.text().trim().match(/\$([\d,]+\.?\d*)/);
      if (!m) continue;
      const price = parseFloat(m[1].replace(/,/g, ""));
      const parent = $(el).closest("li, div.s-item__wrapper");
      const linkEl = parent.find("a[href]").first();
      let link = linkEl.length ? linkEl.attr("href") : url;
      if (link.includes("?")) link = link.split("?")[0];
      return { title, price, url: link, source: "eBay" };
    }
  } catch {}
  return null;
}

async function scrapeWalmart(query) {
  try {
    const url = `https://www.walmart.com/search?q=${encodeURIComponent(query)}`;
    const html = await fetchText(url);
    if (!html) return null;
    let pm =
      html.match(/"priceInfo":\{"currentPrice":\{"price":([\d.]+)/) ||
      html.match(/"currentPrice":\{"price":([\d.]+)/);
    const tm = html.match(/"name":"([^"]{10,150})"/);
    const im = html.match(/"usItemId":"(\d+)"/);
    if (pm && tm) {
      const price = parseFloat(pm[1]);
      const title = tm[1];
      if (isRelevant(title, query)) {
        const itemId = im ? im[1] : "";
        const link = itemId
          ? `https://www.walmart.com/ip/${itemId}`
          : url;
        return { title, price, url: link, source: "Walmart" };
      }
    }
  } catch {}
  return null;
}

async function scrapeOfficedepot(query) {
  try {
    const html = await fetchText(
      `https://www.officedepot.com/catalog/search.do?Ntt=${encodeURIComponent(query)}`
    );
    if (!html) return null;
    const $ = cheerio.load(html);
    for (const el of $("div.od-product-card").toArray()) {
      let title = null,
        href = null;
      for (const a of $(el).find("a[href]").toArray()) {
        const t = $(a).text().trim();
        if (t && t.length >= 5) {
          title = t;
          href = $(a).attr("href");
          break;
        }
      }
      if (!title || !href) continue;
      if (!isRelevant(title, query)) continue;
      const fullUrl = href.startsWith("http")
        ? href
        : `https://www.officedepot.com${href}`;
      const m = $(el).text().match(/\$([\d,]+\.\d{2})/);
      if (m) {
        const price = parseFloat(m[1].replace(/,/g, ""));
        return { title, price, url: fullUrl, source: "Office Depot" };
      }
    }
  } catch {}
  return null;
}

// ── Orchestrator ──

const SCRAPERS = [scrapeAmazon, scrapeNewegg, scrapeEbay, scrapeOfficedepot, scrapeWalmart];

const FALLBACK_RETAILERS = [
  ["Target", "https://www.target.com/s?searchTerm="],
  ["Best Buy", "https://www.bestbuy.com/site/searchpage.jsp?st="],
  ["Costco", "https://www.costco.com/CatalogSearch?dept=All&keyword="],
  ["Home Depot", "https://www.homedepot.com/s/"],
  ["eBay", "https://www.ebay.com/sch/i.html?_nkw="],
  ["Walmart", "https://www.walmart.com/search?q="],
];

async function searchAll(query) {
  const results = [];
  const seen = new Set();

  const settled = await Promise.allSettled(
    SCRAPERS.map((fn) => fn(query))
  );
  for (const s of settled) {
    if (s.status === "fulfilled" && s.value && !seen.has(s.value.source)) {
      seen.add(s.value.source);
      results.push(s.value);
    }
  }

  if (results.length < 6) {
    for (const [retailer, urlBase] of FALLBACK_RETAILERS) {
      if (results.length >= 10 || seen.has(retailer)) continue;
      seen.add(retailer);
      results.push({
        title: `Search ${retailer} for "${query}"`,
        price: null,
        url: `${urlBase}${encodeURIComponent(query)}`,
        source: `${retailer} (search link)`,
      });
    }
  }
  return results;
}

// ── Recall checker (CPSC SaferProducts.gov) ──

async function checkRecalls(query) {
  try {
    const url = `https://www.saferproducts.gov/RestWebServices/Recall?format=json&RecallTitle=${encodeURIComponent(query)}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    const r = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    if (!r.ok) return [];
    const data = await r.json();
    return data.slice(0, 5).map((item) => ({
      title: item.Title || "Unknown Recall",
      date: item.RecallDate || "",
      description:
        item.Products && item.Products.length
          ? item.Products[0].Description || ""
          : "",
      url: item.URL || "",
      hazard:
        item.Hazards && item.Hazards.length
          ? item.Hazards[0].Name || ""
          : "",
    }));
  } catch {
    return [];
  }
}

// ── Netlify handler ──

exports.handler = async (event) => {
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
  };

  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 204, headers, body: "" };
  }

  let query = "";
  try {
    const body = JSON.parse(event.body || "{}");
    query = (body.query || "").trim();
  } catch {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: "Invalid request body" }),
    };
  }

  if (!query) {
    return {
      statusCode: 400,
      headers,
      body: JSON.stringify({ error: "Please enter a search term" }),
    };
  }

  const [results, recalls] = await Promise.all([
    searchAll(query),
    checkRecalls(query),
  ]);
  const prices = results.filter((r) => r.price !== null).map((r) => r.price);
  let stats = {};
  if (prices.length) {
    const sorted = [...prices].sort((a, b) => a - b);
    const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
    const mid = Math.floor(sorted.length / 2);
    const median =
      sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    stats = {
      average: Math.round(avg * 100) / 100,
      lowest: Math.round(Math.min(...prices) * 100) / 100,
      highest: Math.round(Math.max(...prices) * 100) / 100,
      median: Math.round(median * 100) / 100,
      count: prices.length,
    };
  }

  return {
    statusCode: 200,
    headers,
    body: JSON.stringify({ query, results, stats, recalls }),
  };
};
