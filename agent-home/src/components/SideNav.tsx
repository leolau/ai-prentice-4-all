"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NavGlyph } from "@/components/NavGlyph";
import { isActive, PRIMARY_NAV, SECONDARY_NAV, type NavItem } from "@/components/nav-items";
import { usePersistentState } from "@/lib/use-persistent-state";

const COLLAPSE_STORAGE_KEY = "agent-home:sidenav-collapsed";

/**
 * Desktop/tablet-wide left sidebar (FG-20 adaptive shell). Hidden below `lg`,
 * where the fixed `BottomNav` is the primary navigation instead. Shares its
 * destinations with `BottomNav` via `nav-items` so the two never drift, and
 * adds a "More" section for the secondary surfaces that only fit here. Can be
 * collapsed to an icon-only rail; the choice persists in `localStorage`.
 */
function SideLink({
  item,
  active,
  collapsed,
  badgeCount,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  badgeCount?: number;
}) {
  return (
    <li>
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        title={collapsed ? item.label : undefined}
        className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm ${
          collapsed ? "justify-center" : ""
        } ${
          active
            ? "bg-[var(--color-surface-2)] text-[var(--color-accent)]"
            : "text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
        }`}
      >
        <span className="flex items-center text-lg leading-none">
          <NavGlyph glyph={item.glyph} />
        </span>
        {collapsed ? (
          <span className="sr-only">{item.label}</span>
        ) : (
          <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{item.label}</span>
              {item.hint ? (
                <span className="truncate text-xs text-[var(--color-muted)]">
                  {item.hint}
                </span>
              ) : null}
            </span>
            {badgeCount ? (
              <span className="shrink-0 rounded-full bg-[var(--color-accent)] px-1.5 text-[10px] leading-4 text-[var(--color-surface)]">
                {badgeCount}
              </span>
            ) : null}
          </span>
        )}
      </Link>
    </li>
  );
}

export function SideNav({ badgeCounts = {} }: { badgeCounts?: Record<string, number> }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = usePersistentState<boolean>(
    COLLAPSE_STORAGE_KEY,
    false,
    (raw) => raw === "1",
    (value) => (value ? "1" : "0"),
  );

  return (
    <aside
      data-component="SideNav"
      aria-label="Primary"
      className={`sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] lg:flex ${
        collapsed ? "w-16" : "w-64"
      }`}
      style={{ paddingTop: "var(--safe-top)" }}
    >
      <div
        className={`flex items-center gap-2 px-3 py-4 ${
          collapsed ? "justify-center" : "justify-between px-5"
        }`}
      >
        {collapsed ? null : (
          <div className="min-w-0">
            <p className="truncate text-base font-semibold tracking-tight">
              Agent Home
            </p>
            <p className="truncate text-xs text-[var(--color-muted)]">
              Hermes · mobile-first
            </p>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="shrink-0 rounded-lg border border-[var(--color-border)] px-2 py-1 text-sm text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <span aria-hidden>{collapsed ? "»" : "«"}</span>
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        <ul className="flex flex-col gap-1">
          {PRIMARY_NAV.map((item) => (
            <SideLink
              key={item.href}
              item={item}
              active={isActive(pathname, item.href)}
              collapsed={collapsed}
              badgeCount={item.badge ? badgeCounts[item.badge] : undefined}
            />
          ))}
        </ul>
        {collapsed ? (
          <div className="my-3 border-t border-[var(--color-border)]" />
        ) : (
          <p className="px-3 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
            More
          </p>
        )}
        <ul className="flex flex-col gap-1">
          {SECONDARY_NAV.map((item) => (
            <SideLink
              key={item.href}
              item={item}
              active={isActive(pathname, item.href)}
              collapsed={collapsed}
            />
          ))}
        </ul>
      </nav>
    </aside>
  );
}
