import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// ProjectDetailView + RunView use next/navigation — stub the router hooks
// so SSR rendering works.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: () => {}, push: () => {} }),
}));

import { AddToProjectSheet } from "@/components/projects/AddToProjectSheet";
import { CardDetailView } from "@/components/projects/CardDetailView";
import { ProjectDetailView } from "@/components/projects/ProjectDetailView";
import { RunView } from "@/components/projects/RunView";
import { BoardPanel } from "@/components/projects/panels/BoardPanel";
import { BriefPanel } from "@/components/projects/panels/BriefPanel";
import { FilesPanel } from "@/components/projects/panels/FilesPanel";
import { GuidancePanel } from "@/components/projects/panels/GuidancePanel";
import { LinkRow } from "@/components/projects/panels/LinkRow";
import { MemoriesPanel } from "@/components/projects/panels/MemoriesPanel";
import { OutputsPanel } from "@/components/projects/panels/OutputsPanel";
import { PeoplePanel } from "@/components/projects/panels/PeoplePanel";
import { PlanPanel } from "@/components/projects/panels/PlanPanel";
import { ProgressPanel } from "@/components/projects/panels/ProgressPanel";
import { ReferencesPanel } from "@/components/projects/panels/ReferencesPanel";
import { RunsPanel } from "@/components/projects/panels/RunsPanel";
import { ToolsPanel } from "@/components/projects/panels/ToolsPanel";
import type {
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
  ProjectDirective,
  ProjectLink,
  ProjectOutputWithDeliveries,
  ProjectPlaybookResponse,
  ProjectRun,
} from "@/types";

const NOW = Math.floor(Date.now() / 1000);

const LINK = (over: Partial<ProjectLink>): ProjectLink => ({
  project_id: "prj_1",
  kind: "url",
  profile: "default",
  ref: "https://example.com",
  label: null,
  added_by: "leo",
  added_at: NOW,
  resolved: true,
  ...over,
});

const PROJECT: ProjectDetail = {
  id: "prj_1",
  slug: "monday-digest",
  name: "Send the Monday digest",
  description: "The digest the team reads before standup.",
  icon: null,
  color: null,
  board_slug: "monday",
  primary_path: null,
  archived: false,
  created_at: NOW - 30 * 86_400,
  goal: "The team starts Monday already briefed",
  visibility: "shared",
  owner_user_id: "leo_owner",
  status: "active",
  cadence: "repeatable",
  schedule: "every monday 09:00",
  review_every: null,
  autonomy: "supervised",
  max_in_progress: 1,
  budget_usd_per_run: null,
  definition_of_done: null,
  target_audience: "the platform team",
  score_rubric: null,
  toolsets: "web,file",
  skills: "digest-writer",
  due_at: null,
  host_profile: "default",
  cron_job_id: "cron_1",
  summary: "Where this stands: run 14 waiting on your answer about the tone.",
  summary_at: NOW - 3_600,
  last_reviewed_at: null,
  next_run_at: NOW + 3 * 86_400,
  outputs: [],
  members: [{ project_id: "prj_1", user_id: "leo", role: "lead", added_by: null, added_at: NOW }],
  profiles: [
    { project_id: "prj_1", profile: "default", role: "host", added_by: null, added_at: NOW },
    { project_id: "prj_1", profile: "worker", role: "worker", added_by: null, added_at: NOW },
  ],
  contacts: [],
  links: {},
  progress: {
    rung: "outputs",
    label: "outputs",
    headline: "1 of 2 outputs accepted",
    accepted: 1,
    required: 2,
    cards: { total: 3, done: 1, running: 1, blocked: 1 },
  },
  score: null,
  health: "attention",
  runs: [
    {
      run_no: 14,
      status: "waiting",
      trigger: "schedule",
      started_at: NOW - 7_200,
      ended_at: null,
      duration_seconds: null,
      outcome: null,
      score_user: null,
    },
  ],
  card_rollup: { total: 3, done: 1, running: 1, blocked: 1 },
  recent_events: [],
};

const CARD = (over: Partial<ProjectBoardTask>): ProjectBoardTask => ({
  id: "task_1",
  title: "Draft the digest",
  body: null,
  status: "running",
  assignee: "default",
  priority: 3,
  created_at: NOW - 86_400,
  started_at: NOW - 3_600,
  completed_at: null,
  tenant: null,
  project_id: "prj_1",
  result: null,
  current_step_key: "draft",
  ...over,
});

const BOARD: ProjectBoardView = {
  columns: [
    { name: "todo", tasks: [CARD({ id: "task_1", status: "todo" })] },
    { name: "running", tasks: [CARD({ id: "task_2" })] },
    { name: "blocked", tasks: [CARD({ id: "task_3", status: "blocked", title: "Blocked by tone decision" })] },
  ],
};

const OUTPUT = (over: Partial<ProjectOutputWithDeliveries>): ProjectOutputWithDeliveries => ({
  id: "out_1",
  project_id: "prj_1",
  seq: 1,
  title: "The digest itself",
  spec: null,
  kind: "artifact",
  required: 1,
  recurring: 1,
  status: "pending",
  delivered_at: null,
  accepted_at: null,
  accepted_by: null,
  created_at: NOW,
  deliveries: [],
  ...over,
});

describe("detail panels", () => {
  it("BriefPanel renders the requirements and the audience chip", () => {
    const html = renderToStaticMarkup(<BriefPanel project={PROJECT} />);
    expect(html).toContain("The digest the team reads before standup.");
    expect(html).toContain("the platform team");
  });

  it("OutputsPanel puts undelivered required outputs first", () => {
    const html = renderToStaticMarkup(
      <OutputsPanel
        slug="monday-digest"
        outputs={[
          OUTPUT({ id: "out_delivered", seq: 1, status: "delivered", delivered_at: NOW }),
          OUTPUT({ id: "out_pending", seq: 2, title: "Still owed" }),
          OUTPUT({ id: "out_optional", seq: 3, title: "Nice to have", required: 0 }),
        ]}
      />,
    );
    const pending = html.indexOf("Still owed");
    const delivered = html.indexOf("The digest itself");
    const optional = html.indexOf("Nice to have");
    expect(pending).toBeGreaterThan(-1);
    expect(pending).toBeLessThan(delivered);
    expect(delivered).toBeLessThan(optional);
    // Accept lives in the Outputs panel and only for delivered rows.
    expect(html).toContain("Accept");
  });

  it("ProgressPanel shows the ladder headline and blocked cards", () => {
    const html = renderToStaticMarkup(
      <ProgressPanel
        slug="monday-digest"
        project={PROJECT}
        blockedCards={[CARD({ id: "task_3", status: "blocked", title: "Blocked by tone decision" })]}
      />,
    );
    expect(html).toContain("1 of 2 outputs accepted");
    expect(html).toContain("Blocked by tone decision");
    expect(html).toContain("/projects/monday-digest/cards/task_3");
  });

  it("BoardPanel degrades when the board read failed, and says so", () => {
    const html = renderToStaticMarkup(<BoardPanel slug="monday-digest" board={null} />);
    expect(html).toContain("unavailable");
  });

  it("BoardPanel renders columns and card links", () => {
    const html = renderToStaticMarkup(<BoardPanel slug="monday-digest" board={BOARD} />);
    expect(html).toContain("blocked");
    expect(html).toContain("/projects/monday-digest/cards/task_2");
  });

  it("RunsPanel links each run row to the run page", () => {
    const html = renderToStaticMarkup(<RunsPanel slug="monday-digest" runs={PROJECT.runs} />);
    expect(html).toContain("/projects/monday-digest/runs/14");
    expect(html).toContain("waiting");
  });

  it("PlanPanel covers the no-plan state", () => {
    const html = renderToStaticMarkup(<PlanPanel playbook={null} />);
    expect(html).toContain("Plan");
  });

  it("PlanPanel renders the active revision's steps and provenance", () => {
    const playbook: ProjectPlaybookResponse = {
      active: {
        project_id: "prj_1",
        rev: 3,
        body: "Write, review, send.",
        steps: [
          { key: "draft", title: "Draft it", assignee: "default" },
          { key: "send", title: "Send it", checkpoint: true },
        ],
        active: 1,
        created_by: "leo",
        created_at: NOW - 86_400,
        activated_at: NOW - 43_200,
        note: "added the send checkpoint",
      },
      revisions: [],
    };
    const html = renderToStaticMarkup(<PlanPanel playbook={playbook} />);
    expect(html).toContain("Draft it");
    expect(html).toContain("Send it");
    expect(html).toContain("revision 3");
  });

  it("PlanPanel shows a retro's proposed revision awaiting activation", () => {
    const playbook: ProjectPlaybookResponse = {
      active: null,
      revisions: [
        {
          project_id: "prj_1",
          rev: 2,
          body: "Draft, then read it aloud before sending.",
          steps: [],
          active: 0,
          created_by: "leo",
          created_at: NOW - 3_600,
          activated_at: null,
          note: "proposed by run 14",
        },
      ],
    };
    const html = renderToStaticMarkup(<PlanPanel playbook={playbook} />);
    expect(html).toContain('data-component="ProposedRevisions"');
    expect(html).toContain("awaiting activation");
    expect(html).toContain("proposed by run 14");
  });

  it("GuidancePanel shows proposed directives with the member's Activate", () => {
    const proposed: ProjectDirective = {
      id: "dir_1",
      project_id: "prj_1",
      kind: "directive",
      body: "Never email before 9am",
      scope: "project",
      target_ref: null,
      rating: null,
      author_user_id: "leo",
      created_at: NOW - 3_600,
      active: 0,
      retired_at: null,
      superseded_by: null,
    };
    const html = renderToStaticMarkup(
      <GuidancePanel
        slug="monday-digest"
        initial={{ directives: [], proposed: [proposed], applies_from: "next run" }}
      />,
    );
    expect(html).toContain('data-component="ProposedDirectives"');
    expect(html).toContain("Never email before 9am");
    expect(html).toContain("Activate");
  });

  it("PeoplePanel marks the host profile", () => {
    const html = renderToStaticMarkup(<PeoplePanel project={PROJECT} />);
    expect(html).toContain("host");
    expect(html).toContain("worker");
    expect(html).toContain("leo");
  });

  it("FilesPanel collapses to the Add affordance when empty", () => {
    const html = renderToStaticMarkup(<FilesPanel project={PROJECT} />);
    expect(html).toContain("Add a file");
  });

  it("ReferencesPanel and MemoriesPanel hide entirely when empty", () => {
    expect(renderToStaticMarkup(<ReferencesPanel project={PROJECT} />)).toBe("");
    expect(renderToStaticMarkup(<MemoriesPanel project={PROJECT} />)).toBe("");
  });

  it("ReferencesPanel keeps samples separate from references", () => {
    const html = renderToStaticMarkup(
      <ReferencesPanel
        project={{
          ...PROJECT,
          links: {
            sample: [LINK({ kind: "sample", ref: "sample.md", label: "Match this" })],
            reference: [LINK({ kind: "reference", ref: "ref.md", label: "Read this" })],
          },
        }}
      />,
    );
    expect(html).toContain("Samples");
    expect(html.indexOf("Match this")).toBeLessThan(html.indexOf("Read this"));
  });

  it("ToolsPanel splits the CSV narrowing into chips", () => {
    const html = renderToStaticMarkup(<ToolsPanel project={PROJECT} />);
    expect(html).toContain("web");
    expect(html).toContain("file");
    expect(html).toContain("digest-writer");
    expect(html).toContain("default");
  });
});

describe("LinkRow", () => {
  it("maps kinds onto the right hrefs", () => {
    const url = renderToStaticMarkup(<LinkRow link={LINK({})} />);
    expect(url).toContain('href="https://example.com"');
    expect(url).toContain('target="_blank"');

    const arrival = renderToStaticMarkup(
      <LinkRow link={LINK({ kind: "arrival", ref: "inc_1", label: "A file from WhatsApp" })} />,
    );
    expect(arrival).toContain('href="/inbox/inc_1"');

    const todo = renderToStaticMarkup(
      <LinkRow link={LINK({ kind: "todo", ref: "todo_1" })} />,
    );
    expect(todo).toContain('href="/todos/todo_1"');

    // No known surface: a static row, never a dead link.
    const memory = renderToStaticMarkup(
      <LinkRow link={LINK({ kind: "memory", ref: "mem_1" })} />,
    );
    expect(memory).not.toContain("<a");
  });

  it("greys an unresolved pointer instead of leaking content", () => {
    const html = renderToStaticMarkup(
      <LinkRow link={LINK({ kind: "todo", ref: "todo_1", resolved: null })} />,
    );
    expect(html).toContain("text-[var(--color-muted)]");
  });
});

describe("ProjectDetailView", () => {
  it("renders the header, actions and the §13 panel order", () => {
    const html = renderToStaticMarkup(
      <ProjectDetailView project={PROJECT} board={BOARD} playbook={null} directives={null} />,
    );
    expect(html).toContain('data-component="ProjectDetailView"');
    expect(html).toContain("Send the Monday digest");
    expect(html).toContain("Run now");
    // A waiting run earns the Continue button.
    expect(html).toContain("Continue run 14");
    // The agent's standing line rides under the header.
    expect(html).toContain("Where this stands");
    // The rolling summary says how fresh it is (§17 step 11).
    expect(html).toContain("where this stands · updated today");
    for (const label of ["Brief", "Outputs", "Progress", "Board", "Runs", "Plan", "Guidance", "People", "Files", "Tools"]) {
      expect(html).toContain(label);
    }
    // Empty References/Memories hide — anchors included.
    expect(html).not.toContain('href="#panel-references"');
    expect(html).not.toContain('href="#panel-memories"');
  });
});

describe("RunView", () => {
  const RUN: ProjectRun = {
    id: "run_14",
    project_id: "prj_1",
    run_no: 14,
    trigger: "schedule",
    triggered_by: null,
    profile: "default",
    playbook_rev: 3,
    status: "waiting",
    started_at: NOW - 7_200,
    ended_at: null,
    session_id: null,
    trace_id: null,
    outcome: null,
    summary: null,
    retro: null,
    retro_at: null,
    score_self: null,
    score_user: null,
    score_note: null,
    scored_by: null,
    scored_at: null,
    error: null,
    cards: [{ task_id: "task_2", step_key: "draft", status: "running", title: "Draft the digest" }],
    cost: 0.42,
    cost_recorded: true,
    duration_seconds: 1_200,
    deliveries: [
      {
        id: "del_1",
        output_id: "out_1",
        run_id: "run_14",
        task_id: null,
        link_kind: "file",
        link_ref: "digest.md",
        profile: "default",
        label: "digest.md",
        note: null,
        delivered_at: NOW - 3_600,
      },
    ],
  };

  it("shows timing, cost, cards and deliveries", () => {
    const html = renderToStaticMarkup(<RunView slug="monday-digest" run={RUN} />);
    expect(html).toContain("Run #14");
    expect(html).toContain("$0.42");
    expect(html).toContain("/projects/monday-digest/cards/task_2");
    expect(html).toContain("digest.md");
    // A waiting run offers Continue; cancel stays available while live.
    expect(html).toContain("Continue");
    expect(html).toContain("Cancel");
    expect(html).not.toContain("Repeat this run");
  });

  it("flags unrecorded cost and offers repeat once the run closed", () => {
    const html = renderToStaticMarkup(
      <RunView slug="monday-digest" run={{ ...RUN, status: "done", cost: null, cost_recorded: false, ended_at: NOW }} />,
    );
    expect(html).toContain("not recorded");
    expect(html).toContain("Repeat this run");
    expect(html).not.toContain("Cancel");
  });
});

describe("CardDetailView", () => {
  it("renders the board row with its timing and worker summary", () => {
    const html = renderToStaticMarkup(
      <CardDetailView
        slug="monday-digest"
        card={{
          ...CARD({ body: "Write the thing.", result: "Done — sent to the channel." }),
          age: { created_age_seconds: 86_400, started_age_seconds: 3_600, time_to_complete_seconds: null },
          latest_summary: null,
        }}
      />,
    );
    expect(html).toContain("Draft the digest");
    expect(html).toContain("Write the thing.");
    expect(html).toContain("Done — sent to the channel.");
    expect(html).toContain("/projects/monday-digest");
  });
});

describe("AddToProjectSheet", () => {
  it("offers only linking when there is nothing to promote", () => {
    const html = renderToStaticMarkup(
      <AddToProjectSheet onClose={() => {}} fixedSlug="monday-digest" fixedName="Digest" />,
    );
    expect(html).toContain("Add link");
    expect(html).not.toContain("Promote to card");
  });

  it("offers promotion alongside linking for a to-do (§10)", () => {
    const html = renderToStaticMarkup(
      <AddToProjectSheet
        onClose={() => {}}
        prefill={{ kind: "todo", ref: "td_1", label: "Draft the plan" }}
        promote={{ todoId: "td_1", todoTitle: "Draft the plan" }}
      />,
    );
    expect(html).toContain("Promote to card");
    expect(html).toContain("Add link");
    // The one line of copy that states the difference (§13).
    expect(html).toContain("moves the");
    expect(html).toContain("to-do to working");
  });
});

// The boundary test is the production guard: every new module under
// src/components/projects must be listed — extend it when adding files.
describe("boundary expectations", () => {
  it("keeps filters.ts + format.ts server-safe by construction", async () => {
    // Importing without a DOM would throw if either module were "use client"
    // with client-only side effects at module scope.
    const filters = await import("@/components/projects/filters");
    const format = await import("@/components/projects/format");
    expect(filters.DEFAULT_VIEW).toBe("active");
    expect(format.dayDistance(NOW + 3_600)).toBe("today");
  });
});
