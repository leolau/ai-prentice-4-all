/**
 * Soft-navigation seam for the app-mcp `navigate` command.
 *
 * `window.location.assign` is a full page load — it tears down every open
 * WebSocket (the app-mcp bridge itself AND the folder bridge on
 * /app-mcp/folders/ws) and wipes all module state, so an agent that
 * navigates to /files to show an imported file severs the very bridge
 * session it is using. AppMcpBridge registers the Next.js router here on
 * mount; `navigate` prefers it and only falls back to location.assign when
 * no router is registered yet (before the shell mounts, or in tests).
 */

type NavigateFn = (path: string) => void;

let softNavigator: NavigateFn | null = null;

/** Called by the app shell once the Next.js router exists. */
export function registerSoftNavigate(fn: NavigateFn | null): void {
  softNavigator = fn;
}

/** In-place navigation when a router is registered. Returns false when the
 * caller should fall back to a full `window.location.assign`. */
export function softNavigate(path: string): boolean {
  if (!softNavigator) return false;
  softNavigator(path);
  return true;
}
