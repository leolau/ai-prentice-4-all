/**
 * UI theme model (FG-20). A theme is just a `data-theme` value on <html> that
 * overrides the design tokens defined in `globals.css`. The choice persists in
 * `localStorage` and is applied before paint by the inline `ThemeScript` so
 * there is no flash of the wrong palette.
 */
export type ThemeId = "dark" | "light" | "colourful";

export const THEME_STORAGE_KEY = "agent-home:theme";
export const DEFAULT_THEME: ThemeId = "dark";

export interface ThemeOption {
  id: ThemeId;
  label: string;
  description: string;
}

export const THEMES: ThemeOption[] = [
  { id: "dark", label: "Dark", description: "The original low-light palette." },
  { id: "light", label: "Light", description: "A bright, white background." },
  {
    id: "colourful",
    label: "Colourful",
    description: "A vivid purple-and-pink palette.",
  },
];

export function isThemeId(value: unknown): value is ThemeId {
  return value === "dark" || value === "light" || value === "colourful";
}

/** Read the persisted theme, falling back to the default. */
export function readStoredTheme(): ThemeId {
  if (typeof window === "undefined") return DEFAULT_THEME;
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeId(raw) ? raw : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

/** Apply a theme to the document and persist it. */
export function applyTheme(theme: ThemeId): void {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = theme;
  }
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // A blocked localStorage still themes the current page; just no persistence.
  }
}
