"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { friendlyError } from "@/components/projects/errors";

import {
  AddToProjectSheet,
} from "@/components/projects/AddToProjectSheet";
import { EditBriefSheet } from "@/components/projects/EditBriefSheet";
import { ProjectLifecycleMenu } from "@/components/projects/ProjectLifecycleMenu";
import { ReadinessChecklist } from "@/components/projects/ReadinessChecklist";
import {
  extraFindings,
  isRunnable,
  readinessItems,
} from "@/components/projects/readiness";
import { agoLabel, dayDistance } from "@/components/projects/format";
import { SummariseSheet } from "@/components/projects/SummariseSheet";
import { CollapsedPanel } from "@/components/projects/panels/CollapsedPanel";
import { BoardPanel } from "@/components/projects/panels/BoardPanel";
import { BriefPanel } from "@/components/projects/panels/BriefPanel";
import { FilesPanel } from "@/components/projects/panels/FilesPanel";
import { GuidancePanel } from "@/components/projects/panels/GuidancePanel";
import { MemoriesPanel } from "@/components/projects/panels/MemoriesPanel";
import { OutputsPanel } from "@/components/projects/panels/OutputsPanel";
import { PeoplePanel } from "@/components/projects/panels/PeoplePanel";
import { PlanPanel } from "@/components/projects/panels/PlanPanel";
import { ProgressPanel } from "@/components/projects/panels/ProgressPanel";
import { ReferencesPanel } from "@/components/projects/panels/ReferencesPanel";
import { RunsPanel } from "@/components/projects/panels/RunsPanel";
import { SettingsPanel } from "@/components/projects/panels/SettingsPanel";
import { ToolsPanel } from "@/components/projects/panels/ToolsPanel";
import {
  CADENCE_GLYPH,
  CADENCE_LABEL,
  HEALTH_LABEL,
} from "@/components/projects/ProjectRow";
import { BusyRegion } from "@/components/ui/BusyRegion";
import { Pill, type Tone } from "@/components/ui/Pill";
import { useProjectEvents } from "@/components/projects/useProjectEvents";
import type {
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
  ProjectDirectivesResponse,
  ProjectDoctorDetail,
  ProjectHealth,
  ProjectPlaybookResponse,
} from "@/types";

const HEALTH_TONE: Record<ProjectHealth, Tone> = {
  ok: "success",
  attention: "warning",
  stalled: "danger",
};

/**
 * The sticky anchor strip, in panel order: Progress (what is next for you)
 * leads, then the deliverables and the work, then the record. Collapsed
 * panels keep their anchor — the wrapper carries the id.
 */
const PANEL_ANCHORS: { id: string; label: string }[] = [
  { id: "panel-progress", label: "Progress" },
  { id: "panel-outputs", label: "Outputs" },
  { id: "panel-brief", label: "Brief" },
  { id: "panel-board", label: "Board" },
  { id: "panel-runs", label: "Runs" },
  { id: "panel-plan", label: "Plan" },
  { id: "panel-settings", label: "Settings" },
  { id: "panel-guidance", label: "Guidance" },
  { id: "panel-people", label: "People" },
  { id: "panel-files", label: "Files" },
  { id: "panel-references", label: "References" },
  { id: "panel-memories", label: "Memories" },
  { id: "panel-tools", label: "Tools" },
];

/**
 * `/projects/[slug]` — the one place (§13). Panels, not tabs: everything the
 * project knows is on one scrollable page; the sticky strip only scrolls you
 * there. Fan-out safe by construction — each separately-fetched resource
 * arrives as `| null` and its panel says "unavailable" instead of failing
 * the page (§16).
 */
export function ProjectDetailView({
  project,
  board,
  playbook,
  directives,
  doctor = null,
  callerUserId,
  isInstanceAdmin,
}: {
  project: ProjectDetail;
  board: ProjectBoardView | null;
  playbook: ProjectPlaybookResponse | null;
  directives: ProjectDirectivesResponse | null;
  /** Server-side findings the local checklist cannot see (§9.2). */
  doctor?: ProjectDoctorDetail | null;
  /** The signed-in principal's user id — the lifecycle gate (§13). */
  callerUserId: string;
  /** Box-wide owner/admin outranks the per-project matrix (§11). */
  isInstanceAdmin: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [summariseOpen, setSummariseOpen] = useState(false);

  const callerRole =
    project.members.find((member) => member.user_id === callerUserId)?.role ??
    null;
  const canLead =
    isInstanceAdmin ||
    project.owner_user_id === callerUserId ||
    callerRole === "lead";

  const readiness = readinessItems(project, playbook);
  const runnable = isRunnable(readiness);
  const findings = extraFindings(doctor, readiness);

  // §12 live updates: a run promoted in the background becomes visible
  // without a manual reload — the poller refreshes when the event head
  // moves (E3).
  useProjectEvents(project.slug);

  const waitingRun = project.runs.find((run) => run.status === "waiting");
  const blockedCards: ProjectBoardTask[] =
    board?.columns.flatMap((column) => column.tasks).filter(
      (task) => task.status === "blocked",
    ) ?? [];

  const hasReferences =
    (project.links.sample ?? []).length > 0 ||
    (project.links.reference ?? []).length > 0 ||
    (project.links.url ?? []).length > 0;
  const hasMemories = (project.links.memory ?? []).length > 0;
  const hasFiles = (project.links.file ?? []).length > 0;
  // The owner is a member by construction; People is empty until a second
  // person or a contact is on it.
  const hasPeople =
    project.members.some((m) => m.user_id !== project.owner_user_id) ||
    project.contacts.length > 0;
  const hasTools = Boolean(project.toolsets?.trim() || project.skills?.trim());
  const anchors = PANEL_ANCHORS.filter(
    (anchor) =>
      (anchor.id !== "panel-references" || hasReferences) &&
      (anchor.id !== "panel-memories" || hasMemories),
  );

  const slugPath = `/api/projects/${encodeURIComponent(project.slug)}`;

  const post = async (path: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(path, { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "That did not go through."));
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  // Run now lands on the run page so the live activity stream is the first
  // thing the user sees; the backend answers `{run: {run_no, …}, …}`.
  const runNow = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/runs`, { method: "POST" });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: string;
        run?: { run_no?: number };
        run_no?: number;
      };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "That did not go through."));
        return;
      }
      const runNo = data.run?.run_no ?? data.run_no;
      if (typeof runNo === "number") {
        router.push(
          `/projects/${encodeURIComponent(project.slug)}/runs/${runNo}`,
        );
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  // Runs require status=active; the backend gate names whatever is missing
  // (outputs / members / profiles) in its 409 detail, shown in the header
  // error paragraph.
  const activate = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(slugPath, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status: "active" }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) {
        setError(friendlyError({ status: res.status, detail: data.detail }, "That did not go through."));
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const meta = [
    project.status,
    CADENCE_LABEL[project.cadence],
    project.schedule ?? undefined,
    project.next_run_at != null
      ? `next ${dayDistance(project.next_run_at)}`
      : undefined,
    project.autonomy,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div data-component="ProjectDetailView" className="flex flex-col gap-4">
      <BusyRegion busy={busy} label={busy ? "Talking to the agent…" : undefined}>
        <div className="flex flex-col gap-4">
          {/* ── Header ─────────────────────────────────────────────── */}
          <header
            id="project-header"
            data-component="ProjectHeader"
            className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
          >
            <div className="flex items-center gap-2">
              <span aria-hidden className="text-lg leading-none text-[var(--color-muted)]">
                {CADENCE_GLYPH[project.cadence]}
              </span>
              <h1 className="min-w-0 flex-1 truncate text-lg font-semibold">
                {project.name}
              </h1>
              <Pill tone={HEALTH_TONE[project.health]}>
                {HEALTH_LABEL[project.health]}
              </Pill>
              <ProjectLifecycleMenu
                project={project}
                callerUserId={callerUserId}
                isInstanceAdmin={isInstanceAdmin}
              />
            </div>
            {project.goal ? (
              <p className="mt-1 text-sm text-[var(--color-muted)]">
                {project.goal}
              </p>
            ) : null}
            <p className="mt-1 text-xs text-[var(--color-muted)]">{meta}</p>
            {project.summary ? (
              <div
                data-component="ProjectSummary"
                className="mt-2 rounded-xl bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <p className="italic">{project.summary}</p>
                {project.summary_at != null ? (
                  <p className="mt-1 text-xs text-[var(--color-muted)]">
                    Summarised {agoLabel(project.summary_at)}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="mt-3 flex flex-wrap gap-2">
              {project.archived ? (
                <p className="text-sm text-[var(--color-muted)]">
                  This project is archived — restore it (⋯) to run it again.
                </p>
              ) : (
              <>
              {project.status === "active" ? (
                <button
                  type="button"
                  onClick={() => void runNow()}
                  disabled={busy || !runnable}
                  title={runnable ? undefined : "See the checklist below."}
                  className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Run now
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void activate()}
                  disabled={busy}
                  className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
                >
                  Activate
                </button>
              )}
              {waitingRun ? (
                <button
                  type="button"
                  onClick={() =>
                    void post(
                      `${slugPath}/runs/${waitingRun.run_no}/continue`,
                    )
                  }
                  disabled={busy}
                  className="rounded-xl border border-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent)] disabled:opacity-50"
                >
                  Continue run {waitingRun.run_no}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => setAddOpen(true)}
                className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50"
              >
                Add
              </button>
              {canLead ? (
                <button
                  type="button"
                  onClick={() => setEditOpen(true)}
                  className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50"
                >
                  Edit
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => setSummariseOpen(true)}
                className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm disabled:opacity-50"
              >
                {project.summary ? "Update summary" : "Summarise"}
              </button>
              </>
              )}
            </div>

            {!project.archived ? (
              <ReadinessChecklist
                items={readiness}
                findings={findings}
                onActivate={() => void activate()}
                activating={busy}
              />
            ) : null}

            {error ? (
              <p className="mt-2 text-sm text-red-400" role="alert">
                {error}
              </p>
            ) : null}
          </header>

          {/* ── Sticky panel anchors ───────────────────────────────── */}
          <nav
            data-component="PanelAnchors"
            className="sticky top-0 z-20 -mx-1 overflow-x-auto bg-[var(--color-bg)]/90 px-1 py-2 backdrop-blur"
          >
            <ul className="flex w-max gap-1.5">
              {anchors.map((anchor) => (
                <li key={anchor.id}>
                  <a
                    href={`#${anchor.id}`}
                    className="block rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 text-xs whitespace-nowrap"
                  >
                    {anchor.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          {/* ── Panels — stacked on a phone, two columns from md: ── */}
          <div className="flex flex-col gap-4 md:grid md:grid-cols-2 md:items-start">
            <ProgressPanel
              slug={project.slug}
              project={project}
              blockedCards={blockedCards}
              readiness={readiness}
            />
            <OutputsPanel
              slug={project.slug}
              outputs={project.outputs}
              archived={project.archived}
            />
            <BriefPanel project={project} />
            <BoardPanel
              slug={project.slug}
              board={board}
              archived={project.archived}
            />
            <RunsPanel
              slug={project.slug}
              runs={project.runs}
              archived={project.archived}
            />
            <PlanPanel
              slug={project.slug}
              playbook={playbook}
              profiles={project.profiles.map((row) => row.profile)}
              canActivate={canLead}
              archived={project.archived}
            />
            <SettingsPanel
              project={project}
              canLead={canLead}
              hasActivePlan={Boolean(playbook?.active)}
            />
            <GuidancePanel
              slug={project.slug}
              initial={directives}
              archived={project.archived}
            />
            {hasPeople ? (
              <PeoplePanel project={project} archived={project.archived} />
            ) : (
              <CollapsedPanel
                anchor="panel-people"
                label="People"
                hint="just you — add someone"
              >
                <PeoplePanel project={project} archived={project.archived} />
              </CollapsedPanel>
            )}
            {hasFiles ? (
              <FilesPanel project={project} archived={project.archived} />
            ) : (
              <CollapsedPanel anchor="panel-files" label="Files" hint="none yet — add one">
                <FilesPanel project={project} archived={project.archived} />
              </CollapsedPanel>
            )}
            <ReferencesPanel project={project} />
            <MemoriesPanel project={project} />
            {hasTools ? (
              <ToolsPanel project={project} archived={project.archived} />
            ) : (
              <CollapsedPanel
                anchor="panel-tools"
                label="Tools"
                hint="full host toolset — narrow it"
              >
                <ToolsPanel project={project} archived={project.archived} />
              </CollapsedPanel>
            )}
          </div>
        </div>
      </BusyRegion>

      {addOpen ? (
        <AddToProjectSheet
          onClose={() => {
            setAddOpen(false);
            router.refresh();
          }}
          fixedSlug={project.slug}
          fixedName={project.name}
        />
      ) : null}

      {summariseOpen ? (
        <SummariseSheet
          slug={project.slug}
          initial={project.summary ?? ""}
          onClose={() => {
            setSummariseOpen(false);
            router.refresh();
          }}
        />
      ) : null}

      {editOpen ? (
        <EditBriefSheet
          project={project}
          onClose={() => {
            setEditOpen(false);
            router.refresh();
          }}
        />
      ) : null}
    </div>
  );
}
