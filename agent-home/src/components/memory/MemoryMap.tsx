"use client";

import { useEffect, useState, type ReactNode } from "react";

import { describeSource, sourceLink } from "@/components/memory/citation";
import type {
  MemoryProjection,
  MemoryProjectionPoint,
  MemoryQueryPlacement,
} from "@/types";

/**
 * FG-23 A3 — the 2-D memory map on the phone. No charting dependency
 * (`@observablehq/plot` is for the desktop dashboard): a scatter plot is
 * `<circle>` elements plus a linear scale. Inline SVG keeps the bundle small
 * for a PWA on mobile data.
 *
 * Fitting is whole-corpus SVD owned by `hermes-memory-projection.timer`
 * (nightly 03:00). There is no endpoint to trigger it and this component
 * adds none.
 */

/** Fixed 12-colour palette for topic hashing (never Math.random). */
const PALETTE = [
  "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
  "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff",
];

/** Stable string hash → palette index. Same topic always maps to same colour. */
export function topicColor(topic: string | null): string {
  const s = topic || "(none)";
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/**
 * Compute the viewBox from the point extent, padded 5%. Exported as a pure
 * function so the test can assert scaling without a DOM.
 *
 * A single point (zero extent) is centred rather than dividing by zero.
 */
export function computeViewBox(
  points: { x: number; y: number }[],
): { minX: number; minY: number; w: number; h: number } {
  if (points.length === 0) {
    return { minX: 0, minY: 0, w: 100, h: 100 };
  }
  if (points.length === 1) {
    return { minX: points[0].x - 50, minY: points[0].y - 50, w: 100, h: 100 };
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of points) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  const w = maxX - minX || 1;
  const h = maxY - minY || 1;
  const padX = w * 0.05;
  const padY = h * 0.05;
  return {
    minX: minX - padX,
    minY: minY - padY,
    w: w + padX * 2,
    h: h + padY * 2,
  };
}

/**
 * The SVG user-space the points are drawn in. `toViewBox` normalises data
 * coordinates into it, so the `viewBox` attribute is this square and not the
 * data extent — PCA coordinates live in roughly [-0.5, 0.5], and a viewBox in
 * those units would leave every dot outside the frame.
 */
const VIEW_SIDE = 100;

/** Map a data coordinate to viewBox-space [0, 100]. */
function toViewBox(
  val: number,
  min: number,
  range: number,
): number {
  return ((val - min) / range) * VIEW_SIDE;
}

/**
 * Draws the corpus and, when a query has been placed, dims everything but its
 * nearest neighbours. The nearest *list* belongs to `MemoryView`, which owns
 * the query box — rendering it here too showed it twice.
 */
export function MemoryMap({
  projection,
  queryResult,
}: {
  projection: MemoryProjection;
  queryResult: MemoryQueryPlacement | null;
}) {
  const [selected, setSelected] = useState<MemoryProjectionPoint | null>(null);
  const [legendOpen, setLegendOpen] = useState(false);

  // --- Empty / degenerate states (all of which occur) --------------------
  if (projection.algorithm == null || projection.points.length === 0) {
    return (
      <div
        data-component="MemoryMapEmpty"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-center text-sm text-[var(--color-muted)]"
      >
        No map yet — it&apos;s fitted nightly at 03:00.
      </div>
    );
  }

  const points = projection.points;
  // The typed query is included in the extent so a placement that lands
  // outside the corpus's spread is drawn rather than clipped off the frame.
  const placed =
    queryResult && queryResult.x != null && queryResult.y != null
      ? [{ x: queryResult.x, y: queryResult.y }]
      : [];
  const vb = computeViewBox([...points, ...placed]);

  // --- Staleness banners (two different sentences) -----------------------
  let stalenessBanner: string | null = null;
  if (projection.unprojected_count > 0) {
    stalenessBanner = `${projection.unprojected_count} new ${
      projection.unprojected_count === 1 ? "memory" : "memories"
    } ${
      projection.unprojected_count === 1 ? "is" : "are"
    }n't on the map yet`;
  } else if (projection.stale) {
    stalenessBanner =
      "This map was fitted with a different embedder — distances aren't meaningful.";
  }

  // --- Sampled banner ----------------------------------------------------
  const sampledBanner =
    projection.sampled && projection.total_points != null
      ? `Showing ${points.length.toLocaleString()} of ${projection.total_points.toLocaleString()}`
      : null;

  // --- Query placement marker (hollow ring) ------------------------------
  const queryX =
    queryResult && queryResult.x != null
      ? toViewBox(queryResult.x, vb.minX, vb.w)
      : null;
  const queryY =
    queryResult && queryResult.y != null
      ? toViewBox(queryResult.y, vb.minY, vb.h)
      : null;
  const nearestIds = queryResult
    ? new Set(queryResult.nearest.map((n) => n.id))
    : null;

  return (
    <div data-component="MemoryMap" className="space-y-2">
      {stalenessBanner && (
        <p className="text-xs text-[var(--color-muted)]">{stalenessBanner}</p>
      )}
      {sampledBanner && (
        <p className="text-xs text-[var(--color-muted)]">{sampledBanner}</p>
      )}
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-[var(--color-muted)]">
          A 2-D projection always distorts — positions are relative, not exact.
        </p>
        <button
          type="button"
          onClick={() => setLegendOpen(true)}
          className="shrink-0 rounded-lg border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-muted)]"
        >
          Legend
        </button>
      </div>

      {/* 85% of the column width: the plot is square, so trimming the width
          is the only way to lose 15% of its height and still keep the
          aspect ratio — which is what makes it fit a laptop viewport. */}
      <svg
        viewBox={`0 0 ${VIEW_SIDE} ${VIEW_SIDE}`}
        className="mx-auto w-[85%] touch-none rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)]"
        style={{ aspectRatio: "1" }}
        preserveAspectRatio="xMidYMid meet"
      >
        {points.map((p) => {
          const cx = toViewBox(p.x, vb.minX, vb.w);
          const cy = toViewBox(p.y, vb.minY, vb.h);
          const color = topicColor(p.topic);
          const dimmed = nearestIds && !nearestIds.has(p.id);
          const opacity = dimmed ? 0.15 : 0.8;
          return (
            <g key={p.id}>
              {p.elevated && (
                <circle
                  cx={cx}
                  cy={cy}
                  r={3}
                  fill="none"
                  stroke={color}
                  strokeWidth={0.5}
                  opacity={opacity}
                />
              )}
              {/* Shape carries the kind: a memory is a dot, a document chunk
                  a square. Colour is already spent on the topic. */}
              {p.kind === "chunk" ? (
                <rect
                  x={cx - 1.1}
                  y={cy - 1.1}
                  width={2.2}
                  height={2.2}
                  fill={color}
                  opacity={opacity}
                  pointerEvents="none"
                />
              ) : (
                <circle
                  cx={cx}
                  cy={cy}
                  r={1.2}
                  fill={color}
                  opacity={opacity}
                  pointerEvents="none"
                />
              )}
              {/* A 1.2-unit dot is a ~4 px target on a phone: the hit area is
                  a wider invisible circle over it. */}
              <circle
                cx={cx}
                cy={cy}
                r={3}
                fill="transparent"
                onClick={() => setSelected(p)}
                style={{ cursor: "pointer" }}
              />
            </g>
          );
        })}

        {/* Query placement: hollow labelled ring */}
        {queryX != null && queryY != null && (
          <g>
            <circle
              cx={queryX}
              cy={queryY}
              r={3}
              fill="none"
              stroke="currentColor"
              strokeWidth={0.8}
              className="text-[var(--color-accent)]"
            />
            <circle
              cx={queryX}
              cy={queryY}
              r={1}
              fill="none"
              stroke="currentColor"
              strokeWidth={0.4}
              className="text-[var(--color-accent)]"
            />
          </g>
        )}
      </svg>

      {legendOpen && <MemoryMapLegend onClose={() => setLegendOpen(false)} />}

      {selected && (
        <MemoryPointModal
          point={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

/** What a dot's shape, ring and colour mean. */
export function MemoryMapLegend({ onClose }: { onClose: () => void }) {
  useEscapeToClose(onClose);
  return (
    <ModalFrame
      component="MemoryMapLegend"
      title="How to read the map"
      onClose={onClose}
    >
      <dl className="space-y-3 text-sm">
        <LegendRow
          swatch={<circle cx={8} cy={8} r={5} fill="var(--color-accent)" />}
          term="Dot — a memory"
          desc="A fact the agent stored, usually written during a chat."
        />
        <LegendRow
          swatch={
            <rect x={3} y={3} width={10} height={10} fill="var(--color-accent)" />
          }
          term="Square — a document chunk"
          desc="A passage of a file you ingested, retrievable with citations."
        />
        <LegendRow
          swatch={
            <>
              <circle
                cx={8}
                cy={8}
                r={7}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth={1}
              />
              <circle cx={8} cy={8} r={3.5} fill="var(--color-accent)" />
            </>
          }
          term="Outer ring — someone else's"
          desc="Read through your elevated role; the owner is named in the popup."
        />
        <LegendRow
          swatch={
            <>
              <circle
                cx={8}
                cy={8}
                r={7}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth={1}
              />
              <circle
                cx={8}
                cy={8}
                r={2.5}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth={1}
              />
            </>
          }
          term="Hollow double ring — your query"
          desc="Where the text you placed lands; its neighbours stay lit and the rest dim."
        />
        <LegendRow
          swatch={
            <>
              <circle cx={5} cy={8} r={3.5} fill="#4363d8" />
              <circle cx={12} cy={8} r={3.5} fill="#f58231" />
            </>
          }
          term="Colour — the topic"
          desc="One stable colour per topic (untopiced memories share one). Colour says nothing about importance."
        />
      </dl>
      <p className="mt-3 text-xs text-[var(--color-muted)]">
        Distance is meaning: dots near each other were embedded as similar
        text. The projection flattens 1024 dimensions to two, so read
        neighbourhoods, not exact positions.
      </p>
    </ModalFrame>
  );
}

function LegendRow({
  swatch,
  term,
  desc,
}: {
  swatch: ReactNode;
  term: string;
  desc: string;
}) {
  return (
    <div data-component="LegendRow" className="flex gap-3">
      <svg viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent)]">
        {swatch}
      </svg>
      <div>
        <dt className="font-medium">{term}</dt>
        <dd className="text-xs text-[var(--color-muted)]">{desc}</dd>
      </div>
    </div>
  );
}

/**
 * The clicked dot's detail, as a modal over the map.
 *
 * It used to render underneath the plot, which on a tall square map meant
 * clicking a dot appeared to do nothing until you scrolled — the answer was
 * off-screen at the moment you asked for it.
 */
export function MemoryPointModal({
  point,
  onClose,
}: {
  point: MemoryProjectionPoint;
  onClose: () => void;
}) {
  useEscapeToClose(onClose);

  const source = describeSource(point);
  const link = sourceLink(point);

  return (
    <ModalFrame
      component="MemoryPointModal"
      title={point.kind === "chunk" ? "Document chunk" : "Memory"}
      onClose={onClose}
    >
      <p className="max-h-60 overflow-y-auto text-sm leading-relaxed">
        {point.label || "(no label)"}
      </p>

      {source && (
        <div className="mt-3 border-t border-[var(--color-border)] pt-2">
          <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Source
          </p>
          <p className="text-sm">{source.label}</p>
          {source.detail && (
            <p className="break-all text-xs text-[var(--color-muted)]">
              {source.detail}
            </p>
          )}
          {link && (
            <a
              href={link.href}
              target={link.external ? "_blank" : undefined}
              rel={link.external ? "noreferrer" : undefined}
              className="mt-2 inline-block text-sm text-[var(--color-accent)] underline"
            >
              {link.text}
            </a>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--color-muted)]">
        {point.topic && (
          <span className="rounded bg-[var(--color-surface)] px-2 py-0.5">
            {point.topic}
          </span>
        )}
        <span>owner: {point.owner_user_id}</span>
        {point.elevated && point.provenance && (
          <span className="text-[var(--color-accent)]">
            {point.provenance}
          </span>
        )}
      </div>
    </ModalFrame>
  );
}

/** Close on Escape, so a modal over the map is dismissable from the keyboard. */
function useEscapeToClose(onClose: () => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
}

/** Backdrop + dialog card + close button, shared by the map's two popups. */
function ModalFrame({
  component,
  title,
  onClose,
  children,
}: {
  component: string;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div
      data-component={component}
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85dvh] w-full max-w-sm overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)] p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-[var(--color-muted)]"
          >
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
