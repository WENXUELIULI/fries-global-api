import { Hono } from "hono";
import { adminExportRoutes } from "./routes/admin-export.js";
import { adminForensicsRoutes } from "./routes/admin-forensics.js";
import { adminLoginRoutes } from "./routes/admin-login.js";
import { adminLogoutRoutes } from "./routes/admin-logout.js";
import { adminSessionRoutes } from "./routes/admin-session.js";
import { adminSummaryRoutes } from "./routes/admin-summary.js";
import { ingestRoutes } from "./routes/ingest.js";
import { presenceRoutes } from "./routes/presence.js";

export const app = new Hono();

app.get("/health", (c) => c.json({ ok: true }));
app.route("/", presenceRoutes);
app.route("/", ingestRoutes);
app.route("/", adminLoginRoutes);
app.route("/", adminLogoutRoutes);
app.route("/", adminSessionRoutes);
app.route("/", adminSummaryRoutes);
app.route("/", adminExportRoutes);
app.route("/", adminForensicsRoutes);
