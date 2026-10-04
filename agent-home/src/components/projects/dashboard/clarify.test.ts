import { describe, expect, it } from "vitest";

import {
  NOW,
  board,
  card,
  playbook,
  project,
  run,
} from "@/components/projects/dashboard/__fixtures__/dashboard";
import {
  clarifyOf,
  clarifyStatus,
  scopeSkipped,
  suggestScopeFirst,
} from "@/components/projects/dashboard/derive";
import {
  NEEDS_YOU_ORDER,
  needsYouItems,
  nextAction,
} from "@/components/projects/dashboard/nextAction";
import { standSteps } from "@/components/projects/dashboard/whereItStands";
import { isRunnable, readinessItems } from "@/components/projects/readiness";
import type { ReadinessItem } from "@/components/projects/readiness";
import type {
  ClarifySummary,
  ProjectBoardView,
  ProjectDetail,
  ProjectPlaybookResponse,
} from "@/types";

/** A project with no active plan and nothing waiting (fresh from the wizard). */
const NO_PLAN: ProjectPlaybookResponse = { active: null, revisions: [] };
const PLAN = playbook();

function clarify(over: Partial<ClarifySummary> = {}): ClarifySummary {
  return {
    status: "not_started",
    round: 0,
    open_count: 0,
    answered_count: 0,
    understanding: null,
    confirmed_at: null,
    ...over,
  };
}

const OPEN = clarify({ status: "open", round: 1, open_count: 4, answered_count: 1 });
const ANSWERED = clarify({ status: "answered", round: 1, answered_count: 5 });
const CONFIRMED = clarify({
  status: "confirmed",
  round: 1,
  answered_count: 5,
  understanding: "Four MOUs for the HK partners, signed-ready.",
  confirmed_at: NOW,
});

/** `project.clarify` absent (older servers) vs. explicitly not started. */
const fresh = (over: Partial<ProjectDetail> = {}) => project({ status: "planning", ...over });

function hero(
  p: ProjectDetail,
  pb: ProjectPlaybookResponse | null = NO_PLAN,
  opts: { board?: ProjectBoardView | null; readiness?: ReadinessItem[]; canLead?: boolean } = {},
) {
  return nextAction(p, opts.board ?? board(), opts.readiness ?? [], pb, NOW, opts.canLead ?? true);
}

function items(
  p: ProjectDetail,
  pb: ProjectPlaybookResponse | null = NO_PLAN,
  opts: { board?: ProjectBoardView | null; readiness?: ReadinessItem[]; canLead?: boolean } = {},
) {
  return needsYouItems({
    project: p,
    board: opts.board ?? board(),
    readiness: opts.readiness ?? [],
    playbook: pb,
    canLead: opts.canLead,
    now: NOW,
  });
}

function scopeStep(p: ProjectDetail, pb: ProjectPlaybookResponse | null) {
  const step = standSteps(p, board(), pb, NOW).find((s) => s.key === "scope");
  return step ? [step.state, step.sub, step.label] : null;
}

describe("clarify derivations", () => {
  it("an absent summary reads as not started", () => {
    const p = fresh();
    expect("clarify" in p).toBe(false);
    expect(clarifyStatus(p)).toBe("not_started");
    expect(clarifyOf(p)).toMatchObject({ status: "not_started", open_count: 0 });
    expect(clarifyStatus(fresh({ clarify: null }))).toBe("not_started");
    expect(clarifyStatus(fresh({ clarify: OPEN }))).toBe("open");
  });

  it("scope counts as skipped once a plan is active or a run happened", () => {
    expect(scopeSkipped(fresh(), NO_PLAN)).toBe(false);
    expect(scopeSkipped(fresh(), PLAN)).toBe(true);
    expect(scopeSkipped(fresh({ runs: [run()] }), NO_PLAN)).toBe(true);
    expect(scopeSkipped(fresh({ clarify: OPEN }), PLAN)).toBe(false);
  });

  it("suggests asking first only before any plan, revision or run", () => {
    expect(suggestScopeFirst(fresh(), NO_PLAN)).toBe(true);
    expect(suggestScopeFirst(fresh({ clarify: clarify() }), NO_PLAN)).toBe(true);
    expect(suggestScopeFirst(fresh(), PLAN)).toBe(false);
    expect(suggestScopeFirst(fresh({ runs: [run()] }), NO_PLAN)).toBe(false);
    // A drafted revision waiting for activation: the plan flow leads.
    const waiting = { active: null, revisions: [PLAN.active!] } as ProjectPlaybookResponse;
    expect(suggestScopeFirst(fresh(), waiting)).toBe(false);
    // The plan could not be loaded: we cannot tell, so we stay quiet.
    expect(suggestScopeFirst(fresh(), null)).toBe(false);
    expect(suggestScopeFirst(fresh({ archived: true }), NO_PLAN)).toBe(false);
    expect(suggestScopeFirst(fresh({ status: "done" }), NO_PLAN)).toBe(false);
    expect(suggestScopeFirst(fresh({ clarify: OPEN }), NO_PLAN)).toBe(false);
    expect(suggestScopeFirst(fresh({ clarify: CONFIRMED }), NO_PLAN)).toBe(false);
  });
});

describe("nextAction — scope", () => {
  it("absent clarify, no plan, no runs: let the agent ask about scope first", () => {
    const h = hero(fresh(), NO_PLAN, {
      readiness: [{ key: "plan", label: "plan", ok: false, hint: "Write a plan or ask the agent to draft one.", anchor: "?tab=plan", tab: "plan" }],
    });
    expect(h.key).toBe("scope:start");
    expect(h.state).toBe("needs_you");
    expect(h.needsPerson).toBe(true);
    expect(h.headline).toBe("Let the agent ask about scope first");
    expect(h.primary.intent).toEqual({ kind: "navigate", tab: "scope" });
    expect(h.primary.effect).toMatch(/3–7 questions/);
    expect(h.primary.effect).toMatch(/Nothing runs/);
    expect(h.secondary?.label).toBe("Draft the plan directly");
    expect(h.secondary?.intent).toEqual({ kind: "navigate", tab: "plan" });
  });

  it("explicit not_started behaves like absent", () => {
    expect(hero(fresh({ clarify: clarify() })).key).toBe("scope:start");
  });

  it("not_started with an active plan or runs: no nagging", () => {
    expect(hero(fresh({ status: "active" }), PLAN).key).toBe("ready");
    const ran = hero(fresh({ status: "active", runs: [run({ run_no: 1 })] }), NO_PLAN);
    expect(ran.key).not.toMatch(/^scope/);
    expect(items(fresh(), PLAN).some((i) => i.group === "scope")).toBe(false);
  });

  it("only someone who can lead is asked, and never on an archived project", () => {
    expect(hero(fresh(), NO_PLAN, { canLead: false }).key).not.toBe("scope:start");
    expect(items(fresh(), NO_PLAN, { canLead: false }).some((i) => i.group === "scope")).toBe(false);
    expect(hero(fresh({ archived: true })).state).toBe("archived");
    expect(items(fresh({ archived: true, clarify: OPEN }))).toEqual([]);
  });

  it("open questions: the hero points at them", () => {
    const h = hero(fresh({ clarify: OPEN }));
    expect(h.key).toBe("scope:open");
    expect(h.headline).toBe("The agent has 4 questions about scope");
    expect(h.primary.intent).toEqual({ kind: "navigate", tab: "scope" });
    // Still points there with a plan active (the owner asked for a new round).
    expect(hero(fresh({ status: "active", clarify: OPEN }), PLAN).key).toBe("scope:open");
    // Even a non-lead member who can write sees the open questions.
    expect(hero(fresh({ clarify: OPEN }), NO_PLAN, { canLead: false }).key).toBe("scope:open");
  });

  it("one open question reads in the singular", () => {
    const h = hero(fresh({ clarify: clarify({ status: "open", round: 2, open_count: 1 }) }));
    expect(h.headline).toBe("The agent has 1 question about scope");
    expect(h.primary.label).toBe("Answer it");
  });

  it("answered but not confirmed: confirm the agent's understanding", () => {
    const h = hero(fresh({ clarify: ANSWERED }));
    expect(h.key).toBe("scope:answered");
    expect(h.headline).toBe("Confirm the agent's understanding");
    expect(h.primary.intent).toEqual({ kind: "navigate", tab: "scope" });
  });

  it("confirmed: scope asks nothing more; the plan flow takes over", () => {
    const plan: ReadinessItem = { key: "plan", label: "plan", ok: false, hint: "Write a plan or ask the agent to draft one.", anchor: "?tab=plan", tab: "plan" };
    const h = hero(fresh({ clarify: CONFIRMED }), NO_PLAN, { readiness: [plan] });
    expect(h.key).toBe("readiness:plan");
    expect(items(fresh({ clarify: CONFIRMED })).some((i) => i.group === "scope")).toBe(false);
    expect(hero(fresh({ status: "active", clarify: CONFIRMED }), PLAN).key).toBe("ready");
  });

  it("a waiting run or the agent's triage cards still outrank scope", () => {
    const waiting = fresh({ clarify: OPEN, runs: [run({ run_no: 2, status: "waiting", ended_at: null })] });
    expect(hero(waiting, PLAN).state).toBe("waiting");
    const triage = board(card({ id: "t1", status: "triage", created_by: "default" }));
    expect(hero(fresh({ clarify: OPEN, runs: [run()] }), PLAN, { board: triage }).key).toBe("triage");
  });
});

describe("needsYouItems — scope ranking", () => {
  it("scope ranks after run and triage, ahead of blocked, outputs, inputs and readiness", () => {
    expect(NEEDS_YOU_ORDER).toEqual(["run", "triage", "scope", "blocked", "outputs", "inputs", "readiness"]);
    const p = fresh({
      clarify: OPEN,
      runs: [run({ run_no: 3, status: "waiting", ended_at: null })],
      links: {},
    });
    const b = board(
      card({ id: "b", status: "blocked" }),
      card({ id: "t1", status: "triage", created_by: "default" }),
    );
    const list = items(p, PLAN, {
      board: b,
      readiness: [{ key: "schedule", label: "schedule", ok: false, hint: "x", anchor: "?tab=settings", tab: "settings" }],
    });
    expect(list.map((i) => i.group)).toEqual(["run", "triage", "scope", "blocked", "inputs", "readiness"]);
    const ranks = list.map((i) => NEEDS_YOU_ORDER.indexOf(i.group));
    expect([...ranks].sort((x, y) => x - y)).toEqual(ranks);
  });

  it("gives every scope item a navigation to the Scope tab", () => {
    for (const c of [OPEN, ANSWERED, undefined]) {
      const item = items(fresh(c ? { clarify: c } : {})).find((i) => i.group === "scope");
      expect(item?.action.intent).toEqual({ kind: "navigate", tab: "scope" });
      expect(item?.action.effect.length).toBeGreaterThan(0);
    }
  });

  it("the hero's item is always in the list (so the card points up to it)", () => {
    for (const c of [OPEN, ANSWERED, undefined]) {
      const p = fresh(c ? { clarify: c } : {});
      const h = hero(p);
      expect(items(p).some((i) => i.key === h.key)).toBe(true);
    }
  });
});

describe("standSteps — Scope agreed", () => {
  it("sits between Inputs and Plan", () => {
    expect(standSteps(fresh(), board(), NO_PLAN, NOW).map((s) => s.key)).toEqual([
      "brief",
      "inputs",
      "scope",
      "plan",
      "iteration",
      "review",
      "done",
    ]);
  });

  it("absent / not started with no plan: it is next (or not yet, without a brief)", () => {
    expect(scopeStep(fresh(), NO_PLAN)).toEqual(["current", null, "Scope agreed"]);
    expect(scopeStep(fresh({ clarify: clarify() }), NO_PLAN)).toEqual(["current", null, "Scope agreed"]);
    expect(scopeStep(fresh({ goal: "" }), NO_PLAN)?.[0]).toBe("todo");
  });

  it("not started but a plan is already active (or it ran): done, skipped", () => {
    expect(scopeStep(fresh(), PLAN)).toEqual(["done", "skipped", "Scope agreed"]);
    expect(scopeStep(fresh({ runs: [run()] }), NO_PLAN)).toEqual(["done", "skipped", "Scope agreed"]);
  });

  it("open questions and an unconfirmed understanding need you", () => {
    expect(scopeStep(fresh({ clarify: OPEN }), NO_PLAN)).toEqual(["attention", "4 open", "Scope agreed"]);
    expect(scopeStep(fresh({ clarify: ANSWERED }), PLAN)).toEqual(["attention", "confirm", "Scope agreed"]);
  });

  it("confirmed: done", () => {
    expect(scopeStep(fresh({ clarify: CONFIRMED }), NO_PLAN)).toEqual(["done", null, "Scope agreed"]);
  });
});

describe("readiness — scope is never a run gate", () => {
  it("leaves the checklist and runnable unchanged whatever the clarify state", () => {
    const base = project();
    const baseline = readinessItems(base, PLAN);
    expect(isRunnable(baseline)).toBe(true);
    for (const c of [undefined, clarify(), OPEN, ANSWERED, CONFIRMED]) {
      const items = readinessItems(project(c ? { clarify: c } : {}), PLAN);
      expect(items).toEqual(baseline);
      expect(isRunnable(items)).toBe(true);
    }
  });
});
