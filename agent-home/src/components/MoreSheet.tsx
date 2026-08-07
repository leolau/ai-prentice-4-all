"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, SECONDARY_NAV, type NavItem } from "@/components/nav-items";

/**
 * Mobile overflow sheet — the 6th "More" tab in the bottom nav opens this
 * bottom-aligned sheet listing every `SECONDARY_NAV` destination. On desktop
 * the same items live in the `SideNav` "More" section; this sheet is the mobile
 * equivalent so Files, Activity, Settings, etc. are reachable from a phone.
 */
function MoreLink({ item, active, onClose }: { item: NavItem; active: boolean; onClose: () => void }) {
  return (
    <li>
      <Link
        href={item.href}
        onClick={onClose}
        aria-current={active ? "page" : undefined}
        className={`flex items-center gap-3 rounded-xl px-3 py-3 text-sm ${
          active
            ? "bg-[var(--color-surface-2)] text-[var(--color-accent)]"
            : "text-[var(--color-text)] hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <span aria-hidden className="text-lg leading-none">
          {item.glyph}
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium">{item.label}</span>
          {item.hint ? (
            <span className="truncate text-xs text-[var(--color-muted)]">{item.hint}</span>
          ) : null}
        </span>
      </Link>
    </li>
  );
}

export function MoreSheet({ onClose }: { onClose: () => void }) {
  const pathname = usePathname();
  return (
    <div
      data-component="MoreSheet"
      className="fixed inset-0 z-50 flex items-end bg-black/50"
      onClick={onClose}
    >
      <div
        className="mx-auto flex w-full max-w-md flex-col rounded-t-2xl border-x border-t border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        style={{ paddingBottom: "calc(var(--safe-bottom) + 1rem)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">More</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>
        <ul className="flex flex-col gap-1">
          {SECONDARY_NAV.map((item) => (
            <MoreLink
              key={item.href}
              item={item}
              active={isActive(pathname, item.href)}
              onClose={onClose}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}
