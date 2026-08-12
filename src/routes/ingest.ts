import { Hono } from "hono";
import { recordAnalyticsEvent } from "../lib/analytics.js";

type IngestBody = {
  path: string;
  method: string;
  status: number;
  kind?: "visit" | "presence" | "security";
  reason?: string;
  query?: string;
  captureForensics?: "visit" | "security";
};

export const ingestRoutes = new Hono();

ingestRoutes.post("/api/ingest", async (c) => {
  const body = (await c.req.json().catch(() => null)) as IngestBody | null;
  if (!body || typeof body.path !== "string" || typeof body.method !== "string") {
    return c.text("Bad Request", 400);
  }
  recordAnalyticsEvent({
    headers: c.req.raw.headers,
    path: body.path,
    method: body.method,
    status: Number(body.status) || 0,
    kind: body.kind,
    reason: body.reason,
    query: body.query,
    captureForensics: body.captureForensics,
  });
  return c.body(null, 204, { "Cache-Control": "no-store, max-age=0" });
});
