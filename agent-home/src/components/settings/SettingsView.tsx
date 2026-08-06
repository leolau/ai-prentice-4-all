"use client";

import {
  applyTheme,
  DEFAULT_THEME,
  isThemeId,
  THEME_STORAGE_KEY,
  THEMES,
  type ThemeId,
} from "@/lib/theme";
import { usePersistentState } from "@/lib/use-persistent-state";

/**
 * The Settings page body. Currently a UI theme selector: picking a theme
 * applies it to the document immediately (so the change is visible at once) and
 * persists it in `localStorage` for the next visit.
 */
export function SettingsView() {
  const [theme, setTheme] = usePersistentState<ThemeId>(
    THEME_STORAGE_KEY,
    DEFAULT_THEME,
    (raw) => (isThemeId(raw) ? raw : DEFAULT_THEME),
    (value) => value,
  );

  function choose(next: ThemeId) {
    // applyTheme writes the same key + sets <html data-theme> for an immediate
    // repaint; setTheme keeps this view's active-card highlight in sync.
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
    </div>
  );
}
