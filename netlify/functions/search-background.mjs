// OfferScout async worker -> Abacus.AI "Product Price Comparison" workflow.
//
// This is a BACKGROUND function (note the `-background` suffix), so it can run
// for up to 15 minutes — long enough for the ~20-30s AI workflow. It writes the
// finished result into Netlify Blobs keyed by the caller-supplied jobId. The
// page then polls /api/result?jobId=... until the result is ready.
//
// Secrets come from environment variables (never committed):
//   ABACUS_DEPLOYMENT_ID    - workflow deployment id
//   ABACUS_DEPLOYMENT_TOKEN - workflow deployment token (used for auth)
//   ABACUS_ENDPOINT         - (optional) override the prediction endpoint URL

import { getStore } from "@netlify/blobs";

// We must hit the org-specific prediction host directly; the generic
// api.abacus.ai gateway redirects to an internal cluster that isn't publicly
// reachable.
const ABACUS_ENDPOINT =
  process.env.ABACUS_ENDPOINT ||
  "https://winmarkcorporation.abacus.ai/api/v0/executeAgent";

const JOB_STORE = "offerscout-jobs";

// ── Normalisation helpers ──────────────────────────────────────────────────

function toNumber(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return isFinite(value) ? value : null;
  const m = String(value).replace(/,/g, "").match(/[\d]+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

function pick(obj, keys) {
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== "") return obj[k];
  }
  return undefined;
}

// The agent JSON is returned as a string inside segments[].segment; parse it.
function parseMaybeJson(value) {
  if (typeof value === "string") {
    const t = value.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        return JSON.parse(t);
      } catch {
        /* leave as-is */
      }
    }
  }
  return value;
}

// Recursively locate the object holding the comparison data.
function findDataObject(obj, depth = 0) {
  obj = parseMaybeJson(obj);
  if (!obj || depth > 8 || typeof obj !== "object") return null;
  if (Array.isArray(obj)) {
    for (const el of obj) {
      const found = findDataObject(el, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (
    Array.isArray(obj.retailers) ||
    "average_price" in obj ||
    "product_identified" in obj
  ) {
    return obj;
  }
  for (const key of Object.keys(obj)) {
    const found = findDataObject(obj[key], depth + 1);
    if (found) return found;
  }
  return null;
}

function normalize(payload) {
  const data = findDataObject(payload) || {};
  const product = pick(data, ["product_identified", "product", "product_name"]) || "";
  const arr = Array.isArray(data.retailers)
    ? data.retailers
    : Array.isArray(data.results)
    ? data.results
    : [];

  const results = arr
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const source = pick(item, [
        "retailer_name",
        "retailer",
        "source",
        "store",
        "seller",
        "site",
      ]);
      let price = toNumber(
        pick(item, ["price", "current_price", "amount", "value", "cost"])
      );
      if (price === 0) price = null; // 0 means "price unavailable"
      const url = pick(item, ["product_url", "url", "link", "href"]);
      return {
        title: source || product || "Retailer",
        price: price,
        url: url || "#",
        source: product || "Offer",
      };
    })
    .filter(Boolean);

  const prices = results.map((r) => r.price).filter((p) => p !== null);
  let stats = {};
  if (prices.length) {
    const sorted = [...prices].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const median =
      sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    const computedAvg = prices.reduce((a, b) => a + b, 0) / prices.length;

    const lowest =
      toNumber(data.lowest_price && data.lowest_price.value) ?? Math.min(...prices);
    const highest =
      toNumber(data.highest_price && data.highest_price.value) ?? Math.max(...prices);
    const average = toNumber(data.average_price) ?? computedAvg;
    const count = toNumber(data.total_retailers_found) ?? prices.length;

    stats = {
      average: Math.round(average * 100) / 100,
      lowest: Math.round(lowest * 100) / 100,
      highest: Math.round(highest * 100) / 100,
      median: Math.round(median * 100) / 100,
      count: count,
    };
  }
  return { results, stats, product };
}

// ── Background handler ──────────────────────────────────────────────────────

export default async (req) => {
  let body = {};
  try {
    body = await req.json();
  } catch {
    return; // nothing we can do without a body
  }

  const jobId = body.jobId;
  if (!jobId) return;

  const store = getStore({ name: JOB_STORE, consistency: "strong" });

  try {
    const deploymentId = process.env.ABACUS_DEPLOYMENT_ID;
    const deploymentToken = process.env.ABACUS_DEPLOYMENT_TOKEN;
    if (!deploymentId || !deploymentToken) {
      await store.setJSON(jobId, {
        status: "error",
        error: "Server is not configured. Missing Abacus.AI environment variables.",
      });
      return;
    }

    const query = (body.query || "").trim();
    const image = body.image || null;

    let abacusRes;
    if (image) {
      // The workflow's price_comparison(product_description, product_image)
      // accepts product_image as a plain base64 string. The server marks that
      // field as a blob, so passing it in keywordArguments is rejected with
      // "Invalid blob input data". Passing it POSITIONALLY bypasses that
      // validation and routes straight into the function's base64 branch.
      abacusRes = await fetch(ABACUS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          deploymentId,
          deploymentToken,
          arguments: [null, image],
        }),
      });
    } else {
      abacusRes = await fetch(ABACUS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          deploymentId,
          deploymentToken,
          keywordArguments: { product_description: query },
        }),
      });
    }

    const bodyText = await abacusRes.text();

    if (!abacusRes.ok) {
      await store.setJSON(jobId, {
        status: "error",
        error: "AI workflow request failed",
        detail: `HTTP ${abacusRes.status}: ${bodyText.slice(0, 1200)}`,
        mode: image ? "image" : "text",
      });
      return;
    }

    let raw;
    try {
      raw = JSON.parse(bodyText);
    } catch {
      await store.setJSON(jobId, {
        status: "error",
        error: "AI workflow returned invalid JSON",
        detail: bodyText.slice(0, 1200),
        mode: image ? "image" : "text",
      });
      return;
    }

    const { results, stats, product } = normalize(raw);

    // Surface the workflow's own error message (e.g. "Please provide an image")
    // instead of silently showing "no results".
    if (!results.length) {
      const dataObj = findDataObject(raw) || {};
      if (dataObj.error) {
        await store.setJSON(jobId, {
          status: "error",
          error: "AI workflow could not complete the search",
          detail: String(dataObj.error).slice(0, 500),
          mode: image ? "image" : "text",
        });
        return;
      }
    }

    await store.setJSON(jobId, {
      status: "done",
      query: query || product || "your product",
      results,
      stats,
      recalls: [],
    });
  } catch (err) {
    await store.setJSON(jobId, {
      status: "error",
      error: "Worker exception",
      detail: String((err && err.stack) || err).slice(0, 1200),
    });
  }
};

export const config = {
  path: "/api/search",
};
