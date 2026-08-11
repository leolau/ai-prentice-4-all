"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActive, PRIMARY_NAV, SECONDARY_NAV } from "@/components/nav-items";
import { MoreSheet } from "@/components/MoreSheet";
import { NavGlyph } from "@/components/NavGlyph";

/**
 * Fixed bottom tab navigation — the primary mobile-first nav (FG-20 Wave A1).
 * Large touch targets, clears the phone home indicator via safe-area inset.
 * Hidden at `lg`+, where the persistent `SideNav` takes over. Tabs come from
 * the shared `nav-items` model; the 6th "More" tab opens a bottom sheet with
 * the secondary surfaces (Files, Activity, Settings, …) that only fit in the
 * desktop sidebar otherwise.
 */
export function BottomNav() {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = SECONDARY_NAV.some((i) => isActive(pathname, i.href));
  return (
    <>
      <nav
        data-component="BottomNav"
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--color-border)] bg-[var(--color-surface)]/95 backdrop-blur lg:hidden"
        style={{ paddingBottom: "var(--safe-bottom)" }}
      >
        <ul className="mx-auto flex max-w-md items-stretch justify-around md:max-w-2xl">
          {PRIMARY_NAV.map((tab) => {
            const active = isActive(pathname, tab.href);
            return (
              <li key={tab.href} className="flex-1">
                <Link
                  href={tab.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex h-16 flex-col items-center justify-center gap-1 text-xs ${
                    active ? "text-[var(--color-accent)]" : "text-[var(--color-muted)]"
                  }`}
                >
                  <span className="flex h-5 items-center text-xl leading-none">
                    <NavGlyph glyph={tab.glyph} />
                  </span>
                  {tab.label}
                </Link>
              </li>
            );
          })}
          <li className="flex-1">
            <button
              type="button"
              onClick={() => setMoreOpen(true)}
              aria-current={moreActive ? "page" : undefined}
              aria-label="More"
              className={`flex h-16 w-full flex-col items-center justify-center gap-1 text-xs ${
                moreActive ? "text-[var(--color-accent)]" : "text-[var(--color-muted)]"
              }`}
            >
              <span aria-hidden className="text-xl leading-none">
                ⋯
              </span>
              More
            </button>
          </li>
        </ul>
      </nav>
      {moreOpen ? <MoreSheet onClose={() => setMoreOpen(false)} /> : null}
    </>
  );
}
