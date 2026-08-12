import { Hono } from "hono";
import { ADMIN_COOKIE, getCookie, verifyAdminSession } from "../lib/admin-auth.js";
import { getAnalyticsSummary } from "../lib/analytics.js";

export const adminSummaryRoutes = new Hono();

adminSummaryRoutes.get("/admin/api/summary", (c) => {
  const token = getCookie(c.req.header("cookie"), ADMIN_COOKIE);
  if (!verifyAdminSession(token)) return c.text("Unauthorized", 401);
  return c.json(getAnalyticsSummary(), 200, { "Cache-Control": "private, no-store, max-age=0" });
});
