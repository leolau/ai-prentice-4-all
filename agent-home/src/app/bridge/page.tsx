import { MobileShell } from "@/components/MobileShell";
import { FolderBridgeView } from "@/components/files/FolderBridgeView";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/**
 * Alias for `/files/bridge` at an uncached path. A reverse proxy in front of
 * the box caches the HTML at `/files/bridge` by path and ignores cache-control
 * headers, so after a deploy the browser keeps loading the old HTML (which
 * references old JS chunks without Folder Bridge progress pings). This route
 * serves the exact same page at a path the proxy has never cached, so the
 * browser always gets the latest HTML and JS.
 */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="Folder Bridge">
      <FolderBridgeView />
    </MobileShell>
  );
}
