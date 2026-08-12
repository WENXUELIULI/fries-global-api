import {
  createCipheriv, createDecipheriv, createHmac, randomBytes, randomUUID, timingSafeEqual,
} from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";

export type ForensicSeverity = "low" | "medium" | "high" | "critical";

export type ForensicEvent = {
  id: string;
  at: string;
  category: "visit" | "security";
  sourceIp: string;
  sourceType: "cloudflare" | "local";
  fingerprint: string;
  country: string;
  city: string;
  region: string;
  timezone: string;
  rayId: string;
  edgeColo: string;
  method: string;
  path: string;
  query: string;
  status: number;
  reason: string;
  severity: ForensicSeverity;
  signature: string;
  host: string;
  protocol: string;
  deviceModel: string;
  uaPlatform: string;
  platformVersion: string;
  architecture: string;
  bitness: string;
  mobileHint: string;
  browserBrands: string;
  fullVersionList: string;
  userAgent: string;
  acceptLanguage: string;
  referrer: string;
  origin: string;
};

type ForensicEnvelope = {
  v: 1;
  at: string;
  prev: string;
  iv: string;
  tag: string;
  data: string;
  chain: string;
};

export type ForensicReport = {
  generatedAt: string;
  retentionDays: number;
  integrity: { verified: boolean; checkedFiles: number; checkedRecords: number; errors: number };
  events: ForensicEvent[];
  connections: ForensicEvent[];
  uniqueSources24h: number;
  events24h: number;
  critical24h: number;
  topSources: Array<{ label: string; value: number }>;
  topSignatures: Array<{ label: string; value: number }>;
};

const privateRoot = join(process.cwd(), ".private");
const forensicRoot = join(privateRoot, "forensics");
const authFile = join(privateRoot, "admin-auth.json");
const RETENTION_DAYS = 90;
const MAX_DAILY_BYTES = 20 * 1024 * 1024;
const GLOBAL_WRITES_PER_MINUTE = 180;
const SOURCE_WRITES_PER_MINUTE = 30;
let keyCache: { encryption: Buffer; integrity: Buffer; fingerprint: Buffer } | null = null;
let lastCleanupDate = "";

type WriteCounter = { startedAt: number; count: number };
type ForensicState = { global: WriteCounter; sources: Map<string, WriteCounter>; lastChains: Map<string, string> };
const globalForensics = globalThis as typeof globalThis & { __friesForensicState?: ForensicState };
const forensicState = globalForensics.__friesForensicState ?? {
  global: { startedAt: Date.now(), count: 0 },
  sources: new Map<string, WriteCounter>(),
  lastChains: new Map<string, string>(),
};
globalForensics.__friesForensicState = forensicState;

function keys() {
  if (keyCache) return keyCache;
  let master = process.env.FORENSICS_SECRET || "";
  try {
    const config = JSON.parse(readFileSync(authFile, "utf8")) as { analyticsSecret?: string; sessionSecret?: string };
    master = config.analyticsSecret || config.sessionSecret || master;
  } catch { }
  if (!master) throw new Error("Forensic encryption is not configured.");
  keyCache = {
    encryption: createHmac("sha256", master).update("fries-forensics-encryption-v1").digest(),
    integrity: createHmac("sha256", master).update("fries-forensics-integrity-v1").digest(),
    fingerprint: createHmac("sha256", master).update("fries-forensics-fingerprint-v1").digest(),
  };
  return keyCache;
}

function clean(value: string | null | undefined, max: number) {
  return (value || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, max);
}

function safeIp(value: string | null) {
  const ip = clean(value, 80).replace(/^::ffff:/, "");
  return /^[0-9a-f:.]+$/i.test(ip) ? ip : "unknown";
}

function safeUrl(value: string | null) {
  if (!value) return "";
  try {
    const url = new URL(value);
    return `${url.origin}${url.pathname}`.slice(0, 300);
  } catch {
    return "";
  }
}

function redactedQuery(value?: string) {
  if (!value) return "";
  try {
    const params = new URLSearchParams(value.startsWith("?") ? value.slice(1) : value);
    const safe = new URLSearchParams();
    for (const [key, raw] of params) {
      const name = clean(key, 80);
      const sensitive = /pass|pwd|token|secret|auth|key|email|phone|mobile|session|cookie/i.test(name);
      safe.append(name, sensitive ? "[redacted]" : clean(raw, 160));
      if (safe.toString().length > 600) break;
    }
    const encoded = safe.toString();
    return encoded ? `?${encoded}`.slice(0, 700) : "";
  } catch {
    return "";
  }
}

function structuredValue(value: string | null, max: number) {
  return clean(value, max).replace(/^"|"$/g, "");
}

function deviceModel(headers: Headers, userAgent: string) {
  const clientHint = structuredValue(headers.get("sec-ch-ua-model"), 160);
  if (clientHint) return clientHint;
  const android = userAgent.match(/Android\s[^;)]*;\s*([^;)]+?)(?:\s+Build\/|[;)])/i)?.[1]?.trim();
  if (android) return clean(android, 160);
  if (/iPhone/i.test(userAgent)) return "iPhone (exact model not exposed)";
  if (/iPad/i.test(userAgent)) return "iPad (exact model not exposed)";
  if (/Macintosh|Mac OS X/i.test(userAgent)) return "Mac (hardware model not exposed)";
  if (/Windows/i.test(userAgent)) return "Windows PC (hardware model not exposed)";
  if (/bot|crawler|spider|headless|curl|wget|python|scanner/i.test(userAgent)) return "Automated client / model unavailable";
  return "Model not reported";
}

function severity(status: number, reason: string): ForensicSeverity {
  if (status === 429 || /admin login temporarily blocked|honeypot/i.test(reason)) return "critical";
  if (/sensitive path|invalid admin|cross-site|invalid host/i.test(reason)) return "high";
  if (status === 414 || status === 421 || status === 403) return "high";
  if (status === 405 || status === 401) return "medium";
  return "low";
}

function shortHmac(key: Buffer, value: string, length = 20) {
  return createHmac("sha256", key).update(value).digest("hex").slice(0, length);
}

function writeBudget(source: string, now: number) {
  if (now - forensicState.global.startedAt >= 60_000) {
    forensicState.global = { startedAt: now, count: 0 };
    for (const [key, counter] of forensicState.sources) {
      if (now - counter.startedAt >= 120_000) forensicState.sources.delete(key);
    }
  }
  if (forensicState.global.count >= GLOBAL_WRITES_PER_MINUTE) return false;
  let counter = forensicState.sources.get(source);
  if (!counter || now - counter.startedAt >= 60_000) {
    counter = { startedAt: now, count: 0 };
    forensicState.sources.set(source, counter);
  }
  if (counter.count >= SOURCE_WRITES_PER_MINUTE) return false;
  forensicState.global.count += 1;
  counter.count += 1;
  return true;
}

function cleanup(day: string) {
  if (lastCleanupDate === day || !existsSync(forensicRoot)) return;
  lastCleanupDate = day;
  const files = readdirSync(forensicRoot)
    .filter((name) => /^evidence-\d{4}-\d{2}-\d{2}\.ndjson$/.test(name))
    .sort()
    .reverse();
  for (const file of files.slice(RETENTION_DAYS + 1)) {
    try { rmSync(join(forensicRoot, file), { force: true }); } catch { }
  }
}

function lastChain(file: string) {
  const cached = forensicState.lastChains.get(file);
  if (cached) return cached;
  if (!existsSync(file)) return "GENESIS";
  try {
    const lines = readFileSync(file, "utf8").trim().split("\n");
    const envelope = JSON.parse(lines.at(-1) || "") as ForensicEnvelope;
    if (envelope.chain) {
      forensicState.lastChains.set(file, envelope.chain);
      return envelope.chain;
    }
  } catch { }
  return "GENESIS";
}

function chainValue(envelope: Omit<ForensicEnvelope, "chain">) {
  return createHmac("sha256", keys().integrity)
    .update([envelope.v, envelope.at, envelope.prev, envelope.iv, envelope.tag, envelope.data].join("|"))
    .digest("base64url");
}

function equal(left: string, right: string) {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function recordForensicEvent(input: {
  headers: Headers;
  path: string;
  query?: string;
  method: string;
  status: number;
  reason?: string;
  category?: "visit" | "security";
}) {
  try {
    const at = new Date();
    const keySet = keys();
    const sourceIp = safeIp(input.headers.get("cf-connecting-ip") || input.headers.get("x-real-ip"));
    const userAgent = clean(input.headers.get("user-agent"), 700);
    const sourceKey = shortHmac(keySet.fingerprint, sourceIp);
    if (!writeBudget(sourceKey, at.getTime())) return;

    const day = at.toISOString().slice(0, 10);
    mkdirSync(forensicRoot, { recursive: true });
    cleanup(day);
    const file = join(forensicRoot, `evidence-${day}.ndjson`);
    if (existsSync(file) && statSync(file).size >= MAX_DAILY_BYTES) return;

    const rayId = clean(input.headers.get("cf-ray"), 80);
    const reason = clean(input.reason, 140) || "Security policy";
    const path = clean(input.path, 300) || "/";
    const query = redactedQuery(input.query);
    const event: ForensicEvent = {
      id: randomUUID(),
      at: at.toISOString(),
      category: input.category || "security",
      sourceIp,
      sourceType: input.headers.has("cf-connecting-ip") ? "cloudflare" : "local",
      fingerprint: shortHmac(keySet.fingerprint, `${sourceIp}|${userAgent}`, 24),
      country: clean(input.headers.get("cf-ipcountry"), 2).toUpperCase() || "XX",
      city: clean(input.headers.get("cf-ipcity"), 100),
      region: clean(input.headers.get("cf-region"), 100),
      timezone: clean(input.headers.get("cf-timezone"), 80),
      rayId,
      edgeColo: rayId.includes("-") ? clean(rayId.split("-").at(-1), 8).toUpperCase() : "",
      method: clean(input.method, 12).toUpperCase(),
      path,
      query,
      status: input.status,
      reason,
      severity: severity(input.status, reason),
      signature: shortHmac(keySet.fingerprint, `${input.method}|${path}|${query}|${userAgent}`, 16),
      host: clean(input.headers.get("host"), 180),
      protocol: clean(input.headers.get("x-forwarded-proto"), 16) || "http",
      deviceModel: deviceModel(input.headers, userAgent),
      uaPlatform: structuredValue(input.headers.get("sec-ch-ua-platform"), 80),
      platformVersion: structuredValue(input.headers.get("sec-ch-ua-platform-version"), 80),
      architecture: structuredValue(input.headers.get("sec-ch-ua-arch"), 40),
      bitness: structuredValue(input.headers.get("sec-ch-ua-bitness"), 20),
      mobileHint: clean(input.headers.get("sec-ch-ua-mobile"), 20),
      browserBrands: clean(input.headers.get("sec-ch-ua"), 300),
      fullVersionList: clean(input.headers.get("sec-ch-ua-full-version-list"), 500),
      userAgent,
      acceptLanguage: clean(input.headers.get("accept-language"), 180),
      referrer: safeUrl(input.headers.get("referer")),
      origin: safeUrl(input.headers.get("origin")),
    };

    const previous = lastChain(file);
    const iv = randomBytes(12);
    const envelopeBase = { v: 1 as const, at: event.at, prev: previous, iv: iv.toString("base64url"), tag: "", data: "" };
    const aad = Buffer.from(`${envelopeBase.v}|${envelopeBase.at}|${envelopeBase.prev}`);
    const cipher = createCipheriv("aes-256-gcm", keySet.encryption, iv);
    cipher.setAAD(aad);
    const encrypted = Buffer.concat([cipher.update(JSON.stringify(event), "utf8"), cipher.final()]);
    envelopeBase.tag = cipher.getAuthTag().toString("base64url");
    envelopeBase.data = encrypted.toString("base64url");
    const envelope: ForensicEnvelope = { ...envelopeBase, chain: chainValue(envelopeBase) };
    appendFileSync(file, `${JSON.stringify(envelope)}\n`, { encoding: "utf8", mode: 0o600 });
    forensicState.lastChains.set(file, envelope.chain);
  } catch {
    // Evidence logging must never interrupt the public website.
  }
}

function decrypt(envelope: ForensicEnvelope) {
  const keySet = keys();
  const decipher = createDecipheriv("aes-256-gcm", keySet.encryption, Buffer.from(envelope.iv, "base64url"));
  decipher.setAAD(Buffer.from(`${envelope.v}|${envelope.at}|${envelope.prev}`));
  decipher.setAuthTag(Buffer.from(envelope.tag, "base64url"));
  const plaintext = Buffer.concat([decipher.update(Buffer.from(envelope.data, "base64url")), decipher.final()]);
  return JSON.parse(plaintext.toString("utf8")) as ForensicEvent;
}

function ranked(events: ForensicEvent[], selector: (event: ForensicEvent) => string, limit = 8) {
  const counts = new Map<string, number>();
  for (const event of events) {
    const label = selector(event);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [...counts.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value).slice(0, limit);
}

export function getForensicReport(days = RETENTION_DAYS, eventLimit = 200, deduplicateConnections = true): ForensicReport {
  const events: ForensicEvent[] = [];
  let checkedFiles = 0;
  let checkedRecords = 0;
  let errors = 0;
  const cutoff = Date.now() - Math.min(days, RETENTION_DAYS) * 86_400_000;
  if (existsSync(forensicRoot)) {
    const files = readdirSync(forensicRoot)
      .filter((name) => /^evidence-\d{4}-\d{2}-\d{2}\.ndjson$/.test(name))
      .sort()
      .slice(-(Math.min(days, RETENTION_DAYS) + 1));
    for (const name of files) {
      checkedFiles += 1;
      let previous = "GENESIS";
      let contents = "";
      try { contents = readFileSync(join(forensicRoot, name), "utf8"); } catch { errors += 1; continue; }
      for (const line of contents.split("\n")) {
        if (!line) continue;
        checkedRecords += 1;
        try {
          const envelope = JSON.parse(line) as ForensicEnvelope;
          const expected = chainValue({ v: envelope.v, at: envelope.at, prev: envelope.prev, iv: envelope.iv, tag: envelope.tag, data: envelope.data });
          if (envelope.prev !== previous || !equal(envelope.chain, expected)) errors += 1;
          previous = envelope.chain;
          const event = decrypt(envelope);
          if (Date.parse(event.at) >= cutoff) events.push(event);
        } catch {
          errors += 1;
        }
      }
    }
  }
  events.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const securityEvents = events.filter((event) => event.category !== "visit");
  const connectionEvents = events.filter((event) => event.category === "visit");
  const uniqueConnections = new Map<string, ForensicEvent>();
  for (const event of connectionEvents) {
    const key = deduplicateConnections ? event.fingerprint : event.id;
    if (!uniqueConnections.has(key)) uniqueConnections.set(key, event);
  }
  const last24h = securityEvents.filter((event) => Date.parse(event.at) >= Date.now() - 86_400_000);
  return {
    generatedAt: new Date().toISOString(),
    retentionDays: RETENTION_DAYS,
    integrity: { verified: errors === 0, checkedFiles, checkedRecords, errors },
    events: securityEvents.slice(0, eventLimit),
    connections: [...uniqueConnections.values()].slice(0, eventLimit),
    uniqueSources24h: new Set(last24h.map((event) => event.sourceIp)).size,
    events24h: last24h.length,
    critical24h: last24h.filter((event) => event.severity === "critical").length,
    topSources: ranked(last24h, (event) => `${event.sourceIp} · ${event.country}`),
    topSignatures: ranked(last24h, (event) => `${event.signature} · ${event.reason}`),
  };
}

export function forensicEvidenceExport() {
  const report = getForensicReport(RETENTION_DAYS, 10_000, false);
  const allEvents = [...report.events, ...report.connections].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return {
    format: "Fries Global encrypted forensic evidence export v1",
    exportedAt: new Date().toISOString(),
    notice: "Source IP identifies a network endpoint, not necessarily a person. Preserve original timestamps and Ray IDs when escalating to a provider or law enforcement.",
    integrity: report.integrity,
    retentionDays: report.retentionDays,
    eventCount: allEvents.length,
    events: allEvents,
  };
}
