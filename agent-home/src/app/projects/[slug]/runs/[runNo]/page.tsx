import { MobileShell } from "@/components/MobileShell";
import { RunView } from "@/components/projects/RunView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { ProjectRun } from "@/types";

// Reads the live principal + the run row per request — never at build time.
export const dynamic = "force-dynamic";

/**
 * **One run's page** (§7): the cards it moved, what it delivered, its cost
 * and its retro — the record a score gets pinned to (score-it lands in 9b).
 */
export default async function Page({
  params,
}: {
  params: Promise<{ slug: string; runNo: string }>;
}) {
  await requirePrincipal();
  const { slug, runNo } = await params;
  const runNoInt = Number(runNo);

  if (!Number.isInteger(runNoInt) || runNoInt < 1) {
    return (
      <MobileShell title="Run">
        <div
          data-component="RunPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          That run number doesn&apos;t look right.
        </div>
      </MobileShell>
    );
  }

  const client = await apiClientForRequest();
  // Fetch under the try, render outside it.
  let run: ProjectRun | null = null;
  let runError: string | null = null;
  try {
    run = await client.projectRun(slug, runNoInt);
  } catch (err) {
    runError = err instanceof Error ? err.message : "Failed to load";
  }

  if (!run) {
    return (
      <MobileShell title="Run">
        <div
          data-component="RunPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load run {runNoInt} ({runError}).
        </div>
      </MobileShell>
    );
  }

  return (
    <MobileShell title={`Run ${runNoInt}`}>
      <RunView slug={slug} run={run} />
    </MobileShell>
  );
}
