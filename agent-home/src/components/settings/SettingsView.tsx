"use client";

import { useEffect, useState } from "react";

import type { SessionTag } from "@/types";
import {
  applyTheme,
  DEFAULT_THEME,
  isThemeId,
  THEME_STORAGE_KEY,
  THEMES,
  type ThemeId,
} from "@/lib/theme";
import { usePersistentState } from "@/lib/use-persistent-state";

/** Tag colours — same set used in SessionModal. */
const TAG_COLORS = [
  { id: "blue", label: "Blue" },
  { id: "red", label: "Red" },
  { id: "green", label: "Green" },
  { id: "amber", label: "Amber" },
  { id: "purple", label: "Purple" },
  { id: "gray", label: "Gray" },
];

const TAG_DOT: Record<string, string> = {
  blue: "var(--color-accent)",
  red: "#ef4444",
  green: "#22c55e",
  amber: "#f59e0b",
  purple: "#a855f7",
  gray: "var(--color-muted)",
};

/**
 * The Settings page body. Currently provides:
 * - A UI theme selector (applies immediately, persists in localStorage).
 * - A Tags management section (create / list / delete tags).
 */
export function SettingsView() {
  const [theme, setTheme] = usePersistentState<ThemeId>(
    THEME_STORAGE_KEY,
    DEFAULT_THEME,
    (raw) => (isThemeId(raw) ? raw : DEFAULT_THEME),
    (value) => value,
  );

  function choose(next: ThemeId) {
    setTheme(next);
    applyTheme(next);
  }

  return (
    <div data-component="SettingsView" className="space-y-6">
      <section>
        <h2 className="text-sm font-semibold">Colour theme</h2>
        <p className="mb-3 text-xs text-[var(--color-muted)]">
          Choose how the interface looks. Your choice is saved on this device.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {THEMES.map((opt) => {
            const active = opt.id === theme;
            return (
              <button
                key={opt.id}
                type="button"
                aria-pressed={active}
                onClick={() => choose(opt.id)}
                className={`flex flex-col items-start gap-2 rounded-2xl border p-3 text-left ${
                  active
                    ? "border-[var(--color-accent)] bg-[var(--color-surface-2)]"
                    : "border-[var(--color-border)] bg-[var(--color-surface)]"
                }`}
              >
                <span
                  aria-hidden
                  className="flex w-full gap-1.5"
                  data-theme={opt.id}
                >
                  <span className="h-6 flex-1 rounded-md bg-[var(--color-bg)]" />
                  <span className="h-6 flex-1 rounded-md bg-[var(--color-surface-2)]" />
                  <span className="h-6 flex-1 rounded-md bg-[var(--color-accent)]" />
                </span>
                <span className="flex items-center gap-2 text-sm font-medium">
                  {opt.label}
                  {active ? (
                    <span className="text-xs text-[var(--color-accent)]">
                      ✓ Active
                    </span>
                  ) : null}
                </span>
                <span className="text-xs text-[var(--color-muted)]">
                  {opt.description}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <TagsSection />
    </div>
  );
}

/* ── Tags management ─────────────────────────────────────────────── */

function TagsSection() {
  const [tags, setTags] = useState<SessionTag[]>([]);
  const [name, setName] = useState("");
  const [color, setColor] = useState("blue");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void loadTags();
  }, []);

  async function loadTags() {
    try {
      const res = await fetch("/api/chat/sessions/tags", { cache: "no-store" });
      if (res.ok) {
        const body = (await res.json()) as { tags?: SessionTag[] };
        if (body.tags) setTags(body.tags);
      }
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }

  async function createTag() {
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await fetch("/api/chat/sessions/tags", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: trimmed, color }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create tag.");
      }
      const body = (await res.json()) as { tag?: SessionTag };
      if (body.tag) {
        setTags((prev) =>
          prev.some((t) => t.id === body.tag!.id)
            ? prev
            : [...prev, body.tag!],
        );
      }
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create tag.");
    } finally {
      setCreating(false);
    }
  }

  async function deleteTag(tagId: string, tagName: string) {
    if (!confirm(`Delete tag "${tagName}"? This removes it from all sessions.`))
      return;
    setError(null);
    try {
      const res = await fetch(`/api/chat/sessions/tags/${encodeURIComponent(tagId)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Failed to delete tag.");
      setTags((prev) => prev.filter((t) => t.id !== tagId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete tag.");
    }
  }

  return (
    <section data-section="tags">
      <h2 className="text-sm font-semibold">Tags</h2>
      <p className="mb-3 text-xs text-[var(--color-muted)]">
        Define tags here, then associate them with conversations from the chat.
      </p>

      {/* Create form */}
      <div className="mb-3 flex flex-wrap gap-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void createTag();
            }
          }}
          placeholder="Tag name…"
          maxLength={50}
          className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-fg)]"
        />
        <select
          value={color}
          onChange={(e) => setColor(e.target.value)}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-2 text-sm text-[var(--color-fg)]"
        >
          {TAG_COLORS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void createTag()}
          disabled={creating || !name.trim()}
          className="shrink-0 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-60"
        >
          {creating ? "Creating…" : "Create"}
        </button>
      </div>

      {error && (
        <p className="mb-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      )}

      {/* Tag list */}
      {loading ? (
        <p className="text-xs text-[var(--color-muted)]">Loading tags…</p>
      ) : tags.length === 0 ? (
        <p className="text-xs text-[var(--color-muted)]">
          No tags defined yet. Create one above.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {tags.map((tag) => (
            <span
              key={tag.id}
              className="flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-xs"
            >
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ background: TAG_DOT[tag.color] ?? TAG_DOT.blue }}
              />
              <span className="text-[var(--color-fg)]">{tag.name}</span>
              {tag.session_count !== undefined && (
                <span className="text-[var(--color-muted)]">
                  ({String(tag.session_count)})
                </span>
              )}
              <button
                type="button"
                onClick={() => void deleteTag(tag.id, tag.name)}
                className="ml-0.5 text-xs opacity-60 hover:opacity-100"
                aria-label={`Delete tag ${tag.name}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
