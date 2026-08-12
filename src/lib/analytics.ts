import { createHmac } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { getForensicReport, recordForensicEvent, type ForensicReport } from "./forensics.js";

export type AnalyticsEvent = {
  at: string;
  kind: "visit" | "presence" | "security";
  path: string;
  method: string;
  status: number;
  deviceId: string;
  networkId: string;
  networkHint: string;
  country: string;
  device: "Desktop" | "Mobile" | "Tablet" | "Bot" | "Unknown";
  browser: string;
  os: string;
  referrer: string;
  reason?: string;
};

export type AnalyticsSummary = {
  generatedAt: string;
  onlineNow: number;
  views24h: number;
  unique24h: number;
  security24h: number;
  bot24h: number;
  views7d: number;
  hourly: Array<{ label: string; views: number; visitors: number }>;
  topPages: Array<{ label: string; value: number }>;
  topCountries: Array<{ label: string; value: number }>;
  topDevices: Array<{ label: string; value: number }>;
  topReferrers: Array<{ label: string; value: number }>;
  recentVisitors: Array<{
    at: string;
    deviceId: string;
    networkHint: string;
    country: string;
    device: string;
    browser: string;
    os: string;
    path: string;
    referrer: string;
  }>;
  recentSecurity: Array<{
    at: string;
    networkHint: string;
    country: string;
    path: string;
    method: string;
    status: number;
    reason: string;
  }>;
  forensics: ForensicReport;
};

const privateRoot = join(process.cwd(), ".private");
const eventRoot = join(privateRoot, "analytics");
const authFile = join(privateRoot, "admin-auth.json");
const MAX_EVENT_FILES = 31;
const MAX_EVENT_FILE_BYTES = 10 * 1024 * 1024;
const WRITE_LIMIT_PER_MINUTE = 240;
const WRITE_LIMIT_PER_NETWORK_MINUTE = 60;
let cachedSecret = "";
let lastCleanupDate = "";

type WriteCounter = { startedAt: number; count: number };
type AnalyticsWriteState = { global: WriteCounter; networks: Map<string, WriteCounter> };
const globalAnalytics = globalThis as typeof globalThis & { __friesAnalyticsWriteState?: AnalyticsWriteState };
const analyticsWriteState = globalAnalytics.__friesAnalyticsWriteState ?? {
  global: { startedAt: Date.now(), count: 0 },
  networks: new Map<string, WriteCounter>(),
};
globalAnalytics.__friesAnalyticsWriteState = analyticsWriteState;

function analyticsSecret() {
  if (cachedSecret) return cachedSecret;
  try {
    const config = JSON.parse(readFileSync(authFile, "utf8")) as { analyticsSecret?: string };
    if (config.analyticsSecret && config.analyticsSecret.length >= 32) {
      cachedSecret = config.analyticsSecret;
      return cachedSecret;
    }
  } catch { }
  cachedSecret = process.env.ANALYTICS_SECRET || "fries-global-local-preview-anonymous-analytics";
  return cachedSecret;
}

function shortHmac(value: string) {
  return createHmac("sha256", analyticsSecret()).update(value).digest("hex").slice(0, 16);
}

function normalizeIp(value: string) {
  return value.trim().replace(/^::ffff:/, "").slice(0, 80) || "local";
}

function networkHint(ip: string) {
  if (ip === "local") return "Local preview";
  if (ip.includes(":")) {
    const parts = ip.split(":").filter(Boolean);
    return `${parts.slice(0, 2).join(":")}::`;
  }
  const parts = ip.split(".");
  return parts.length === 4 ? `${parts[0]}.${parts[1]}.x.x` : "Anonymous network";
}

function parseAgent(userAgent: string) {
  const ua = userAgent.slice(0, 512);
  const bot = /bot|crawler|spider|headless|curl|wget|python|scanner|facebookexternalhit|preview/i.test(ua);
  const tablet = /iPad|Tablet|PlayBook|Silk/i.test(ua);
  const mobile = /Mobi|Android|iPhone|Windows Phone/i.test(ua);
  const device: AnalyticsEvent["device"] = bot ? "Bot" : tablet ? "Tablet" : mobile ? "Mobile" : ua ? "Desktop" : "Unknown";

  let browser = "Other";
  if (/Edg\//i.test(ua)) browser = "Edge";
  else if (/OPR\//i.test(ua)) browser = "Opera";
  else if (/CriOS|Chrome\//i.test(ua)) browser = "Chrome";
  else if (/FxiOS|Firefox\//i.test(ua)) browser = "Firefox";
  else if (/Safari\//i.test(ua)) browser = "Safari";
  else if (bot) browser = "Automated client";

  let os = "Other";
  if (/Windows/i.test(ua)) os = "Windows";
  else if (/iPhone|iPad|iPod/i.test(ua)) os = "iOS / iPadOS";
  else if (/Android/i.test(ua)) os = "Android";
  else if (/Mac OS X|Macintosh/i.test(ua)) os = "macOS";
  else if (/Linux/i.test(ua)) os = "Linux";
  return { device, browser, os };
}

function safePath(pathname: string) {
  const withoutQuery = pathname.split(/[?#]/, 1)[0] || "/";
  return withoutQuery.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 240);
}

function referrerHost(value: string) {
  if (!value) return "Direct / unknown";
  try {
    const url = new URL(value);
    return url.hostname.toLowerCase().slice(0, 120) || "Direct / unknown";
  } catch {
    return "Direct / unknown";
  }
}

function cleanupOldFiles(today: string) {
  if (lastCleanupDate === today || !existsSync(eventRoot)) return;
  lastCleanupDate = today;
  const files = readdirSync(eventRoot)
    .filter((name) => /^events-\d{4}-\d{2}-\d{2}\.ndjson$/.test(name))
    .sort()
    .reverse();
  for (const name of files.slice(MAX_EVENT_FILES)) {
    try { rmSync(join(eventRoot, name), { force: true }); } catch { }
  }
}

function consumeWriteBudget(key: string, now: number) {
  if (now - analyticsWriteState.global.startedAt >= 60_000) {
    analyticsWriteState.global = { startedAt: now, count: 0 };
    for (const [network, counter] of analyticsWriteState.networks) {
      if (now - counter.startedAt >= 120_000) analyticsWriteState.networks.delete(network);
    }
  }
  if (analyticsWriteState.global.count >= WRITE_LIMIT_PER_MINUTE) return false;

  let network = analyticsWriteState.networks.get(key);
  if (!network || now - network.startedAt >= 60_000) {
    network = { startedAt: now, count: 0 };
    analyticsWriteState.networks.set(key, network);
  }
  if (network.count >= WRITE_LIMIT_PER_NETWORK_MINUTE) return false;
  analyticsWriteState.global.count += 1;
  network.count += 1;
  return true;
}

export function recordAnalyticsEvent(input: {
  headers: Headers;
  path: string;
  method: string;
  status: number;
  kind?: AnalyticsEvent["kind"];
  reason?: string;
  query?: string;
  captureForensics?: "visit" | "security";
}) {
  try {
    const forensicCategory = input.captureForensics || (input.kind === "security" ? "security" : input.kind === "visit" || !input.kind ? "visit" : undefined);
    if (forensicCategory) {
      recordForensicEvent({
        headers: input.headers,
        path: input.path,
        query: input.query,
        method: input.method,
        status: input.status,
        reason: input.reason || (forensicCategory === "visit" ? "Page visit" : "Security policy"),
        category: forensicCategory,
      });
    }
    const at = new Date();
    const day = at.toISOString().slice(0, 10);
    const ip = normalizeIp(input.headers.get("cf-connecting-ip") || input.headers.get("x-real-ip") || "local");
    const userAgent = input.headers.get("user-agent") || "";
    const anonymousNetwork = shortHmac(ip);
    if (!consumeWriteBudget(anonymousNetwork, at.getTime())) return;

    mkdirSync(eventRoot, { recursive: true });
    cleanupOldFiles(day);
    const eventFile = join(eventRoot, `events-${day}.ndjson`);
    if (existsSync(eventFile) && statSync(eventFile).size >= MAX_EVENT_FILE_BYTES) return;

    const agent = parseAgent(userAgent);
    const event: AnalyticsEvent = {
      at: at.toISOString(),
      kind: input.kind || "visit",
      path: safePath(input.path),
      method: input.method.slice(0, 12),
      status: input.status,
      deviceId: shortHmac(`${ip}|${userAgent}`),
      networkId: anonymousNetwork,
      networkHint: networkHint(ip),
      country: (input.headers.get("cf-ipcountry") || "XX").toUpperCase().slice(0, 2),
      device: agent.device,
      browser: agent.browser,
      os: agent.os,
      referrer: referrerHost(input.headers.get("referer") || ""),
      reason: input.reason?.slice(0, 100),
    };
    appendFileSync(eventFile, `${JSON.stringify(event)}\n`, { encoding: "utf8", mode: 0o600 });
  } catch {
    // Analytics must never prevent the public website from responding.
  }
}

function readRecentEvents(days = 7) {
  if (!existsSync(eventRoot)) return [] as AnalyticsEvent[];
  const cutoff = Date.now() - days * 86_400_000;
  const files = readdirSync(eventRoot)
    .filter((name) => /^events-\d{4}-\d{2}-\d{2}\.ndjson$/.test(name))
    .sort()
    .slice(-Math.min(days + 1, MAX_EVENT_FILES));
  const events: AnalyticsEvent[] = [];
  for (const name of files) {
    let contents = "";
    try { contents = readFileSync(join(eventRoot, name), "utf8"); } catch { continue; }
    for (const line of contents.split("\n")) {
      if (!line || events.length >= 100_000) continue;
      try {
        const event = JSON.parse(line) as AnalyticsEvent;
        if (Date.parse(event.at) >= cutoff) events.push(event);
      } catch { }
    }
  }
  return events.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

function ranked(events: AnalyticsEvent[], selector: (event: AnalyticsEvent) => string, limit = 6) {
  const counts = new Map<string, number>();
  for (const event of events) {
    const label = selector(event);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, limit);
}

export function countryName(code: string) {
  if (code === "XX") return "Unknown";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || code;
  } catch {
    return code;
  }
}

export function getAnalyticsSummary(): AnalyticsSummary {
  const now = Date.now();
  const events = readRecentEvents(7);
  const visits = events.filter((event) => event.kind === "visit");
  const activity = events.filter((event) => event.kind === "visit" || event.kind === "presence");
  const security = events.filter((event) => event.kind === "security");
  const visits24h = visits.filter((event) => Date.parse(event.at) >= now - 86_400_000);
  const security24h = security.filter((event) => Date.parse(event.at) >= now - 86_400_000);
  const onlineEvents = activity.filter((event) => Date.parse(event.at) >= now - 2 * 60_000);

  const hourly = Array.from({ length: 24 }, (_, index) => {
    const start = now - (23 - index) * 3_600_000;
    const end = start + 3_600_000;
    const bucket = visits.filter((event) => {
      const time = Date.parse(event.at);
      return time >= start && time < end;
    });
    return {
      label: new Date(start).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }),
      views: bucket.length,
      visitors: new Set(bucket.map((event) => event.deviceId)).size,
    };
  });

  const latestByDevice = new Map<string, AnalyticsEvent>();
  for (const event of activity) {
    const previous = latestByDevice.get(event.deviceId);
    if (event.kind === "presence" && previous) {
      latestByDevice.set(event.deviceId, { ...previous, at: event.at, path: event.path });
    } else {
      latestByDevice.set(event.deviceId, event);
    }
  }

  return {
    generatedAt: new Date(now).toISOString(),
    onlineNow: new Set(onlineEvents.map((event) => event.deviceId)).size,
    views24h: visits24h.length,
    unique24h: new Set(visits24h.map((event) => event.deviceId)).size,
    security24h: security24h.length,
    bot24h: visits24h.filter((event) => event.device === "Bot").length,
    views7d: visits.length,
    hourly,
    topPages: ranked(visits24h, (event) => event.path),
    topCountries: ranked(visits24h, (event) => `${event.country} · ${countryName(event.country)}`),
    topDevices: ranked(visits24h, (event) => event.device),
    topReferrers: ranked(visits24h, (event) => event.referrer),
    recentVisitors: [...latestByDevice.values()].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, 40).map((event) => ({
      at: event.at,
      deviceId: event.deviceId,
      networkHint: event.networkHint,
      country: `${event.country} · ${countryName(event.country)}`,
      device: event.device,
      browser: event.browser,
      os: event.os,
      path: event.path,
      referrer: event.referrer,
    })),
    recentSecurity: [...security].reverse().slice(0, 40).map((event) => ({
      at: event.at,
      networkHint: event.networkHint,
      country: `${event.country} · ${countryName(event.country)}`,
      path: event.path,
      method: event.method,
      status: event.status,
      reason: event.reason || "Security policy",
    })),
    forensics: getForensicReport(),
  };
}

export function analyticsCsv() {
  const events = readRecentEvents(30);
  const escape = (value: string | number | undefined) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const header = ["time", "type", "path", "method", "status", "anonymous_device", "network_hint", "country", "device", "browser", "os", "referrer", "reason"];
  const rows = events.map((event) => [
    event.at, event.kind, event.path, event.method, event.status, event.deviceId, event.networkHint,
    event.country, event.device, event.browser, event.os, event.referrer, event.reason,
  ].map(escape).join(","));
  return `\uFEFF${header.join(",")}\r\n${rows.join("\r\n")}`;
}

export const analyticsStorageDirectory = dirname(join(eventRoot, "events.ndjson"));
