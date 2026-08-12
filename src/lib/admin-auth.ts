import { createHmac, scryptSync, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type AdminConfig = {
  username: string;
  passwordSalt: string;
  passwordHash: string;
  sessionSecret: string;
  analyticsSecret: string;
};

export const ADMIN_COOKIE = "fries_admin_session";
export const ADMIN_SESSION_SECONDS = 4 * 60 * 60;

function loadConfig(): AdminConfig | null {
  try {
    const config = JSON.parse(readFileSync(join(process.cwd(), ".private", "admin-auth.json"), "utf8")) as AdminConfig;
    if (!config.username || !config.passwordSalt || !config.passwordHash || !config.sessionSecret) return null;
    return config;
  } catch {
    return null;
  }
}
function safeEqual(left: string, right: string) {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function verifyCredentials(username: string, password: string) {
  const config = loadConfig();
  if (!config || username.length > 80 || password.length > 256) return false;
  const suppliedHash = scryptSync(password, config.passwordSalt, 64).toString("hex");
  return safeEqual(username, config.username) && safeEqual(suppliedHash, config.passwordHash);
}

export function createAdminSession() {
  const config = loadConfig();
  if (!config) throw new Error("Admin authentication is not configured.");
  const payload = `${Math.floor(Date.now() / 1000) + ADMIN_SESSION_SECONDS}.${crypto.randomUUID()}`;
  const signature = createHmac("sha256", config.sessionSecret).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

export function verifyAdminSession(token?: string) {
  if (!token || token.length > 300) return false;
  const config = loadConfig();
  if (!config) return false;
  const lastDot = token.lastIndexOf(".");
  if (lastDot < 1) return false;
  const payload = token.slice(0, lastDot);
  const signature = token.slice(lastDot + 1);
  const expiresAt = Number(payload.split(".", 1)[0]);
  if (!Number.isFinite(expiresAt) || expiresAt <= Math.floor(Date.now() / 1000)) return false;
  const expected = createHmac("sha256", config.sessionSecret).update(payload).digest("base64url");
  return safeEqual(signature, expected);
}

export function requestIsHttps(request: Request) {
  return request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() === "https" || new URL(request.url).protocol === "https:";
}

export function validSameOriginRequest(request: Request) {
  const origin = request.headers.get("origin");
  const host = (request.headers.get("x-forwarded-host") || request.headers.get("host") || "").toLowerCase();
  if (!origin || !host) return false;
  try {
    return new URL(origin).host.toLowerCase() === host && request.headers.get("sec-fetch-site") !== "cross-site";
  } catch {
    return false;
  }
}

export function getCookie(header: string | undefined, name: string) {
  if (!header) return undefined;
  const parts = header.split(/;\s*/);
  for (const part of parts) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i) === name) return decodeURIComponent(part.slice(i + 1));
  }
  return undefined;
}
