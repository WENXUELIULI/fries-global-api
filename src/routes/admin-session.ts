import { Hono } from "hono";
import { ADMIN_COOKIE, getCookie, verifyAdminSession } from "../lib/admin-auth.js";

export const adminSessionRoutes = new Hono();

adminSessionRoutes.get("/admin/api/session", (c) => {
  const token = getCookie(c.req.header("cookie"), ADMIN_COOKIE);
  if (!verifyAdminSession(token)) return c.text("Unauthorized", 401);
  return c.body(null, 204, { "Cache-Control": "private, no-store, max-age=0" });
});
