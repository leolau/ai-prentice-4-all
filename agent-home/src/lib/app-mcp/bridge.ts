/**
 * The app-mcp browser bridge.
 *
 * One WebSocket to the app-mcp service, ticket-authenticated (the ticket is
 * minted by the BFF under the signed session cookie, so the browser's
 * auth story is unchanged). The bridge has two jobs:
 *
 * 1. **Report** — keep the service aware of the current page and the last
 *    element the user touched (focusin + taps), debounced.
 * 2. **Obey** — execute commands the service relays from the agent's MCP
 *    tools (snapshot / click / type / navigate / …) and return the outcome.
 *
 * Everything is best-effort: if the service is down or unconfigured the app
 * must not notice — reconnect quietly with backoff, never surface errors.
 */
import { executeCommand, type AppMcpCommand } from "@/lib/app-mcp/actions";
import { elementRef, inShellChrome } from "@/lib/app-mcp/snapshot";
import { getLastActiveElement, setUiContext } from "@/lib/app-mcp/state";

interface ServiceMessage {
  type?: string;
  id?: string;
  command?: AppMcpCommand;
}

let started = false;
let ws: WebSocket | null = null;
let reconnectDelay = 1000;
let stateTimer: ReturnType<typeof setTimeout> | null = null;

async function fetchTicket(): Promise<string | null> {
  try {
    const res = await fetch("/api/app-mcp/ticket", { method: "POST" });
    if (!res.ok) return null;
    const data = (await res.json()) as { ticket?: unknown };
    return typeof data.ticket === "string" ? data.ticket : null;
  } catch {
    return null;
  }
}

/** Push the current page + last-active element to the service (if connected). */
export function reportState(): void {
  setUiContext({ path: window.location.pathname, element: getLastActiveElement() });
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  try {
    ws.send(
      JSON.stringify({
        type: "state",
        path: window.location.pathname,
        element: getLastActiveElement(),
      }),
    );
  } catch {
    // A mid-close send is harmless; reconnect handles recovery.
  }
}

/** Debounced variant for high-frequency focus/pointer events. */
function scheduleStateReport(): void {
  if (stateTimer) clearTimeout(stateTimer);
  stateTimer = setTimeout(() => {
    stateTimer = null;
    reportState();
  }, 200);
}

function noteActiveElement(target: EventTarget | null): void {
  if (!(target instanceof Element)) return;
  // Interactions with the shell chrome (lead-chat composer, Coral launcher)
  // must not shadow the page the user is actually looking at.
  if (inShellChrome(target)) return;
  const ref = elementRef(target);
  if (!ref.name && ref.role === "element") return;
  setUiContext({ path: window.location.pathname, element: ref });
  scheduleStateReport();
}

function trackActiveElement(): void {
  document.addEventListener("focusin", (e) => noteActiveElement(e.target));
  document.addEventListener(
    "pointerdown",
    (e) => noteActiveElement(e.target),
    true,
  );
}

function scheduleReconnect(): void {
  ws = null;
  setTimeout(() => void connect(), reconnectDelay);
  reconnectDelay = Math.min(30_000, reconnectDelay * 2);
}

async function connect(): Promise<void> {
  if (started && ws) return;
  const ticket = await fetchTicket();
  if (!ticket) {
    scheduleReconnect();
    return;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  let sock: WebSocket;
  try {
    sock = new WebSocket(
      `${proto}://${window.location.host}/app-mcp/ws?ticket=${encodeURIComponent(ticket)}`,
    );
  } catch {
    scheduleReconnect();
    return;
  }
  ws = sock;
  sock.onopen = () => {
    reconnectDelay = 1000;
    reportState();
  };
  sock.onmessage = (ev) => {
    let msg: ServiceMessage;
    try {
      msg = JSON.parse(String(ev.data)) as ServiceMessage;
    } catch {
      return;
    }
    if (msg.type !== "cmd" || !msg.command) return;
    const result = executeCommand(msg.command);
    try {
      sock.send(JSON.stringify({ type: "result", id: msg.id, ...result }));
    } catch {
      // Connection died mid-action; the service times out and reports it.
    }
    if (msg.command.type === "navigate" || msg.command.type === "click") {
      scheduleStateReport();
    }
  };
  sock.onclose = () => scheduleReconnect();
  sock.onerror = () => sock.close();
}

/** Idempotent start — called by <AppMcpBridge/> on mount. */
export function startBridge(): void {
  if (started) return;
  started = true;
  trackActiveElement();
  reportState();
  void connect();
}

/** Test-only: reset module state between cases. */
export function __resetBridgeForTest(): void {
  started = false;
  ws = null;
  reconnectDelay = 1000;
  if (stateTimer) clearTimeout(stateTimer);
  stateTimer = null;
}
