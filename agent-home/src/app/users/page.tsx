import { MobileShell } from "@/components/MobileShell";
import { PAGE_SIZE } from "@/components/users/api";
import { UsersView } from "@/components/users/UsersView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type {
  AdministeredProfileEntry,
  DirectoryResponse,
  MembersResponse,
} from "@/types";

// Reads the live principal (cookie), the directory and — for an admin — the
// first roster page, per request. Never at build time.
export const dynamic = "force-dynamic";

/** The profile the console administers when the URL names none. */
const DEFAULT_PROFILE = "default";

/**
 * FG-26 — the **Users** screen, replacing `/members`.
 *
 * Unlike the old roster this page is not owner/admin-only: every enrolled
 * principal may read the directory, because somebody who cannot see who else
 * is in the profile cannot address or delegate to them. Management (enrolment,
 * roles, activation links, import, audit) is loaded only for owner/admin, and
 * every BFF route re-checks that gate — as does Python, which is the authority.
 *
 * Both lists come from **this profile's** principals. The box-wide account table
 * is deliberately never the source: one Supabase serves every profile, so
 * listing accounts would expose people enrolled somewhere else entirely.
 *
 * FG-28 — `?profile=<name>` selects which profile this console administers: the
 * client is bound to it so the directory, roster and the create form all read
 * and write under that scope. The switcher in {@link UsersView} lists only
 * profiles where the caller holds an `admin`/`owner` row, so a `member` here
 * sees the current profile as a read-only label.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ profile?: string }>;
}) {
  const principal = await requirePrincipal();
  const canManage = principal.role === "owner" || principal.role === "admin";

  const { profile: requestedProfile } = await searchParams;
  const profileName = (requestedProfile ?? "").trim() || DEFAULT_PROFILE;

  let directory: DirectoryResponse = {
    configured: false,
    entries: [],
    total: 0,
  };
  let page: MembersResponse | null = null;
  let administered: AdministeredProfileEntry[] = [];
  let error: string | null = null;
  try {
    // Bind every per-profile read to the selected profile: a profile is a
    // whole HERMES_HOME, so directory/roster/create form must all see the
    // same scope. The administered list is cross-profile —
    // `administeredProfiles` bypasses the bound profile internally so it
    // does not echo it back as the only hit.
    const client = await apiClientForRequest({ profile: profileName });
    directory = await client.directory({ limit: 200 });
    if (canManage) {
      page = await client.members({ limit: PAGE_SIZE, offset: 0 });
      administered = (
        await client.administeredProfiles().catch(() => ({ profiles: [] }))
      ).profiles;
    }
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load users";
  }

  const profile = page?.profile ?? directory.profile ?? profileName;

  return (
    <MobileShell title="Users">
      {error ? (
        <div
          data-component="UsersError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load users ({error}).
        </div>
      ) : (
        <UsersView
          role={principal.role}
          userId={principal.user_id}
          profile={profile}
          administeredProfiles={administered}
          directory={directory}
          initialPage={page}
        />
      )}
    </MobileShell>
  );
}