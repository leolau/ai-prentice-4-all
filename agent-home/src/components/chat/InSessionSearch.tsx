"use client";

import { useState, useMemo, useEffect } from "react";

import type { ChatMessage } from "@/types";
import { SearchNav } from "./SearchNav";

/**
 * In-session keyword search with next/previous navigation.
 * Highlights matching text in messages and scrolls to the current match.
 * Runs entirely client-side against the loaded message list.
 */
export function InSessionSearch({
  messages,
  onClose,
  highlightRef,
}: {
  messages: ChatMessage[];
  onClose: () => void;
  highlightRef: React.RefObject<((msgIndex: number, term: string) => void) | null>;
}) {
  const [query, setQuery] = useState("");
  const [currentIndex, setCurrentIndex] = useState(0);

  // Compute match indices — messages whose content includes the query (case-insensitive).
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return messages
      .map((m, i) => {
        const content = (m.content ?? "").toLowerCase();
        return content.includes(q) ? i : -1;
      })
      .filter((i) => i >= 0);
  }, [query, messages]);

  const currentMsgIndex = matches[currentIndex] ?? -1;

  // Scroll to the current match and tell the message list to highlight.
  useEffect(() => {
    if (currentMsgIndex < 0) return;
    const el = document.querySelector(
      `[data-msg-index="${currentMsgIndex}"]`,
    );
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    highlightRef.current?.(currentMsgIndex, query.trim());
  }, [currentMsgIndex, query, highlightRef]);

  const jump = (dir: 1 | -1) => {
    if (matches.length === 0) return;
    const next = (currentIndex + dir + matches.length) % matches.length;
    setCurrentIndex(next);
  };

  return (
    <div className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={query}
          autoFocus
          onChange={(e) => {
            setQuery(e.target.value);
            setCurrentIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              jump(e.shiftKey ? -1 : 1);
            } else if (e.key === "Escape") {
              onClose();
            }
          }}
          placeholder="Search in conversation…"
          className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-fg)]"
        />
        <SearchNav
          current={currentIndex}
          total={matches.length}
          onPrev={() => jump(-1)}
          onNext={() => jump(1)}
          onClose={onClose}
        />
      </div>
    </div>
  );
}
