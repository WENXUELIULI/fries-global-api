import { describe, expect, it, beforeAll } from "vitest";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { app } from "../src/app.js";
import { ADMIN_COOKIE, createAdminSession } from "../src/lib/admin-auth.js";

beforeAll(() => {
  rmSync(join(process.cwd(), ".private"), { recursive: true, force: true });
  rmSync(join(process.cwd(), "ADMIN_ACCESS.txt"), { force: true });
  execFileSync(process.execPath, ["scripts/init-admin.mjs"], { cwd: process.cwd() });
});

describe("admin summary", () => {
  it("rejects missing session", async () => {
    const res = await app.request("/admin/api/summary");
    expect(res.status).toBe(401);
  });

  it("returns json with valid session", async () => {
    const token = createAdminSession();
    const res = await app.request("/admin/api/summary", {
      headers: { cookie: `${ADMIN_COOKIE}=${token}` },
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty("generatedAt");
  });
});
