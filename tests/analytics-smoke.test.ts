import { describe, expect, it, beforeAll } from "vitest";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { getAnalyticsSummary, recordAnalyticsEvent } from "../src/lib/analytics.js";

beforeAll(() => {
  rmSync(join(process.cwd(), ".private"), { recursive: true, force: true });
  rmSync(join(process.cwd(), "ADMIN_ACCESS.txt"), { force: true });
  execFileSync(process.execPath, ["scripts/init-admin.mjs"], { cwd: process.cwd() });
});

describe("analytics smoke", () => {
  it("records a visit and returns summary shape", () => {
    recordAnalyticsEvent({
      headers: new Headers({ "user-agent": "vitest", "cf-connecting-ip": "1.2.3.4" }),
      path: "/en",
      method: "GET",
      status: 200,
      kind: "visit",
    });
    const summary = getAnalyticsSummary();
    expect(summary).toHaveProperty("generatedAt");
    expect(summary).toHaveProperty("views24h");
    expect(summary).toHaveProperty("hourly");
  });
});
