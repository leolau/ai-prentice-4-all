"use client";

import { useState } from "react";

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
      <p className="text-xs text-[var(--color-muted)]">
        A 2-D projection always distorts — positions are relative, not exact.
      </p>

      <svg
        viewBox={`0 0 ${VIEW_SIDE} ${VIEW_SIDE}`}
        className="w-full touch-none rounded-2xl border border-[var(--color-border)] bg-[var(--color-bg)]"
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
              <circle
                cx={cx}
                cy={cy}
                r={1.2}
                fill={color}
                opacity={opacity}
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

      {/* Bottom sheet for selected point */}
      {selected && (
        <div
          data-component="MemoryMapSheet"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
          onClick={() => setSelected(null)}
        >
          <p className="text-sm leading-relaxed">
            {selected.label || "(no label)"}
          </p>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-[var(--color-muted)]">
            {selected.topic && (
              <span className="rounded bg-[var(--color-bg)] px-2 py-0.5">
                {selected.topic}
              </span>
            )}
            <span>owner: {selected.owner_user_id}</span>
            {selected.elevated && selected.provenance && (
              <span className="text-[var(--color-accent)]">
                {selected.provenance}
              </span>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
