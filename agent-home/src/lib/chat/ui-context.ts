/**
 * app-mcp awareness seam for chat turns.
 *
 * The browser bridge reports which page the user is on and which element
 * they last touched (`uiContext` on the send body). Both chat routes prepend
 * it as ONE line ahead of the message, so the agent can resolve references
 * like "this page" or "the button I'm on" without extra round-trips. The
 * value is client-supplied, so it is type-checked, flattened to a single
 * line, and length-capped before it enters the prompt.
 */
export function withUiContext(message: string, uiContext: unknown): string {
  if (typeof uiContext !== "string") return message;
  const line = uiContext.replace(/[\r\n\t\0]+/g, " ").trim().slice(0, 200);
  if (!line) return message;
  return message ? `${line}\n${message}` : line;
}
