import type { ProjectChangeKind } from "@/types";

/** The sheet's kind chips, in display order. */
export const CHANGE_KINDS: readonly { kind: ProjectChangeKind; label: string; icon: string }[] = [
  { kind: "add", label: "Add requirement", icon: "＋" },
  { kind: "direction", label: "Change direction", icon: "↺" },
  { kind: "output", label: "Change an output", icon: "◎" },
  { kind: "attach", label: "Attach", icon: "📎" },
  { kind: "memory", label: "Memory", icon: "🧠" },
];

export function changeKindLabel(kind: ProjectChangeKind): string {
  return CHANGE_KINDS.find((k) => k.kind === kind)?.label ?? kind;
}
