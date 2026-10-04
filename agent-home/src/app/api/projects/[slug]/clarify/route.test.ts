/**
 * BFF route tests for scope clarification: principal gate, validation before
 * any upstream call, normalised payloads and the idempotency key forwarded.
 */
import { describe, expect, it, vi } from "vitest";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { POST as ANSWERS } from "./answers/route";
import { POST as CONFIRM } from "./confirm/route";
import { POST as QUESTIONS } from "./questions/route";
import { GET } from "./route";

const params = { params: Promise.resolve({ slug: "mou" }) };

function req(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("http://x/api/projects/mou/clarify", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("clarify BFF routes", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    clientState.client = { projectClarify: vi.fn() };
    expect((await GET(req({}), params)).status).toBe(401);
    expect((await ANSWERS(req({ answers: [{ id: "q", answer: "a" }] }), params)).status).toBe(401);
    principalState.principal = { user_id: "leo" };
  });

  it("GET proxies the state", async () => {
    const projectClarify = vi.fn(async () => ({ status: "open" }));
    clientState.client = { projectClarify };
    const res = await GET(req({}), params);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "open" });
    expect(projectClarify).toHaveBeenCalledWith("mou");
  });

  it("questions forwards focus and the idempotency key; refuses a long focus", async () => {
    const askClarifyQuestions = vi.fn(async () => ({ job: { status: "running" } }));
    clientState.client = { askClarifyQuestions };
    await QUESTIONS(req({ focus: " budget " }, { "Idempotency-Key": "k1" }), params);
    expect(askClarifyQuestions).toHaveBeenLastCalledWith("mou", { focus: "budget" }, "k1");
    await QUESTIONS(req({}), params);
    expect(askClarifyQuestions).toHaveBeenLastCalledWith("mou", {}, undefined);
    const long = await QUESTIONS(req({ focus: "x".repeat(501) }), params);
    expect(long.status).toBe(400);
    expect(askClarifyQuestions).toHaveBeenCalledTimes(2);
  });

  it("answers normalises the list and refuses blanks before upstream", async () => {
    const answerClarifyQuestions = vi.fn(async () => ({ status: "answered" }));
    clientState.client = { answerClarifyQuestions };
    expect((await ANSWERS(req({ answers: [] }), params)).status).toBe(400);
    expect((await ANSWERS(req({ answers: [{ id: "a", answer: "  " }] }), params)).status).toBe(400);
    expect(
      (await ANSWERS(req({ answers: [{ id: "a", answer: "x".repeat(2001) }] }), params)).status,
    ).toBe(400);
    expect(answerClarifyQuestions).not.toHaveBeenCalled();
    await ANSWERS(
      req(
        { answers: [{ id: " a ", answer: " Teachers " }, { id: "b", skip: true }, { nope: 1 }, 5] },
        { "Idempotency-Key": "k2" },
      ),
      params,
    );
    expect(answerClarifyQuestions).toHaveBeenCalledWith(
      "mou",
      { answers: [{ id: "a", answer: "Teachers" }, { id: "b", skip: true }] },
      "k2",
    );
  });

  it("confirm forwards only understanding and draft_plan", async () => {
    const confirmClarify = vi.fn(async () => ({ state: {}, plan_draft: null }));
    clientState.client = { confirmClarify };
    await CONFIRM(req({ understanding: " A deck ", draft_plan: true, extra: 1 }, { "Idempotency-Key": "k3" }), params);
    expect(confirmClarify).toHaveBeenLastCalledWith(
      "mou",
      { understanding: "A deck", draft_plan: true },
      "k3",
    );
    await CONFIRM(req({ draft_plan: "yes" }), params);
    expect(confirmClarify).toHaveBeenLastCalledWith("mou", {}, undefined);
    expect((await CONFIRM(req({ understanding: "x".repeat(4001) }), params)).status).toBe(400);
  });
});
