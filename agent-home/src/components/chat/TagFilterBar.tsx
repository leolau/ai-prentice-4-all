"use client";

import type { SessionTag } from "@/types";

const TAG_BG: Record<string, string> = {
  blue: "var(--color-accent)",
  red: "#ef4444",
  green: "#22c55e",
  amber: "#f59e0b",
  purple: "#a855f7",
  gray: "var(--color-muted)",
};

/**
 * Filter bar above the session list. Tags are toggle chips — tap once to
 * include (highlighted), tap again to exclude (struck-through), tap a third
 * time to clear. The match-mode selector switches between AND (all) and OR (any).
 */
export function TagFilterBar({
  tags,
  includeTags,
  excludeTags,
  matchMode,
  onToggle,
  onMatchModeChange,
}: {
  tags: SessionTag[];
  includeTags: string[];
  excludeTags: string[];
  matchMode: "any" | "all";
  onToggle: (tagName: string) => void;
  onMatchModeChange: (mode: "any" | "all") => void;
}) {
  if (tags.length === 0) return null;

  const active = includeTags.length > 0 || excludeTags.length > 0;

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--color-border)] px-3 py-1.5">
      {active && (
        <button
          type="button"
          onClick={() => onMatchModeChange(matchMode === "any" ? "all" : "any")}
          className="shrink-0 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-[0.625rem] text-[var(--color-muted)]"
        >
          {matchMode === "any" ? "OR" : "AND"}
        </button>
      )}
      {tags.map((tag) => {
        const isIncluded = includeTags.includes(tag.name);
        const isExcluded = excludeTags.includes(tag.name);
        const color = TAG_BG[tag.color] ?? TAG_BG.blue;
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag.name)}
            className="shrink-0 rounded-full px-2 py-0.5 text-xs transition-colors"
            style={{
              background: isIncluded
                ? color
                : isExcluded
                  ? "repeating-linear-gradient(45deg, transparent, transparent 3px, var(--color-surface-2) 3px, var(--color-surface-2) 6px)"
                  : "transparent",
              color: isIncluded ? "#fff" : isExcluded ? color : color,
              border: `1px solid ${isIncluded || isExcluded ? color : "var(--color-border)"}`,
              textDecoration: isExcluded ? "line-through" : "none",
            }}
          >
            {tag.name}
          </button>
        );
      })}
    </div>
  );
}
