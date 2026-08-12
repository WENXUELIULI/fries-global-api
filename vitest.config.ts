import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    // Tests share .private/ via init-admin; avoid parallel beforeAll races.
    fileParallelism: false,
  },
});
