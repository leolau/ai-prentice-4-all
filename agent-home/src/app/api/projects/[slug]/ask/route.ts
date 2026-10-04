/**
 * POST /api/projects/:slug/ask — "Ask about this project". Body:
 * `{ question, history? }`. Read-only: the answer comes from the project's
 * current state in a one-shot model call of its own, never the running
 * tasks' conversation. The `Idempotency-Key` is forwarded so a retry
 * replays the first answer instead of asking again.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export const QUESTION_MAX = 2000;
const HISTORY_MAX = 6;

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const question = String(body.question ?? "").trim();
  if (!question) return invalidRequest("Type a question first.");
  if (question.length > QUESTION_MAX) {
    return invalidRequest(
      `That question is too long — keep it under ${QUESTION_MAX} characters.`,
    );
  }
  const history = Array.isArray(body.history)
    ? body.history
        .filter(
          (t): t is { q: unknown; a: unknown } =>
            !!t && typeof t === "object" && "q" in t && "a" in t,
        )
        .map((t) => ({ q: String(t.q ?? ""), a: String(t.a ?? "") }))
        .filter((t) => t.q && t.a)
        .slice(-HISTORY_MAX)
    : [];
  const key = req.headers.get("Idempotency-Key") ?? undefined;
  return withPrincipal((client) =>
    client.askProject(slug, { question, history }, key),
  );
}
