/**
 * Recovery for a stale-bundle "chunk load" failure (client-side only).
 *
 * A deploy replaces Next.js's content-hashed JS files; a tab (or an
 * installed PWA left open, or backgrounded, across the deploy) still
 * holds references to the OLD hashes, which no longer exist on the
 * server. The next dynamic import or route-chunk fetch then throws —
 * on Chrome as `ChunkLoadError` / "Loading chunk N failed", on Safari/
 * WebKit as "Importing a module script failed" (the exact production
 * incident this fixes: an installed iOS PWA left open across three
 * deploys threw an unrecoverable "Application error"). A fresh load
 * fetches the current bundle and works fine — the fix is simply to
 * reload once, automatically, rather than surface an error.
 */

const CHUNK_ERROR_PATTERNS = [
  /ChunkLoadError/i,
  /Loading chunk [\w-]+ failed/i,
  /Loading CSS chunk/i,
  /Failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /Importing a module script failed/i,
];

/** Best-effort test: does `error` look like a stale-bundle chunk-load
 * failure rather than a real application bug? */
export function isChunkLoadError(error: unknown): boolean {
  if (!error) return false;
  if (error instanceof Error) {
    if (error.name === "ChunkLoadError") return true;
    return CHUNK_ERROR_PATTERNS.some((re) => re.test(error.message));
  }
  return CHUNK_ERROR_PATTERNS.some((re) => re.test(String(error)));
}

const RELOAD_FLAG = "agent-home:chunk-reload-attempted";

/** A `window`-shaped seam so tests can supply a fake one without a real
 * DOM — the only two things this module ever touches. */
export interface ReloadWindow {
  sessionStorage: Pick<Storage, "getItem" | "setItem" | "removeItem">;
  location: Pick<Location, "reload">;
}

function currentWindow(): ReloadWindow | null {
  return typeof window === "undefined" ? null : (window as unknown as ReloadWindow);
}

/**
 * If `error` looks like a stale-bundle failure, reload the page exactly
 * once per "session" (a `sessionStorage` flag, not a real one — cleared
 * by `markAppHealthy` once something loads successfully, so a *later*
 * deploy's stale chunk still gets its own single retry rather than being
 * silently blocked by an old flag). Returns whether it triggered a
 * reload, so a caller can skip rendering a fallback for the instant
 * before the reload lands.
 */
export function reloadOnceForChunkError(
  error: unknown,
  win: ReloadWindow | null = currentWindow(),
): boolean {
  if (!win || !isChunkLoadError(error)) return false;
  try {
    if (win.sessionStorage.getItem(RELOAD_FLAG) === "1") return false;
    win.sessionStorage.setItem(RELOAD_FLAG, "1");
  } catch {
    // sessionStorage can throw (private browsing, storage quota) — still
    // reload; worst case is one extra reload later, not a stuck error page.
  }
  win.location.reload();
  return true;
}

/** Call once the app has actually rendered something — proof the current
 * bundle is good, so a future stale-chunk failure isn't pre-blocked by
 * today's reload flag. */
export function markAppHealthy(win: ReloadWindow | null = currentWindow()): void {
  try {
    win?.sessionStorage.removeItem(RELOAD_FLAG);
  } catch {
    // ignore — the flag is only ever a courtesy, not a correctness gate
  }
}
