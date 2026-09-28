"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { StatusSummary } from "@/types";

const POLL_MS = 30_000;

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v)}%`;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return `${n}`;
}

/**
 * The ambient status strip under the header: CPU, storage, 30-day token
 * burn, live background work, and scheduled jobs. Polls while the tab is
 * visible and re-reads on focus; a failed read keeps the last good values
 * (a strip that blanks on every hiccup is worse than a stale one).
 */
export function StatusStrip() {
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const res = await fetch("/api/status/summary", { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as StatusSummary;
        if (!cancelled) {
          setSummary(data);
          setStale(false);
        }
      } catch {
        if (!cancelled) setStale(true);
      }
    }
    void tick();
    const interval = setInterval(() => {
      if (document.visibilityState === "visible") void tick();
    }, POLL_MS);
    const wake = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      cancelled = true;
      clearInterval(interval);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, []);

  if (!summary) return null;

  const tokens = summary.tokens_month
    ? fmtTokens(summary.tokens_month.input + summary.tokens_month.output)
    : null;
  const cost = summary.tokens_month?.cost_usd;
  const tasks =
    (summary.active_conversations ?? 0) + (summary.running_cards ?? 0);

  const chip =
    "inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-[var(--color-surface-2)] px-2 py-0.5";

  return (
    <div
      data-component="StatusStrip"
      data-stale={stale || undefined}
      className={`overflow-x-auto border-b border-[var(--color-border)] px-4 py-1.5 text-[11px] text-[var(--color-muted)] lg:px-8 ${
        stale ? "opacity-60" : ""
      }`}
    >
      <div className="mx-auto flex w-full max-w-2xl items-center gap-2 lg:max-w-5xl">
        <Link href="/capacity" className={chip} title="CPU busy — tap for the capacity page">
          <span className="font-medium text-[var(--color-fg)]">CPU</span>
          {fmtPct(summary.cpu_pct)}
        </Link>
        <Link href="/capacity" className={chip} title="Storage used on the hermes volume">
          <span className="font-medium text-[var(--color-fg)]">Disk</span>
          {fmtPct(summary.disk_pct)}
        </Link>
        {tokens !== null ? (
          <Link
            href="/models"
            className={chip}
            title="Tokens used in the last 30 days (input + output)"
          >
            <span className="font-medium text-[var(--color-fg)]">Tokens</span>
            {tokens}/mo
            {cost ? ` · $${cost.toFixed(2)}` : ""}
          </Link>
        ) : null}
        <span className={chip} title="Active agent conversations + running kanban cards">
          <span className="font-medium text-[var(--color-fg)]">Tasks</span>
          {tasks}
        </span>
        <span className={chip} title="Scheduled cron jobs (enabled of total)">
          <span className="font-medium text-[var(--color-fg)]">Cron</span>
          {summary.cron_enabled}/{summary.cron_total}
        </span>
        {stale ? <span className="ml-auto">updating…</span> : null}
      </div>
    </div>
  );
}
