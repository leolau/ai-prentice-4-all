/**
 * Browser-side fetch helpers for the FG-30 profile-suggestion components.
 *
 * Every call goes to an `agent-home` BFF route, never to the Python API: the
 * Python layer is the authority on owner-only adopt/dismiss (it binds the
 * requesting principal via `_comms_resolve_principal`, the #253 fix), and a
 * 403 from upstream is the real gate — not a BFF re-derivation.
 */
export class ForwardError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ForwardError";
    this.status = status;
  }
}

/** Send JSON (or an empty POST) to a BFF route and parse its envelope. */
export async function sendJson<T>(
  url: string,
  method: "POST" | "PUT" | "DELETE",
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