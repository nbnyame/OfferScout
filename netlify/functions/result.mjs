// OfferScout result poller. Returns the stored job result from Netlify Blobs.
//   - { status: "pending" }              -> still working, keep polling
//   - { status: "done", results, ... }   -> finished
//   - { status: "error", error }         -> failed

import { getStore } from "@netlify/blobs";

const JOB_STORE = "offerscout-jobs";

export default async (req) => {
  const headers = { "Content-Type": "application/json" };
  const url = new URL(req.url);
  const jobId = url.searchParams.get("jobId");

  if (!jobId) {
    return new Response(
      JSON.stringify({ status: "error", error: "Missing jobId" }),
      { status: 400, headers }
    );
  }

  const store = getStore({ name: JOB_STORE, consistency: "strong" });
  const data = await store.get(jobId, { type: "json" });

  if (!data) {
    // Not written yet — the background worker is still running.
    return new Response(JSON.stringify({ status: "pending" }), { headers });
  }

  return new Response(JSON.stringify(data), { headers });
};

export const config = {
  path: "/api/result",
};
