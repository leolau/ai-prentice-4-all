/**
 * The app's current UI context for app-mcp: which page is on screen and which
 * element the user last touched. Maintained by the app-mcp bridge
 * (`lib/app-mcp/bridge.ts`) and read by the chat seam (`lib/chat/stream.ts`)
 * so every turn the user sends carries a one-line awareness context toward
 * the agent. Pure module state — no window access — so it is SSR-safe and
 * trivially testable.
 */

export interface UiElementRef {
  /** ARIA-ish role of the element ("button", "link", "textbox", …). */
  role: string;
  /** Its accessible name (aria-label, label, text, placeholder…). */
  name: string;
}

export interface UiContext {
  /** Router path currently on screen, e.g. "/todos". */
  path: string;
  /** The last element the user focused or tapped, if any. */
  element: UiElementRef | null;
}

let current: UiContext | null = null;

export function setUiContext(next: UiContext): void {
  current = next;
}

export function getUiContext(): UiContext | null {
  return current;
}

export function getLastActiveElement(): UiElementRef | null {
  return current?.element ?? null;
}

const NAME_LIMIT = 80;

function clip(name: string): string {
  const flat = name.replace(/\s+/g, " ").trim();
  return flat.length > NAME_LIMIT ? `${flat.slice(0, NAME_LIMIT - 1)}…` : flat;
}

/**
 * The one-line context prepended to outbound chat turns, e.g.
 * `[app context: page /todos · last active: button "Filter"]`. Returns null
 * when there is nothing to say (server render, before the bridge starts).
 */
export function formatUiContext(ctx: UiContext | null): string | null {
  if (!ctx || !ctx.path) return null;
  const element = ctx.element
    ? `${ctx.element.role} "${clip(ctx.element.name)}"`
    : "none";
  return `[app context: page ${ctx.path} · last active: ${element}]`;
}
