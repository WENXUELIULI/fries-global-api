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

describe("GET /api/presence", () => {
  it("returns 204", async () => {
    const res = await app.request("/api/presence?page=%2Fen&profile=1", {
      method: "GET",
      headers: { "user-agent": "vitest" },
    });
    expect(res.status).toBe(204);
    expect(res.headers.get("cache-control")).toMatch(/no-store/);
  });
});
