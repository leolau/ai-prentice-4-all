import { MobileShell } from "@/components/MobileShell";
import { SettingsView } from "@/components/settings/SettingsView";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/**
 * Settings page. Client preferences only (UI theme for now); no server state,
 * but still behind the principal gate so it lives inside the app shell.
 */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="Settings">
      <SettingsView />
    </MobileShell>
  );
}
