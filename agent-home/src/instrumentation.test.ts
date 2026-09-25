import { describe, expect, it } from "vitest";

describe("instrumentation register()", () => {
  it("disables Node's HTTP server request timeout", async () => {
    const originalRuntime = process.env.NEXT_RUNTIME;
    process.env.NEXT_RUNTIME = "nodejs";
    try {
      const { register } = await import("@/instrumentation");
      await register();
      const http = await import("node:http");
      const server = new http.Server();
      // The constructor sets requestTimeout to Node's 300000ms default;
      // our patched setter must always coerce it to 0 (disabled).
      expect(server.requestTimeout).toBe(0);
      server.requestTimeout = 300_000;
      expect(server.requestTimeout).toBe(0);
    } finally {
      process.env.NEXT_RUNTIME = originalRuntime;
    }
  });

  it("replaces the global fetch dispatcher so outbound requests aren't killed at 300s", async () => {
    const originalRuntime = process.env.NEXT_RUNTIME;
    process.env.NEXT_RUNTIME = "nodejs";
    try {
      const undici = await import("undici");
      const before = undici.getGlobalDispatcher();
      const { register } = await import("@/instrumentation");
      await register();
      const after = undici.getGlobalDispatcher();
      // A new Agent must have been installed — Node's undici default
      // Agent has a 300s headersTimeout/bodyTimeout that killed a real
      // 1.03 GiB Folder Bridge import at 311s in production (BFF ->
      // Supabase Storage), even after the server-side timeout was fixed.
      expect(after).not.toBe(before);
    } finally {
      process.env.NEXT_RUNTIME = originalRuntime;
    }
  });

  it("is a no-op outside the Node.js runtime (edge/browser)", async () => {
    const originalRuntime = process.env.NEXT_RUNTIME;
    process.env.NEXT_RUNTIME = "edge";
    try {
      const undici = await import("undici");
      const before = undici.getGlobalDispatcher();
      const { register } = await import("@/instrumentation");
      await register();
      expect(undici.getGlobalDispatcher()).toBe(before);
    } finally {
      process.env.NEXT_RUNTIME = originalRuntime;
    }
  });
});
