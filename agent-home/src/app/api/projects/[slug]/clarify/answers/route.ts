/**
 * POST /api/projects/:slug/clarify/answers — `{answers: [{id, answer} |
 * {id, skip: true}]}`. Answers can change until the round is confirmed.
 */
import { NextResponse } from "next/server";

import type { ClarifyAnswerInput } from "@/types";

import { invalidRequest, readBody, withPrincipal } from "../../../hermes-bridge";

export const ANSWER_MAX = 2000;

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const raw = Array.isArray(body.answers) ? body.answers : [];
  const answers: ClarifyAnswerInput[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const rec = item as Record<string, unknown>;
    const id = String(rec.id ?? "").trim();
    if (!id) continue;
    if (rec.skip === true) {
      answers.push({ id, skip: true });
      continue;
    }
    const answer = String(rec.answer ?? "").trim();
    if (!answer) return invalidRequest("Type an answer or skip the question.");
    if (answer.length > ANSWER_MAX) {
      return invalidRequest(`Keep each answer under ${ANSWER_MAX} characters.`);
    }
    answers.push({ id, answer });
  }
  if (answers.length === 0) return invalidRequest("Answer at least one question.");
  const key = req.headers.get("idempotency-key") ?? undefined;
  return withPrincipal((client) => client.answerClarifyQuestions(slug, { answers }, key));
}
