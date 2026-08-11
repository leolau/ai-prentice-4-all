"use client";

import type { ProfileSummary } from "@/types";

/** The profile the picker means when nothing is selected. */
export const DEFAULT_PROFILE = "default";

/**
 * Chooses which profile answers this chat (FG-28). A profile is an independent
 * `HERMES_HOME` — its own SOUL, goal, skills, memory, credentials and
 * conversations — so this switches *which brain* replies, and with it the
 * conversation list, not merely a filter over one list.
 *
 * Renders nothing when the box serves a single profile: the overwhelmingly
 * common deployment should not carry a control with one option.
 */
export function ProfilePicker({
  profiles,
  selected,
  onSelect,
  disabled = false,
}: {
  profiles: ProfileSummary[];
  selected: string;
  onSelect: (profile: string) => void;
  /** True while a turn is in flight — switching mid-turn is refused. */
  disabled?: boolean;
}) {
  if (profiles.length < 2) return null;

  const active = profiles.find((p) => p.name === selected);

  return (
    <div
      data-component="ProfilePicker"
      className="mb-3 flex items-center gap-2 text-xs"
    >
      <label
        htmlFor="chat-profile"
        className="shrink-0 text-[var(--color-muted)]"
      >
        Profile
      </label>
      <select
        id="chat-profile"
        value={selected}
        disabled={disabled}
        onChange={(e) => onSelect(e.target.value)}
        className="min-w-0 flex-1 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 text-xs text-[var(--color-fg)] disabled:opacity-50"
      >
        {profiles.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
            {p.is_default ? " (default)" : ""}
          </option>
        ))}
      </select>
      {active?.description ? (
        <span className="hidden truncate text-[var(--color-muted)] sm:block">
          {active.description}
        </span>
      ) : null}
    </div>
  );
}
