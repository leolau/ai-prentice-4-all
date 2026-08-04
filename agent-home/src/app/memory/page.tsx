import { MobileShell } from "@/components/MobileShell";
import { MemoryView } from "@/components/memory/MemoryView";
import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { MemoryRowsResponse, MemorySummary } from "@/types";

// Per-principal HTML — never cached.
export const dynamic = "force-dynamic";

/**
 * FG-23 A2 — the `/memory` tab on `agent-home` (the phone). The first paint is
 * RSC: the server resolves the principal, fetches the summary + first page of
 * rows from the Python API, and renders. Search, paging, the map and query
 * placement are client-side refetches through BFF handlers under
 * `src/app/api/memory/*`.
 *
 * A 409 here means "authenticated, but no principal" — render a plain sentence
 * (an enrolment problem, not a bug) rather than raw JSON.
 */
export default async function Page() {
  await requirePrincipal();

  let summary: MemorySummary | null = null;
  let first: MemoryRowsResponse | null = null;
  let error: string | null = null;
  let noPrincipal = false;

  try {
    const client = await apiClientForRequest();
    [summary, first] = await Promise.all([
      client.memorySummary(),
      client.memoryRows({ limit: 25 }),
    ]);
  } catch (err) {
    // The status, not the message: `HermesApiError.message` embeds the request
    // path, so matching the string "409" in it can fire on a path.
    if (err instanceof HermesApiError && err.status === 409) {
      noPrincipal = true;
    } else {
      error = err instanceof Error ? err.message : "Failed to load memory";
    }
  }

  return (
    <MobileShell title="Memory">
      {noPrincipal ? (
        <div
          data-component="MemoryNoPrincipal"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Your login isn&apos;t linked to a memory principal yet. Ask the owner
          to enrol you with <code className="font-mono">hermes owner alias</code>.
        </div>
      ) : error || !summary || !first ? (
        <div
          data-component="MemoryError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load memory ({error}).
        </div>
      ) : (
        <MemoryView summary={summary} initialRows={first} />
      )}
    </MobileShell>
  );
}
