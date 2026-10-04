import { project, playbook, run, T0 } from "@/components/projects/changes/testFixtures";
import type { ProjectTabProps } from "@/components/projects/tabs/types";
import type { PlaybookRev, ProjectDirective } from "@/types";

const dir = (id: string, created_at: number, body: string): ProjectDirective => ({
  id,
  project_id: "prj_1",
  kind: "directive",
  body,
  scope: "project",
  target_ref: null,
  rating: null,
  author_user_id: "leo",
  created_at,
  active: 1,
  retired_at: null,
  superseded_by: null,
});

export function tabProps(over: Partial<ProjectTabProps> = {}): ProjectTabProps {
  const book = playbook();
  const rev2: PlaybookRev = { ...book.active!, rev: 2, activated_at: T0 + 2.5 * 3600 };
  return {
    project: project({
      runs: [run(3, { status: "running", ended_at: null }), run(2), run(1)],
    }),
    board: null,
    playbook: { active: rev2, revisions: [{ ...book.active!, active: 0 }, rev2] },
    directives: {
      directives: [dir("d_1", T0, "Keep it under 500 words"), dir("d_2", T0 + 2.2 * 3600, "Add a Chinese edition")],
      applies_from: "next run",
    },
    doctor: null,
    callerUserId: "leo",
    canLead: true,
    readiness: [],
    runnable: true,
    onNavigate: () => {},
    onChangeRequest: () => {},
    ...over,
  };
}
