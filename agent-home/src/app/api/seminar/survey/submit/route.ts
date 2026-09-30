/**
 * POST /api/seminar/survey/submit — store seminar survey answers.
 * **Unauthenticated on purpose**: attendees have no account; the personal
 * token from their WhatsApp link is what identifies them. Forwards to the
 * Python API, which validates, throttles, and queues the slides. The token is
 * never logged.
 */
import { NextResponse } from "next/server";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";
import { callerIp } from "@/lib/api/callerIp";
import type { SeminarSurveyAnswers } from "@/types";

interface SubmitBody {
  token?: unknown;
  answers?: unknown;
}

export async function POST(request: Request): Promise<NextResponse> {
  let body: SubmitBody;
  try {
    body = (await request.json()) as SubmitBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const token = typeof body.token === "string" ? body.token : "";
  if (!token || !body.answers || typeof body.answers !== "object") {
    return NextResponse.json(
      { error: "invalid_request", detail: "此問卷連結無效。 This survey link is not valid." },
      { status: 400 },
    );
  }
  try {
    return NextResponse.json(
      await new HermesApiClient().submitSeminarSurvey(
        { token, answers: body.answers as SeminarSurveyAnswers },
        callerIp(request),
      ),
    );
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "survey_failed", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      {
        error: "api_unreachable",
        detail: "問卷暫時無法使用。 The survey is unavailable right now.",
      },
      { status: 502 },
    );
  }
}
