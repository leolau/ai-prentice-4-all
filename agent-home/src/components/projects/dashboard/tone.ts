import type { DashTone } from "@/components/projects/dashboard/nextAction";

export const TONE_CARD: Record<DashTone, string> = {
  danger: "border-red-500/50 bg-red-500/10",
  warning: "border-amber-500/50 bg-amber-500/10",
  accent: "border-[var(--color-accent)]/50 bg-[var(--color-surface)]",
  info: "border-sky-500/40 bg-sky-500/5",
  calm: "border-[var(--color-border)] bg-[var(--color-surface)]",
};

export const TONE_DOT: Record<DashTone, string> = {
  danger: "bg-red-400",
  warning: "bg-amber-400",
  accent: "bg-[var(--color-accent)]",
  info: "bg-sky-400",
  calm: "bg-emerald-400",
};
