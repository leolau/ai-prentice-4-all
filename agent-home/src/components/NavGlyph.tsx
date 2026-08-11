"use client";

import { useLinkStatus } from "next/link";

import { Spinner } from "@/components/ui/Spinner";

/**
 * A nav item's glyph, replaced by a spinner while that item's navigation is in
 * flight.
 *
 * `useLinkStatus` only reports for the nearest enclosing `Link`, so this has to
 * be a child of the `Link` rather than something the nav computes for itself.
 *
 * The route skeleton tells the user the app is loading; this tells them *which
 * destination* it decided to load. Without it a slow page looks like a missed
 * tap and invites a second one on a different tab.
 */
export function NavGlyph({ glyph }: { glyph: string }) {
  const { pending } = useLinkStatus();
  if (pending) {
    return (
      <span role="status" className="inline-flex text-[var(--color-accent)]">
        <Spinner size="md" />
        <span className="sr-only">Loading…</span>
      </span>
    );
  }
  return <span aria-hidden>{glyph}</span>;
}
