import Link from "next/link";

import type { ProjectLink } from "@/types";

/**
 * One pointer row. A link is never an authority (§11 rule 5): until the
 * owning store resolves it under the caller's principal, the row renders
 * greyed from its cached label — no content, no leak.
 */
export function LinkRow({ link }: { link: ProjectLink }) {
  const unresolved = link.resolved === null;
  const body = (
    <>
      <span
        className={`block truncate text-sm ${
          unresolved ? "text-[var(--color-muted)]" : ""
        }`}
      >
        {link.label ?? link.ref}
      </span>
      <span className="block text-xs text-[var(--color-muted)]">
        {link.profile}
      </span>
    </>
  );

  const href =
    link.kind === "url"
      ? link.ref
      : link.kind === "arrival"
        ? `/inbox/${encodeURIComponent(link.ref)}`
        : link.kind === "todo"
          ? `/todos/${encodeURIComponent(link.ref)}`
          : null;

  if (link.kind === "url") {
    return (
      <a
        href={link.ref}
        target="_blank"
        rel="noreferrer"
        data-component="LinkRow"
        className="block rounded-lg bg-[var(--color-surface-2)] px-3 py-2"
      >
        {body}
      </a>
    );
  }
  if (href) {
    return (
      <Link
        href={href}
        data-component="LinkRow"
        className="block rounded-lg bg-[var(--color-surface-2)] px-3 py-2"
      >
        {body}
      </Link>
    );
  }
  return (
    <div
      data-component="LinkRow"
      className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2"
    >
      {body}
    </div>
  );
}
