"use client";

import { FOCUS_MAX } from "@/components/projects/scope/clarify";
import { INPUT } from "@/components/projects/scope/ui";

/** The optional "what should the agent focus on" line sent with a question request. */
export function FocusInput({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-[var(--color-muted)]">
        {label} <span className="opacity-70">(optional)</span>
      </label>
      <input
        id={id}
        type="text"
        value={value}
        maxLength={FOCUS_MAX}
        disabled={disabled}
        placeholder="e.g. who signs off, or the deadline"
        className={INPUT}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
