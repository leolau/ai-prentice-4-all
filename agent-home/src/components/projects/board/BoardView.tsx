"use client";

import Link from "next/link";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { CardTile } from "@/components/projects/board/CardTile";
import { NeedsYouStrip, type StripItem } from "@/components/projects/board/NeedsYouStrip";
import { NewCardForm } from "@/components/projects/board/NewCardForm";
import {
  LANES,
  boardTasks,
  canActOnBoard,
  cardRunNo,
  filterCounts,
  filterTasks,
  groupIntoLanes,
  laneEmpty,
  needsYou,
  openRunState,
  runNumbers,
  splitDone,
  type BoardFilter,
  type FilterContext,
  type LaneKey,
} from "@/components/projects/board/model";
import { useBoardContext } from "@/components/projects/board/useBoardContext";
import { BoardPanel } from "@/components/projects/panels/BoardPanel";
import { useServerState } from "@/components/ui/useRefresh";
import type {
  ProjectBoardContext,
  ProjectBoardTask,
  ProjectBoardView,
  ProjectDetail,
} from "@/types";

const CHIP =
  "rounded-full border px-2.5 py-1 text-xs aria-pressed:border-[var(--color-accent)] aria-pressed:bg-[var(--color-accent)]/15 aria-pressed:text-[var(--color-accent)]";

const FILTER_LABEL: Record<BoardFilter, string> = {
  all: "All",
  needs_me: "Needs me",
  mine: "Mine",
  agent: "Agent",
};

/**
 * The project's board for people: what needs a person on top, then four
 * lanes (Up next · Working · Waiting · Done) that fold the eight internal
 * stages by what they mean. "Show all 8 stages" brings back the full
 * stage view with its "Move to…" control.
 */
export function BoardView({
  project,
  board,
  callerUserId,
  canLead = false,
  context: contextProp,
  viewers,
}: {
  project: ProjectDetail;
  board: ProjectBoardView | null;
  callerUserId: string;
  canLead?: boolean;
  /** Skip the context read and use this (tests / server data). */
  context?: ProjectBoardContext | null;
  /** Other people viewing the board right now, when presence is known. */
  viewers?: string[];
}) {
  const slug = project.slug;
  const [liveBoard, setLiveBoard] = useServerState(board);
  const context = useBoardContext(slug, board, contextProp);
  const [filter, setFilter] = useState<BoardFilter>("all");
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const [allStages, setAllStages] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [held, setHeld] = useState<Record<string, ProjectBoardTask>>({});

  const update = useCallback(
    (fn: (b: ProjectBoardView) => ProjectBoardView) =>
      setLiveBoard((prev) => (prev ? fn(prev) : prev)),
    [setLiveBoard],
  );
  const onHold = useCallback(
    (task: ProjectBoardTask) =>
      setHeld((h) => (h[task.id] ? h : { ...h, [task.id]: task })),
    [],
  );
  const onRelease = useCallback(
    (taskId: string) =>
      setHeld((h) => {
        if (!h[taskId]) return h;
        const next = { ...h };
        delete next[taskId];
        return next;
      }),
    [],
  );

  const memberIds = useMemo(
    () => new Set(project.members.map((m) => m.user_id)),
    [project.members],
  );
  const profiles = useMemo(
    () => [...new Set([project.host_profile, ...project.profiles.map((p) => p.profile)].filter(
          (p): p is string => !!p,
        ))],
    [project.host_profile, project.profiles],
  );
  const canAct = canActOnBoard(project, callerUserId, canLead);
  const cardRuns = context?.card_runs ?? {};
  const ctx: FilterContext = { userId: callerUserId, canAct, cardRuns, memberIds };
  const [mountedAt] = useState(() => Math.floor(Date.now() / 1000));
  const now = liveBoard?.now ?? mountedAt;

  if (liveBoard == null) {
    return <BoardPanel slug={slug} board={null} archived={project.archived} />;
  }

  const tasks = boardTasks(liveBoard);
  const counts = filterCounts(tasks, ctx);
  const runs = runNumbers(tasks, cardRuns);
  const filtered = filterTasks(tasks, filter, ctx, runFilter);
  const isFiltered = filter !== "all" || runFilter != null;
  const lanes = groupIntoLanes(filtered);
  const openRun = openRunState(context, tasks, project.runs, now);

  const live = new Map(tasks.map((t) => [t.id, t]));
  const heldIds = new Set(Object.keys(held));
  const stripSource = filtered.filter((t) => !heldIds.has(t.id)).concat(Object.values(held));
  const strip: StripItem[] = needsYou(stripSource).map((item) => ({
    ...item,
    live: live.get(item.task.id) ?? null,
  }));
  const viewing = (viewers ?? []).filter((v) => v && v !== callerUserId);

  return (
    <section
      id="panel-board"
      data-component="BoardView"
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {(Object.keys(FILTER_LABEL) as BoardFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            disabled={allStages}
            className={`${CHIP} border-[var(--color-border)] disabled:opacity-40`}
          >
            {FILTER_LABEL[f]}
            {f === "needs_me" ? ` · ${counts.needs_me}` : ""}
          </button>
        ))}
        {runs.length > 0 ? (
          <select
            aria-label="Filter by run"
            value={runFilter ?? ""}
            disabled={allStages}
            onChange={(e) => setRunFilter(e.target.value ? Number(e.target.value) : null)}
            className={`${CHIP} border-[var(--color-border)] bg-[var(--color-surface)]`}
          >
            <option value="">All runs</option>
            {runs.map((n) => (
              <option key={n} value={n}>
                Run {n}
              </option>
            ))}
          </select>
        ) : null}
        <span className="flex-1" />
        <span data-component="BoardLive" className="text-xs text-[var(--color-muted)]">
          <span className="text-emerald-400">●</span> live
          {viewing.length > 0 ? ` · ${viewing.join(", ")} ${viewing.length === 1 ? "is" : "are"} viewing` : ""}
        </span>
        {!project.archived && !allStages ? (
          <button
            type="button"
            onClick={() => setCreating((v) => !v)}
            aria-expanded={creating}
            className="rounded-lg border border-[var(--color-border)] px-2.5 py-1 text-xs"
          >
            ＋ New card
          </button>
        ) : null}
        <button
          type="button"
          aria-pressed={allStages}
          onClick={() => setAllStages((v) => !v)}
          className="rounded-lg px-2.5 py-1 text-xs text-[var(--color-accent)]"
        >
          {allStages ? "Show 4 lanes" : "Show all 8 stages"}
        </button>
      </div>

      {allStages ? (
        <BoardPanel slug={slug} board={liveBoard} archived={project.archived} />
      ) : (
        <>
          {creating ? (
            <NewCardForm slug={slug} update={update} onDone={() => setCreating(false)} />
          ) : null}
          <NeedsYouStrip
            slug={slug}
            items={strip}
            canAct={canAct}
            profiles={profiles}
            memberIds={memberIds}
            cardRuns={cardRuns}
            now={now}
            update={update}
            onHold={onHold}
            onRelease={onRelease}
          />
          <div data-component="BoardLanes" className="grid gap-3 md:grid-cols-4">
            {LANES.map((lane) => {
              const cards = lanes[lane.key];
              const { shown, more } =
                lane.key === "done" ? splitDone(cards, doneOpen) : { shown: cards, more: 0 };
              return (
                <Lane
                  key={lane.key}
                  laneKey={lane.key}
                  label={lane.label}
                  hint={lane.hint}
                  count={cards.length}
                  empty={laneEmpty(lane.key, { filtered: isFiltered, openRun })}
                  slug={slug}
                >
                  {shown.length > 0 ? (
                    <ul className="flex flex-col gap-1.5">
                      {shown.map((task) => (
                        <CardTile
                          key={task.id}
                          slug={slug}
                          task={task}
                          runNo={cardRunNo(task, cardRuns)}
                          memberIds={memberIds}
                          now={now}
                        />
                      ))}
                    </ul>
                  ) : null}
                  {more > 0 ? (
                    <button
                      type="button"
                      onClick={() => setDoneOpen(true)}
                      className="px-1 py-1 text-xs text-[var(--color-accent)]"
                    >
                      +{more} more
                    </button>
                  ) : lane.key === "done" && doneOpen && cards.length > 3 ? (
                    <button
                      type="button"
                      onClick={() => setDoneOpen(false)}
                      className="px-1 py-1 text-xs text-[var(--color-muted)]"
                    >
                      Show fewer
                    </button>
                  ) : null}
                </Lane>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

function Lane({
  laneKey,
  label,
  hint,
  count,
  empty,
  slug,
  children,
}: {
  laneKey: LaneKey;
  label: string;
  hint: string;
  count: number;
  empty: ReturnType<typeof laneEmpty>;
  slug: string;
  children: ReactNode;
}) {
  return (
    <div
      data-component="BoardLane"
      data-lane={laneKey}
      className="rounded-2xl bg-[var(--color-surface-2)] p-2"
    >
      <div className="flex items-center gap-1.5 px-1 pb-2">
        <h3 className="text-xs font-bold uppercase tracking-wide text-[var(--color-muted)]">
          {label}
        </h3>
        <span className="rounded-full bg-[var(--color-surface)] px-1.5 text-[11px] text-[var(--color-muted)]">
          {count}
        </span>
      </div>
      {count === 0 ? (
        <p data-component="LaneEmpty" className="px-1 py-2 text-xs text-[var(--color-muted)]">
          {empty.text}
          {empty.link ? (
            <>
              {" — "}
              <Link
                href={`/projects/${encodeURIComponent(slug)}/runs/${empty.link.runNo}`}
                className="text-[var(--color-accent)] underline"
              >
                {empty.link.label}
              </Link>
              .
            </>
          ) : null}
        </p>
      ) : (
        children
      )}
      {laneKey === "up_next" && count > 0 ? (
        <p className="px-1 pt-1.5 text-[11px] text-[var(--color-muted)]">{hint}</p>
      ) : null}
    </div>
  );
}
