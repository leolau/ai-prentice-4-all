import { MobileShell } from "@/components/MobileShell";
import { PAGE_SIZE, UsersView } from "@/components/users/UsersView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { DirectoryResponse, MembersResponse } from "@/types";

// Reads the live principal (cookie), the directory and — for an admin — the
// first roster page, per request. Never at build time.
export const dynamic = "force-dynamic";

/**
 * FG-26 — the **Users** screen, replacing `/members`.
 *
 * Unlike the old roster this page is not owner/admin-only: every enrolled
 * principal may read the directory, because somebody who cannot see who else is
 * in the profile cannot address or delegate to them. Management (enrolment,
 * roles, activation links, import, audit) is loaded only for owner/admin, and
 * every BFF route re-checks that gate — as does Python, which is the authority.
 *
 * Both lists come from **this profile's** principals. The box-wide account table
 * is deliberately never the source: one Supabase serves every profile, so
 * listing accounts would expose people enrolled somewhere else entirely.
 */
export default async function Page() {
  const principal = await requirePrincipal();
  const canManage = principal.role === "owner" || principal.role === "admin";

  let directory: DirectoryResponse = { configured: false, entries: [], total: 0 };
  let page: MembersResponse | null = null;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    directory = await client.directory({ limit: 200 });
    if (canManage) page = await client.members({ limit: PAGE_SIZE, offset: 0 });
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load users";
  }

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
          profile={page?.profile ?? directory.profile ?? "default"}
          directory={directory}
          initialPage={page}
        />
      )}
    </MobileShell>
  );
}
