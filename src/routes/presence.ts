import { Hono } from "hono";
import { recordAnalyticsEvent } from "../lib/analytics.js";

export const presenceRoutes = new Hono();

presenceRoutes.get("/api/presence", (c) => {
  const requestedPage = c.req.query("page") || "/";
  const reportedPath = requestedPage.startsWith("/") ? requestedPage : "/";
  const isInitialProfile = c.req.query("profile") === "1";
  recordAnalyticsEvent({
    headers: c.req.raw.headers,
    path: reportedPath,
    method: "GET",
    status: 204,
    kind: "presence",
    captureForensics: isInitialProfile ? "visit" : undefined,
    reason: isInitialProfile ? "Active device profile" : undefined,
  });
  return c.body(null, 204, { "Cache-Control": "no-store, max-age=0" });
});
