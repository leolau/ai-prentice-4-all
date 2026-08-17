import { notFound } from "next/navigation";

import { MobileShell } from "@/components/MobileShell";
import { CardDetailView } from "@/components/projects/CardDetailView";
import { HermesApiError } from "@/lib/api/client";
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
  try {
    card = await client.projectCard(slug, id);
  } catch (err) {
    // 404 = no such card (or no such project for this caller) — Next's
    // not-found, not a load error that prints upstream detail (F7).
    if (err instanceof HermesApiError && err.status === 404) notFound();
  }

  if (!card) {
    return (
      <MobileShell title="Card">
        <div
          data-component="CardPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load this card right now.
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
