import { MobileShell } from "@/components/MobileShell";
import { TodosList } from "@/components/todos/TodosList";
import { DEFAULT_STAGES } from "@/components/todos/filters";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { TodosFacets, TodosResponse } from "@/types";

// Reads the live principal (cookie) + the caller's C2-scoped to-dos per
// request — never at build time.
export const dynamic = "force-dynamic";

const EMPTY_TODOS: TodosResponse = { items: [], next_cursor: null };
const EMPTY_FACETS: TodosFacets = {
  stages: [],
  priorities: [],
  source_kinds: [],
};

/**
 * **To-dos** — the staging layer between what arrived and what gets done.
 *
 * Triage puts most of what it notices here silently (`staged`) and only
 * promotes what clears the bar to `open`, where it is worth an interruption.
 * The page is where the user overrules either judgement.
 *
 * BFF: the server resolves the principal and loads the first C2-scoped page
 * from the same filters the URL carries, so a shared `/todos?stage=open` link
 * arrives already filtered rather than rendering everything for one frame.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await requirePrincipal();
  const params = await searchParams;
  const first = (key: string) => {
    const value = params[key];
    return (Array.isArray(value) ? value[0] : value) ?? undefined;
  };

  let todos: TodosResponse = EMPTY_TODOS;
  let facets: TodosFacets = EMPTY_FACETS;
  let error: string | null = null;
  try {
    const client = await apiClientForRequest();
    [todos, facets] = await Promise.all([
      client.todos({
        limit: 50,
        q: first("q"),
        stage: first("stage") ?? DEFAULT_STAGES.join(","),
        priority: first("priority"),
        source_ref: first("source_ref"),
        include_snoozed: first("include_snoozed") === "true",
      }),
      client.todosFacets(),
    ]);
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load your to-dos";
  }

  return (
    <MobileShell title="To-dos">
      {error ? (
        <div
          data-component="TodosPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load your to-dos ({error}).
        </div>
      ) : (
        <TodosList initial={todos} facets={facets} />
      )}
    </MobileShell>
  );
}
