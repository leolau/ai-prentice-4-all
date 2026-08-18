/**
 * The projects writes' answer envelopes (design ed.3.2 §13, Block 1 fixes).
 *
 * Each write answers with more than a status: accept carries the updated row
 * plus the closure offer, continue carries the run inside an envelope with
 * the budget gate, add-directive carries the full new row. Applying those to
 * panel state happens through these pure helpers — one place per envelope
 * shape, testable without a DOM.
 */
import type {
  ProjectDirective,
  ProjectOutputWithDeliveries,
  ProjectRun,
} from "@/types";

/** POST …/outputs/:id/accept answers with the updated row + the closure
 * offer. Merge the row (joined deliveries survive the spread) and surface
 * the offer. */
export function applyAcceptEnvelope(
  outputs: ProjectOutputWithDeliveries[],
  outputId: string,
  envelope: {
    output?: Partial<ProjectOutputWithDeliveries>;
    offers_closure?: boolean;
  },
): { outputs: ProjectOutputWithDeliveries[]; offersClosure: boolean } {
  const updated = envelope.output ?? {};
  return {
    outputs: outputs.map((row) =>
      row.id === outputId ? { ...row, ...updated } : row,
    ),
    offersClosure: envelope.offers_closure === true,
  };
}

/** POST …/runs/:n/continue answers `{run, promoted, budget_gate}`; cancel
 * answers the bare run row. Unwrap whichever came back — a run row only
 * merges when it carries a status, and the budget gate is whatever the
 * envelope says (absent → cleared). */
export function unwrapRunEnvelope(
  data: Record<string, unknown>,
): { run: Partial<ProjectRun> | null; budgetGate: string | null } {
  const envelope = data as {
    run?: Partial<ProjectRun>;
    budget_gate?: string | null;
  };
  const updated = (envelope.run ?? data) as Partial<ProjectRun>;
  return {
    run: updated.status ? updated : null,
    budgetGate: envelope.budget_gate ?? null,
  };
}

/** POST …/directives answers the full new row — body, author, date — with
 * `applies_from` riding flat beside it. The panel prepends the row so the
 * instruction shows without a reload. */
export function prependDirective(
  directives: ProjectDirective[],
  created: ProjectDirective & { applies_from?: string },
): ProjectDirective[] {
  return [created, ...directives];
}
