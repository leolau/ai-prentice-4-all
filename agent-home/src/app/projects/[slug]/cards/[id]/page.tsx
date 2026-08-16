import { MobileShell } from "@/components/MobileShell";
import { CardDetailView } from "@/components/projects/CardDetailView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { ProjectCardDetail } from "@/types";

// Reads the live principal + the card row per request — never at build time.
export const dynamic = "force-dynamic";

/**
 * **One card on a project board** (§13): a read-only look at what the card
 * knows — the board remains the surface that mutates it.
 */
export default async function Page({
  params,
}: {
  params: Promise<{ slug: string; id: string }>;
}) {
  await requirePrincipal();
  const { slug, id } = await params;
  const client = await apiClientForRequest();

  // Fetch under the try, render outside it.
  let card: ProjectCardDetail | null = null;
  let cardError: string | null = null;
  try {
    card = await client.projectCard(slug, id);
  } catch (err) {
    cardError = err instanceof Error ? err.message : "Failed to load";
  }

  if (!card) {
    return (
      <MobileShell title="Card">
        <div
          data-component="CardPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load this card ({cardError}).
        </div>
      </MobileShell>
    );
  }

  return (
    <MobileShell title={card.title}>
      <CardDetailView slug={slug} card={card} />
    </MobileShell>
  );
}
