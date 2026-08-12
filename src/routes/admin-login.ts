import { Hono } from "hono";
import { setCookie } from "hono/cookie";
import {
  ADMIN_COOKIE,
  ADMIN_SESSION_SECONDS,
  createAdminSession,
  requestIsHttps,
  validSameOriginRequest,
  verifyCredentials,
} from "../lib/admin-auth.js";
import { recordAnalyticsEvent } from "../lib/analytics.js";

type LoginState = { startedAt: number; failures: number; blockedUntil: number };
const globalLoginState = globalThis as typeof globalThis & { __friesLoginAttempts?: Map<string, LoginState> };
const loginAttempts = globalLoginState.__friesLoginAttempts ?? new Map<string, LoginState>();
globalLoginState.__friesLoginAttempts = loginAttempts;
const WINDOW_MS = 15 * 60_000;
const BLOCK_MS = 30 * 60_000;
const MAX_FAILURES = 5;

function clientKey(request: Request) {
  return request.headers.get("cf-connecting-ip")?.trim() || "local-origin";
}

export const adminLoginRoutes = new Hono();

adminLoginRoutes.post("/admin/api/login", async (c) => {
  const request = c.req.raw;

  if (!validSameOriginRequest(request) || Number(request.headers.get("content-length") || 0) > 2_048) {
    recordAnalyticsEvent({
      headers: request.headers,
      path: "/admin/api/login",
      method: "POST",
      status: 403,
      kind: "security",
      reason: "Rejected cross-site or oversized admin login",
    });
    return c.text("Forbidden", 403, { "Cache-Control": "no-store" });
  }

  const now = Date.now();
  const key = clientKey(request);
  let state = loginAttempts.get(key) || { startedAt: now, failures: 0, blockedUntil: 0 };
  if (now - state.startedAt > WINDOW_MS) state = { startedAt: now, failures: 0, blockedUntil: 0 };
  if (state.blockedUntil > now) {
    recordAnalyticsEvent({
      headers: request.headers,
      path: "/admin/api/login",
      method: "POST",
      status: 429,
      kind: "security",
      reason: "Admin login temporarily blocked",
    });
    return c.text("Too many login attempts", 429, {
      "Retry-After": String(Math.ceil((state.blockedUntil - now) / 1000)),
      "Cache-Control": "no-store",
    });
  }

  const form = await c.req.formData();
  const username = String(form.get("username") || "");
  const password = String(form.get("password") || "");
  const honeypot = String(form.get("website") || "");
  if (honeypot || !verifyCredentials(username, password)) {
    state.failures += 1;
    if (state.failures >= MAX_FAILURES) state.blockedUntil = now + BLOCK_MS;
    loginAttempts.set(key, state);
    recordAnalyticsEvent({
      headers: request.headers,
      path: "/admin/api/login",
      method: "POST",
      status: 401,
      kind: "security",
      reason: honeypot ? "Admin login honeypot triggered" : "Invalid admin credentials",
    });
    c.header("Cache-Control", "no-store, max-age=0");
    return c.redirect("/admin/login?error=1", 303);
  }

  loginAttempts.delete(key);
  setCookie(c, ADMIN_COOKIE, createAdminSession(), {
    httpOnly: true,
    secure: requestIsHttps(request),
    sameSite: "Strict",
    path: "/admin",
    maxAge: ADMIN_SESSION_SECONDS,
  });
  c.header("Cache-Control", "no-store, max-age=0");
  return c.redirect("/admin", 303);
});
