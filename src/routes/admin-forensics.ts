import { Hono } from "hono";
import { ADMIN_COOKIE, getCookie, verifyAdminSession } from "../lib/admin-auth.js";
import { forensicEvidenceExport } from "../lib/forensics.js";

export const adminForensicsRoutes = new Hono();

adminForensicsRoutes.get("/admin/api/forensics", (c) => {
  const token = getCookie(c.req.header("cookie"), ADMIN_COOKIE);
  if (!verifyAdminSession(token)) return c.text("Unauthorized", 401);
  const day = new Date().toISOString().slice(0, 10);
  return c.json(forensicEvidenceExport(), 200, {
    "Cache-Control": "private, no-store, max-age=0",
    "Content-Disposition": `attachment; filename="fries-global-forensics-${day}.json"`,
  });
});
