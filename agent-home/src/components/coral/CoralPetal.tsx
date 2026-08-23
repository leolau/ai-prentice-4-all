"use client";

import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";

import { NavGlyph } from "@/components/NavGlyph";
import type { AppManifest } from "@/components/coral/coral-types";

/**
 * The shared round bubble every petal renders in. Positioned absolutely on
 * the bloom's 0×0 anchor point; the registry-computed offset arrives as
 * --coral-x/--coral-y so the entry animation (coral.css) can start from the
 * button without touching the final position.
 */
export function PetalBubble({
  x,
  y,
  delayMs,
  active,
  variant = "petal",
  children,
}: {
  x: number;
  y: number;
  delayMs: number;
  active?: boolean;
  variant?: "petal" | "member";
  children: ReactNode;
}) {
  const style = {
    "--coral-x": `${x}px`,
    "--coral-y": `${y}px`,
    "--coral-delay": `${delayMs}ms`,
    transform: "translate(var(--coral-x), var(--coral-y)) translate(-50%, -50%)",
  } as CSSProperties;
  return (
    <div
      className={`${variant === "member" ? "coral-member" : "coral-petal"} absolute left-0 top-0 h-[60px] w-[60px]`}
      style={style}
      data-active={active || undefined}
    >
      {children}
    </div>
  );
}

const bubbleFace =
  "flex h-full w-full flex-col items-center justify-center gap-0.5 rounded-full " +
  "border bg-[var(--color-surface)] shadow-lg transition-colors " +
  "hover:bg-[var(--color-surface-2)] focus-visible:outline-2 " +
  "focus-visible:outline-[var(--color-accent)]";

/** A destination petal: a real link, so deep-linking and the pending spinner work. */
export function AppPetal({
  app,
  x,
  y,
  delayMs,
  variant = "petal",
  active,
  badgeCount,
  onClose,
}: {
  app: AppManifest;
  x: number;
  y: number;
  delayMs: number;
  variant?: "petal" | "member";
  active: boolean;
  badgeCount?: number;
  onClose: () => void;
}) {
  return (
    <PetalBubble x={x} y={y} delayMs={delayMs} variant={variant} active={active}>
      <Link
        href={app.route}
        onClick={onClose}
        role="menuitem"
        aria-current={active ? "page" : undefined}
        title={app.hint}
        className={`relative ${bubbleFace} ${
          active
            ? "border-[var(--color-accent)] text-[var(--color-accent)]"
            : "border-[var(--color-border)] text-[var(--color-text)]"
        }`}
      >
        <span className="text-lg leading-none">
          <NavGlyph glyph={app.glyph} />
        </span>
        <span className="max-w-[52px] truncate text-[10px] leading-tight">
          {app.name}
        </span>
        {badgeCount ? (
          <span className="absolute -right-1 -top-1 rounded-full bg-[var(--color-accent)] px-1.5 text-[10px] leading-4 text-[var(--color-accent-fg)]">
            {badgeCount}
          </span>
        ) : null}
      </Link>
    </PetalBubble>
  );
}
