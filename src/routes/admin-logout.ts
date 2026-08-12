import { Hono } from "hono";
import { setCookie } from "hono/cookie";
import { ADMIN_COOKIE, validSameOriginRequest } from "../lib/admin-auth.js";
import { recordAnalyticsEvent } from "../lib/analytics.js";

export const adminLogoutRoutes = new Hono();

adminLogoutRoutes.post("/admin/api/logout", (c) => {
  const request = c.req.raw;

  if (!validSameOriginRequest(request)) {
    recordAnalyticsEvent({
      headers: request.headers,
      path: "/admin/api/logout",
      method: "POST",
      status: 403,
      kind: "security",
      reason: "Rejected cross-site admin logout",
    });
    return c.text("Forbidden", 403);
  }

  setCookie(c, ADMIN_COOKIE, "", {
    httpOnly: true,
    sameSite: "Strict",
    path: "/admin",
    maxAge: 0,
  });
  c.header("Cache-Control", "no-store, max-age=0");
  return c.redirect("/admin/login?signedout=1", 303);
});
