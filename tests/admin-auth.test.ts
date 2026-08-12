import { describe, expect, it, beforeAll } from "vitest";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { createAdminSession, verifyAdminSession, verifyCredentials } from "../src/lib/admin-auth.js";

const root = process.cwd();

beforeAll(() => {
  rmSync(join(root, ".private"), { recursive: true, force: true });
  rmSync(join(root, "ADMIN_ACCESS.txt"), { force: true });
  execFileSync(process.execPath, ["scripts/init-admin.mjs"], { cwd: root });
});

describe("admin-auth", () => {
  it("rejects bad credentials", () => {
    expect(verifyCredentials("nope", "nope")).toBe(false);
  });

  it("round-trips a session token", () => {
    const token = createAdminSession();
    expect(verifyAdminSession(token)).toBe(true);
    expect(verifyAdminSession("tampered." + token)).toBe(false);
  });
});
