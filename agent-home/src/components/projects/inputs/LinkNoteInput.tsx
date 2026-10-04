"use client";

import { useState } from "react";

import { ActionButton } from "@/components/projects/ActionButton";
import { parseLinkOrNote, type LinkEntry } from "@/components/projects/inputs/inputKinds";

/**
 * One box for a URL or a short note. `onAdd` may be async and resolve
 * `false` to keep the text (a refused write); otherwise the box clears.
 */
export function LinkNoteInput({
  onAdd,
  busy = false,
  disabled = false,
}: {
  onAdd: (entry: LinkEntry) => boolean | void | Promise<boolean | void>;
  busy?: boolean;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  const entry = parseLinkOrNote(text);

  const submit = async () => {
    if (!entry || busy || disabled) return;
    const ok = await onAdd(entry);
    if (ok !== false) setText("");
  };

  return (
    <div data-component="LinkNoteInput" className="flex flex-col gap-1">
      <div className="flex gap-2">
        <input
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
          aria-label="Link or note"
          placeholder="https://drive.google.com/… or paste a note"
          className="min-w-0 flex-1 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
        />
        <ActionButton
          busy={busy}
          pendingLabel="Adding…"
          disabled={!entry || disabled}
          onClick={() => void submit()}
        >
          Add
        </ActionButton>
      </div>
      {entry ? (
        <span className="text-xs text-[var(--color-muted)]">
          {entry.kind === "url" ? "Adds a link" : "Adds a note the agent will read"}
        </span>
      ) : null}
    </div>
  );
}
