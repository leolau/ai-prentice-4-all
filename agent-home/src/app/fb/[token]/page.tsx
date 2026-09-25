import { MobileShell } from "@/components/MobileShell";
import { FolderBridgeView } from "@/components/files/FolderBridgeView";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/**
 * Catch-all alias for `/files/bridge` at an uncached path.
 *
 * A reverse proxy in front of the box caches ALL responses (including 404s)
 * by path and ignores cache-control headers. After a deploy the browser keeps
 * loading the old HTML at `/files/bridge` (referencing old JS chunks without
 * Folder Bridge progress pings), so the 30s total timeout kills any upload
 * over ~5 MB.
 *
 * This catch-all serves the Folder Bridge page at `/fb/<any-token>`. The proxy
 * has never seen these paths, so it forwards to the origin and the browser
 * gets the latest HTML + JS. The token is ignored — it just busts the cache.
 */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="Folder Bridge">
      <FolderBridgeView />
    </MobileShell>
  );
}
