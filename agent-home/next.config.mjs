import { execSync } from "node:child_process";

/** Short git SHA of the checkout being built — what the deploy script
 *  reports as `deploy OK (<sha>)`. Baked into the bundle so the UI can show
 *  it and compare it against the server build (stale-bundle detection).
 *  HERMES_BUILD_SHA overrides it for CI/builds outside a git checkout. */
function resolveBuildSha() {
  if (process.env.HERMES_BUILD_SHA) return process.env.HERMES_BUILD_SHA;
  try {
    return execSync("git rev-parse --short HEAD", { encoding: "utf8" }).trim();
  } catch {
    return "dev";
  }
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: { NEXT_PUBLIC_HERMES_BUILD: resolveBuildSha() },
  // Unlike `web/` (a static SPA export served by the Python backend),
  // `agent-home` runs as a real Next.js **server** (App Router + route
  // handlers) behind Caddy on the prod box. It is the BFF (FG-20 Decision 1):
  // it holds the C1 principal session, proxies agent/authority calls to the
  // Python `/api/*` layer, and does server-side Supabase reads with the
  // principal's RLS context. So there is deliberately NO `output: "export"`.
  reactStrictMode: true,
  // Lint and typecheck run as dedicated `npm run lint` / `npm run typecheck`
  // steps (and in CI); don't couple them to the production build.
  eslint: { ignoreDuringBuilds: true },
  // `pg` is a server-only dependency; never bundle it into client chunks.
  serverExternalPackages: ["pg"],
  // An activation URL *is* the credential until it is redeemed, so the browser
  // must not hand it to the next site in a `Referer` header (a single click on
  // an outbound link would otherwise disclose a live token), and crawlers must
  // not index it. `robots` is also set as page metadata; the header covers the
  // fetch of the page itself.
  async headers() {
    return [
      {
        source: "/activate/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
          { key: "Cache-Control", value: "no-store, max-age=0" },
        ],
      },
      {
        // A survey link identifies one attendee; same treatment as activation.
        source: "/survey/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
          { key: "Cache-Control", value: "no-store, max-age=0" },
        ],
      },
    ];
  },
};

export default nextConfig;
