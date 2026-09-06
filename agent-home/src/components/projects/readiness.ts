import type {
  ProjectDetail,
  ProjectDoctorDetail,
  ProjectPlaybookResponse,
} from "@/types";

export interface ReadinessItem {
  key: "outputs" | "profile" | "plan" | "schedule" | "status";
  label: string;
  ok: boolean;
  /** What to do when not ok — one short sentence. */
  hint: string;
  /** The panel anchor the item sends the user to. */
  anchor: string;
}

/**
 * What the run gate (`start_run`) will refuse on, computed from data the
 * page already holds so the header can say it *before* the click instead of
 * surfacing a 409 string after it. Doctor findings ride beside these for
 * anything only the server can see (a detached cron job, an overdue run).
 */
export function readinessItems(
  project: ProjectDetail,
  playbook: ProjectPlaybookResponse | null,
): ReadinessItem[] {
  const activePlan = playbook?.active ?? null;
  const hasSteps = (activePlan?.steps ?? []).length > 0;
  const items: ReadinessItem[] = [
    {
      key: "outputs",
      label: "At least one output",
      ok: project.outputs.length > 0,
      hint: "Declare what the project delivers.",
      anchor: "#panel-outputs",
    },
    {
      key: "profile",
      label: "A profile to run on",
      ok: project.profiles.length > 0,
      hint: "Add the host profile under People.",
      anchor: "#panel-people",
    },
    {
      key: "plan",
      label: "An active plan with steps",
      ok: activePlan != null && hasSteps,
      hint:
        activePlan == null
          ? playbook == null
            ? "The plan could not be loaded."
            : (playbook.revisions ?? []).length > 0
              ? "A revision is waiting — activate it."
              : "Write a plan or ask the agent to draft one."
          : "The active plan has no steps.",
      anchor: "#panel-plan",
    },
  ];
  if (project.cadence === "repeatable") {
    items.push({
      key: "schedule",
      label: "A schedule",
      ok: Boolean(project.schedule),
      hint: "Repeatable projects need a schedule to fire.",
      anchor: "#panel-settings",
    });
  }
  return items;
}

/** Doctor findings the local checklist does not already cover. */
export function extraFindings(
  doctor: ProjectDoctorDetail | null,
  items: ReadinessItem[],
): { code: string; severity: string; text: string }[] {
  if (!doctor) return [];
  const covered = new Set<string>();
  for (const item of items) {
    if (item.key === "plan") covered.add("no_active_playbook");
    if (item.key === "schedule") covered.add("no_schedule");
    if (item.key === "profile") covered.add("host_profile_missing");
  }
  return doctor.findings
    .filter((f) => !covered.has(f.code))
    .map((f) => ({
      code: f.code,
      severity: f.severity,
      text: f.detail ?? f.message ?? f.code,
    }));
}

export function isRunnable(items: ReadinessItem[]): boolean {
  return items.every((item) => item.ok);
}
