"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { usePathname } from "next/navigation";

import "@/components/coral/coral-apps";
import "./coral.css";
import {
  buildCoralLayout,
  clusterMemberAngles,
  isAppActive,
  petalAngle,
  petalPosition,
  type CoralPetal,
} from "@/components/coral/coral-registry";
import { AppPetal, PetalBubble } from "@/components/coral/CoralPetal";

/** Bloom radii in px (the lg viewport scales the whole bloom ×1.25 in CSS). */
const PETAL_RADIUS = 150;
const MEMBER_RING = 260;
const STAGGER_MS = 24;

/**
 * Coral — the floating launcher and the app's only navigation surface
 * (design: docs/design/coral-app-framework.md). One button, bottom-right;
 * tapping it blooms every registered app into an arc above it, with cluster
 * petals fanning their members on an outer ring.
 *
 * The layout comes from the registry, so this component never names a
 * destination — apps register, the launcher renders what's registered.
 */
export function CoralHost({
  badgeCounts = {},
}: {
  badgeCounts?: Record<string, number>;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [openClusterId, setOpenClusterId] = useState<string | null>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const bloomRef = useRef<HTMLDivElement>(null);

  const petals = buildCoralLayout();

  const close = (reason: "dismiss" | "navigate") => {
    setOpen(false);
    setOpenClusterId(null);
    if (reason === "dismiss") fabRef.current?.focus();
  };

  // Esc dismisses one layer at a time: cluster fan first, then the bloom.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpenClusterId(null);
      if (!openClusterId) setOpen(false);
      fabRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openClusterId]);

  // Opening moves focus into the bloom; arrow keys rove between petals.
  useEffect(() => {
    if (!open) return;
    bloomRef.current
      ?.querySelector<HTMLElement>("a[role='menuitem'], button[role='menuitem']")
      ?.focus();
  }, [open]);

  const onBloomKeyDown = (e: ReactKeyboardEvent) => {
    const keys = ["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"];
    if (!keys.includes(e.key)) return;
    const items = Array.from(
      bloomRef.current?.querySelectorAll<HTMLElement>(
        "a[role='menuitem'], button[role='menuitem']",
      ) ?? [],
    );
    const current = items.indexOf(document.activeElement as HTMLElement);
    if (items.length === 0) return;
    e.preventDefault();
    const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
    const next =
      current === -1
        ? 0
        : (current + (forward ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  };

  return (
    <div data-component="CoralHost">
      {open ? (
        <>
          <div
            className="coral-backdrop fixed inset-0 z-40 bg-black/60"
            onClick={() => close("dismiss")}
            aria-hidden
          />
          <div
            ref={bloomRef}
            id="coral-bloom"
            role="menu"
            aria-label="Coral launcher"
            onKeyDown={onBloomKeyDown}
            className="coral-bloom fixed z-50 h-0 w-0"
            style={{
              right: "calc(1rem + 28px)",
              bottom: "calc(var(--safe-bottom) + 1rem + 28px)",
            }}
          >
            {petals.map((petal, index) => (
              <PetalSlot
                key={petal.type === "app" ? petal.app.id : petal.id}
                petal={petal}
                index={index}
                total={petals.length}
                pathname={pathname}
                badgeCounts={badgeCounts}
                openClusterId={openClusterId}
                onToggleCluster={(id) =>
                  setOpenClusterId((cur) => (cur === id ? null : id))
                }
                onNavigate={() => close("navigate")}
              />
            ))}
          </div>
        </>
      ) : null}
      <button
        ref={fabRef}
        type="button"
        onClick={() => (open ? close("dismiss") : setOpen(true))}
        aria-expanded={open}
        aria-controls={open ? "coral-bloom" : undefined}
        aria-label={open ? "Close Coral menu" : "Open Coral menu"}
        className="coral-fab fixed z-[60] flex h-14 w-14 items-center justify-center rounded-full text-xl text-[var(--color-accent-fg)]"
        style={{
          right: "1rem",
          bottom: "calc(var(--safe-bottom) + 1rem)",
          background:
            "linear-gradient(135deg, var(--color-accent), color-mix(in srgb, var(--color-accent) 60%, #ff7e6b))",
        }}
      >
        <span aria-hidden>{open ? "✕" : "❋"}</span>
      </button>
    </div>
  );
}

function PetalSlot({
  petal,
  index,
  total,
  pathname,
  badgeCounts,
  openClusterId,
  onToggleCluster,
  onNavigate,
}: {
  petal: CoralPetal;
  index: number;
  total: number;
  pathname: string;
  badgeCounts: Record<string, number>;
  openClusterId: string | null;
  onToggleCluster: (id: string) => void;
  onNavigate: () => void;
}) {
  const angle = petalAngle(index, total);
  const { x, y } = petalPosition(index, total, PETAL_RADIUS);
  const delayMs = index * STAGGER_MS;

  if (petal.type === "app") {
    return (
      <span data-coral-item role="none">
        <AppPetal
          app={petal.app}
          x={x}
          y={y}
          delayMs={delayMs}
          active={isAppActive(pathname, petal.app.route)}
          badgeCount={
            petal.app.badgeSlot ? badgeCounts[petal.app.badgeSlot] : undefined
          }
          onClose={onNavigate}
        />
      </span>
    );
  }

  const active = petal.members.some((m) => isAppActive(pathname, m.route));
  const hasBadge = petal.members.some(
    (m) => m.badgeSlot && (badgeCounts[m.badgeSlot] ?? 0) > 0,
  );
  const fanOpen = openClusterId === petal.id;
  const memberAngles = clusterMemberAngles(angle, petal.members.length);

  return (
    <>
      <span data-coral-item role="none">
        <PetalBubble x={x} y={y} delayMs={delayMs} active={active || fanOpen}>
          <button
            type="button"
            role="menuitem"
            aria-expanded={fanOpen}
            aria-haspopup="true"
            title={petal.label}
            onClick={() => onToggleCluster(petal.id)}
            className={`relative flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-full border bg-[var(--color-surface)] shadow-lg transition-colors hover:bg-[var(--color-surface-2)] focus-visible:outline-2 focus-visible:outline-[var(--color-accent)] ${
              active || fanOpen
                ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                : "border-[var(--color-border)] text-[var(--color-text)]"
            }`}
          >
            <span aria-hidden className="text-lg leading-none">
              {petal.glyph}
            </span>
            <span className="max-w-[52px] truncate text-[10px] leading-tight">
              {petal.label}
            </span>
            {hasBadge ? (
              <span
                className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--color-accent)]"
                aria-label="Unread items inside"
              />
            ) : null}
          </button>
        </PetalBubble>
      </span>
      {fanOpen
        ? petal.members.map((member, i) => {
            const memberAngle = memberAngles[i] ?? angle;
            const rad = (memberAngle * Math.PI) / 180;
            const mx = Math.round(MEMBER_RING * Math.cos(rad) * 10) / 10;
            const my = Math.round(-MEMBER_RING * Math.sin(rad) * 10) / 10;
            return (
              <span key={member.id} data-coral-item role="none">
                <AppPetal
                  app={member}
                  x={mx}
                  y={my}
                  delayMs={i * STAGGER_MS}
                  variant="member"
                  active={isAppActive(pathname, member.route)}
                  badgeCount={
                    member.badgeSlot ? badgeCounts[member.badgeSlot] : undefined
                  }
                  onClose={onNavigate}
                />
              </span>
            );
          })
        : null}
    </>
  );
}
