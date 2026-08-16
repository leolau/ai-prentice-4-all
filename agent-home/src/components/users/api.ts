/**
 * Browser-side fetch helpers shared by the FG-26 user-management components.
 *
 * Every call goes to an `agent-home` BFF route, never to the Python API and
 * never to GoTrue: the service-role key stays on the box, and the owner/admin
 * gate is applied server-side (twice — the BFF for UX, Python as the authority).
 */

/**
 * Matches ``DEFAULT_PAGE_SIZE`` in `hermes_cli/members.py`: the server-rendered
 * first page and the client's own fetches must ask for the same number of rows,
 * or paging jumps by one amount and renders another.
 *
 * It lives here, in a plain module, rather than in `UsersView` — a server
 * component may import a *component* from a `"use client"` module, but a plain
 * value it receives is a client reference, not the number it was written as.
 */
export const PAGE_SIZE = 50;

/** A refusal from a BFF route, carrying the status so callers can act on 403. */
export class ForwardError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ForwardError";
    this.status = status;
  }
}

type Method = "POST" | "PUT" | "DELETE";

/** Send JSON to a BFF route and parse its envelope, throwing on refusal. */
export async function sendJson<T>(
  url: string,
  method: Method,
  body?: unknown,
): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const parsed = (await res.json()) as T & { detail?: string; error?: string };
  if (!res.ok) {
    throw new ForwardError(
      res.status,
      parsed.detail ?? parsed.error ?? "The request was refused.",
    );
  }
  return parsed;
}

/** The message to show for a thrown refusal, without leaking internals. */
export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

/** An absolute activation URL for a path the server returned, for copy/paste. */
export function activationUrl(path: string): string {
  if (typeof window === "undefined") return path;
  return new URL(path, window.location.origin).toString();
}
