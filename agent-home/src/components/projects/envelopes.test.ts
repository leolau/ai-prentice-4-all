/**
 * Block 5 — the F1 class: every projects write's ACTUAL upstream envelope,
 * fed through the panel's state update (envelopes.ts). The fixtures mirror
 * what hermes_cli/projects_api.py answers — drift on either side fails here.
 */
import { describe, expect, it } from "vitest";

import {
  applyAcceptEnvelope,
  prependDirective,
  unwrapRunEnvelope,
} from "@/components/projects/envelopes";
import type {
  ProjectDirective,
  ProjectOutputWithDeliveries,
} from "@/types";

const NOW = Math.floor(Date.now() / 1000);

const OUTPUT = (over: Partial<ProjectOutputWithDeliveries>): ProjectOutputWithDeliveries => ({
  id: "out_1",
  project_id: "prj_1",
  seq: 1,
  title: "The digest itself",
  spec: null,
  kind: "artifact",
  required: 1,
  recurring: 1,
  status: "delivered",
  delivered_at: NOW - 3_600,
  accepted_at: null,
  accepted_by: null,
  created_at: NOW - 86_400,
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
  ...over,
});

describe("accept envelope → outputs state", () => {
  // What POST /outputs/:id/accept answers (projects_api.accept_output):
  // the updated row, who accepted it, and the closure offer.
  const UPSTREAM_ACCEPT = {
    output: {
      ...OUTPUT({
        status: "accepted" as const,
        accepted_at: NOW,
        accepted_by: "leo",
      }),
    },
    accepted: "out_1",
    by: "leo",
    offers_closure: true,
  };

  it("merges the accepted row and surfaces the closure offer", () => {
    const rows = [OUTPUT({}), OUTPUT({ id: "out_2", seq: 2, status: "pending", deliveries: [] })];
    const applied = applyAcceptEnvelope(rows, "out_1", UPSTREAM_ACCEPT);
    const merged = applied.outputs.find((row) => row.id === "out_1");
    expect(merged?.status).toBe("accepted");
    expect(merged?.accepted_by).toBe("leo");
    // The joined deliveries survive the merge — Accept merges, not refetches.
    expect(merged?.deliveries).toHaveLength(1);
    // Only the target row moves.
    expect(applied.outputs.find((row) => row.id === "out_2")?.status).toBe("pending");
    expect(applied.offersClosure).toBe(true);
  });

  it("keeps closure silent when the offer is absent", () => {
    const applied = applyAcceptEnvelope([OUTPUT({})], "out_1", {
      output: { status: "accepted" },
      offers_closure: false,
    });
    expect(applied.offersClosure).toBe(false);
  });

  it("survives an envelope without the row (nothing merges, nothing throws)", () => {
    const applied = applyAcceptEnvelope([OUTPUT({})], "out_1", { offers_closure: false });
    expect(applied.outputs[0].status).toBe("delivered");
  });
});

describe("run envelope → run state", () => {
  // What POST /runs/:n/continue answers (projects_run.continue_run):
  // the run inside an envelope with what promoted and the budget gate.
  const UPSTREAM_CONTINUE = {
    run: { run_no: 14, status: "running", outcome: null },
    promoted: ["task_3"],
    budget_gate: "Run 14 has spent $9.80 of a $10.00 budget — continue?",
  };

  it("unwraps the continue envelope: run merges, gate shows", () => {
    const { run, budgetGate } = unwrapRunEnvelope(
      UPSTREAM_CONTINUE as unknown as Record<string, unknown>,
    );
    expect(run?.status).toBe("running");
    expect(budgetGate).toContain("$9.80");
  });

  it("clears the budget gate when the envelope carries none", () => {
    const { run, budgetGate } = unwrapRunEnvelope({
      run: { run_no: 14, status: "running" },
      promoted: [],
      budget_gate: null,
    });
    expect(run?.status).toBe("running");
    expect(budgetGate).toBeNull();
  });

  // What POST /runs/:n/cancel answers: the bare run row, no envelope.
  it("unwraps cancel's bare run row the same way", () => {
    const { run, budgetGate } = unwrapRunEnvelope({
      run_no: 14,
      status: "cancelled",
      outcome: "cancelled",
    });
    expect(run?.status).toBe("cancelled");
    expect(budgetGate).toBeNull();
  });

  it("refuses to merge a payload with no run status", () => {
    const { run } = unwrapRunEnvelope({ detail: "gone" });
    expect(run).toBeNull();
  });
});

describe("add-directive envelope → guidance state", () => {
  // What POST /directives answers (projects_api.add_directive_route): the
  // full new row with applies_from riding flat beside it.
  const UPSTREAM_CREATED: ProjectDirective & { applies_from: string } = {
    id: "dir_9",
    project_id: "prj_1",
    kind: "directive",
    body: "Never email before 9am",
    scope: "project",
    target_ref: null,
    rating: null,
    author_user_id: "leo",
    created_at: NOW,
    active: 1,
    retired_at: null,
    superseded_by: null,
    applies_from: "next run",
  };

  it("prepends the created row so it shows without a reload", () => {
    const existing: ProjectDirective[] = [
      { ...UPSTREAM_CREATED, id: "dir_1", body: "Old rule" },
    ];
    const rows = prependDirective(existing, UPSTREAM_CREATED);
    expect(rows[0].id).toBe("dir_9");
    expect(rows[0].body).toBe("Never email before 9am");
    expect(rows).toHaveLength(2);
  });
});
