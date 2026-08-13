"use client";

import { useState } from "react";

import { activationUrl } from "@/components/users/api";

/**
 * The one-and-only sighting of an activation link.
 *
 * The token is stored as a SHA-256 hash, so this is genuinely the last time
 * anybody can read it — the card says so, and offers Copy rather than expecting
 * the admin to select short-lived text by hand. A lost link is regenerated
 * (which revokes this one), never recovered.
 */
export function InvitationLink({
  label,
  path,
  expiresAt,
  onDismiss,
}: {
  /** Who the link is for, in human terms (an email or a display name). */
  label: string;
  path: string;
  expiresAt?: string | null;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const url = activationUrl(path);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // Clipboard permission denied — the link is on screen to select by hand.
    }
  }

  return (
    <div
      data-component="InvitationLink"
      className="rounded-2xl border border-[var(--color-accent)] bg-[var(--color-surface)] p-4"
    >
      <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Activation link for {label}
      </p>
      <p className="mt-1 break-all font-mono text-xs">{url}</p>
      <p className="mt-2 text-xs text-[var(--color-muted)]">
        Send it over a channel you trust. It is single-use, it expires
        {expiresAt ? ` at ${new Date(expiresAt).toLocaleTimeString()}` : " shortly"},
        and it is <strong>shown only now</strong> — the server keeps just a hash.
        If it is lost or expires, use Regenerate.
      </p>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={copy}
          className="rounded-lg bg-[var(--color-accent)] px-3 py-1 text-xs font-medium text-[var(--color-accent-fg)]"
        >
          {copied ? "Copied" : "Copy link"}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs"
        >
          Done
        </button>
      </div>
    </div>
  );
}
