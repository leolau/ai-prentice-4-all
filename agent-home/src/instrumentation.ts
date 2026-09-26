/**
 * Next.js instrumentation hook — runs once on server startup.
 *
 * Two independent 300s timeouts otherwise kill a large Folder Bridge
 * import, on opposite ends of the same request:
 *
 * 1. Node's HTTP *server* default `requestTimeout` (300s) — kills the
 *    inbound browser -> BFF request. The import route streams the request
 *    body straight to Supabase Storage with zero buffering; a 1 GiB file
 *    at 9 Mbps takes ~16 min, well past the default. Safe behind Caddy on
 *    a single-owner box; Caddy can enforce its own timeouts for
 *    multi-user deploys.
 *
 *    ESM module exports are non-configurable, so we can't wrap
 *    `createServer`. Instead we override the `requestTimeout` property
 *    descriptor on `Server.prototype` — when the constructor does
 *    `this.requestTimeout = 300000`, it calls our setter, which always
 *    stores 0 (disabled).
 *
 * 2. undici's *client* default `headersTimeout`/`bodyTimeout` (300s each)
 *    on Node's global `fetch` — kills the outbound BFF -> Supabase
 *    Storage request the storage-js SDK makes. Confirmed in production:
 *    a 1.03 GiB import failed at exactly 311s with Kong logging a client
 *    reset (`POST .../object/... 400 0`, no upstream storage-api log
 *    entry — Kong never got a chance to reject it; the client, i.e. our
 *    own outbound fetch, gave up first). Every other fix in this file's
 *    history addressed the *inbound* leg only, so large imports kept
 *    failing at ~5 minutes regardless.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const http = await import("node:http");
  const values = new WeakMap<object, number>();
  Object.defineProperty(http.Server.prototype, "requestTimeout", {
    get(this: object) {
      return values.get(this) ?? 0;
    },
    set(this: object, v: number) {
      void v;
      values.set(this, 0);
    },
    configurable: true,
    enumerable: true,
  });

  const { setGlobalDispatcher, Agent } = await import("undici");
  setGlobalDispatcher(
    new Agent({
      headersTimeout: 0,
      bodyTimeout: 0,
      // connect timeout stays at undici's default — only the "no data for
      // N seconds mid-request" clocks are the problem for a slow, large
      // upload; a hung TCP handshake should still fail fast.
    }),
  );

  // Boot marker: this line in journalctl is the proof the hook ran — the
  // alternative is inferring timeout behaviour from production failures.
  console.info("[instrumentation] http requestTimeout=0 + undici timeouts disabled");
}
