import { Hono } from "hono";
import { ADMIN_COOKIE, getCookie, verifyAdminSession } from "../lib/admin-auth.js";
import { analyticsCsv } from "../lib/analytics.js";

export const adminExportRoutes = new Hono();

adminExportRoutes.get("/admin/api/export", (c) => {
  const token = getCookie(c.req.header("cookie"), ADMIN_COOKIE);
  if (!verifyAdminSession(token)) return c.text("Unauthorized", 401);
  const day = new Date().toISOString().slice(0, 10);
  return c.text(analyticsCsv(), 200, {
    "Cache-Control": "private, no-store, max-age=0",
    "Content-Disposition": `attachment; filename="fries-global-analytics-${day}.csv"`,
    "Content-Type": "text/csv; charset=utf-8",
  });
});
