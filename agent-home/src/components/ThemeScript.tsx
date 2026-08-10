import { DEFAULT_THEME, THEME_STORAGE_KEY } from "@/lib/theme";

/**
 * A tiny inline script that sets `<html data-theme>` from localStorage before
 * the first paint, so a non-default theme never flashes the dark palette on
 * load. Runs before hydration; the runtime selector (Settings) takes over
 * after mount.
 */
export function ThemeScript() {
  const code = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
    THEME_STORAGE_KEY,
  )});if(t!=="dark"&&t!=="light"&&t!=="colourful"){t=${JSON.stringify(
    DEFAULT_THEME,
  )};}document.documentElement.dataset.theme=t;}catch(e){document.documentElement.dataset.theme=${JSON.stringify(
    DEFAULT_THEME,
  )};}})();`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
