import { serve } from "@hono/node-server";
import { app } from "./app.js";

const port = Number(process.env.PORT || 3001);

serve({ fetch: app.fetch, port, hostname: "127.0.0.1" }, () => {
  console.log(`fries-global-api listening on http://127.0.0.1:${port}`);
});
