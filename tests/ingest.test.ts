import { describe, expect, it, beforeAll } from "vitest";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { app } from "../src/app.js";

beforeAll(() => {
  rmSync(join(process.cwd(), ".private"), { recursive: true, force: true });
  rmSync(join(process.cwd(), "ADMIN_ACCESS.txt"), { force: true });
  execFileSync(process.execPath, ["scripts/init-admin.mjs"], { cwd: process.cwd() });
});

describe("POST /api/ingest", () => {
  it("accepts visit events", async () => {
    const res = await app.request("/api/ingest", {
      method: "POST",
      headers: { "content-type": "application/json", "user-agent": "vitest" },
      body: JSON.stringify({ path: "/en", method: "GET", status: 200, kind: "visit" }),
    });
    expect(res.status).toBe(204);
  });
});
