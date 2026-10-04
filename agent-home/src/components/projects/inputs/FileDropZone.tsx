"use client";

import { useRef, useState } from "react";

/**
 * A drop target plus a "Choose files" button (multiple). It only hands the
 * picked files up — the caller's upload queue decides what to send, and
 * drops the same file dropped twice.
 */
export function FileDropZone({
  onFiles,
  disabled = false,
  chooseLabel = "Choose files",
  hint = "Drop files here",
  compact = false,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  chooseLabel?: string;
  hint?: string;
  compact?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  return (
    <div
      data-component="FileDropZone"
      data-over={over ? "true" : undefined}
      onDragOver={(e) => {
        if (disabled) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (disabled) return;
        const files = Array.from(e.dataTransfer?.files ?? []);
        if (files.length > 0) onFiles(files);
      }}
      className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-3 text-center text-sm ${
        compact ? "py-3" : "py-5"
      } ${
        over
          ? "border-[var(--color-accent)] bg-[var(--color-surface-2)]"
          : "border-[var(--color-border)]"
      } ${disabled ? "opacity-50" : ""}`}
    >
      <span className="text-[var(--color-muted)]">{hint}</span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium disabled:opacity-40"
      >
        {chooseLabel}
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        data-testid="file-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length > 0) onFiles(files);
        }}
      />
    </div>
  );
}
