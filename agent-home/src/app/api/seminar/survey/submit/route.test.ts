import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/seminar/survey/submit/route";
import { HermesApiError } from "@/lib/api/client";

const submitSeminarSurvey = vi.fn();

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return {
    ...actual,
    HermesApiClient: class {
      submitSeminarSurvey(...args: unknown[]) {
        return submitSeminarSurvey(...args);
      }
    },
  };
});

function post(body: unknown, ip = "203.0.113.9"): Promise<Response> {
  return POST(
    new Request("http://localhost/api/seminar/survey/submit", {
      method: "POST",
      headers: { "content-type": "application/json", "x-forwarded-for": ip },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
  ) as unknown as Promise<Response>;
}

const ANSWERS = { rating: 5, grades: ["P1"], share: "no", next_step: "updates", name: "A" };

describe("POST /api/seminar/survey/submit", () => {
  beforeEach(() => vi.clearAllMocks());

  it("forwards the token, answers and caller IP", async () => {
    submitSeminarSurvey.mockResolvedValue({ ok: true, already_submitted: false });
    const res = await post({ token: "tok", answers: ANSWERS });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, already_submitted: false });
    expect(submitSeminarSurvey).toHaveBeenCalledWith(
      { token: "tok", answers: ANSWERS },
      "203.0.113.9",
    );
  });

  it("refuses a request without a token or answers before calling upstream", async () => {
    expect((await post({ answers: ANSWERS })).status).toBe(400);
    expect((await post({ token: "tok" })).status).toBe(400);
    expect((await post("not json")).status).toBe(400);
    expect(submitSeminarSurvey).not.toHaveBeenCalled();
  });

  it("passes the upstream status and wording through", async () => {
    submitSeminarSurvey.mockRejectedValue(
      new HermesApiError(404, "This survey link is not valid. 此問卷連結無效。"),
    );
    const res = await post({ token: "tok", answers: ANSWERS });
    expect(res.status).toBe(404);
    expect((await res.json()).detail).toContain("此問卷連結無效");
  });

  it("reports an unreachable API as 502", async () => {
    submitSeminarSurvey.mockRejectedValue(new TypeError("fetch failed"));
    const res = await post({ token: "tok", answers: ANSWERS });
    expect(res.status).toBe(502);
  });
});
