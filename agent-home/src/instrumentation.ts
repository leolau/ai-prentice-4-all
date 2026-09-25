/**
 * Next.js instrumentation hook — runs once on server startup.
 *
 * Disables Node's default 300s HTTP request timeout so streaming uploads
 * (Folder Bridge imports of any size) aren't killed mid-transfer. The
 * import route streams the request body straight to Supabase Storage with
 * zero buffering; a 1 GiB file at 9 Mbps takes ~16 min, well past the 300s
 * default. Safe behind Caddy on a single-owner box; Caddy can enforce its
 * own timeouts for multi-user deploys.
 *
 * ESM module exports are non-configurable, so we can't wrap `createServer`.
 * Instead we override the `requestTimeout` property descriptor on
 * `Server.prototype` — when the constructor does `this.requestTimeout =
 * 300000`, it calls our setter, which always stores 0 (disabled).
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const http = await import("node:http");
  const values = new WeakMap<object, number>();
  Object.defineProperty(http.Server.prototype, "requestTimeout", {
    get(this: object) {
      return values.get(this) ?? 0;
    },
    set(this: object, _v: number) {
      values.set(this, 0);
    },
    configurable: true,
    enumerable: true,
  });
}
