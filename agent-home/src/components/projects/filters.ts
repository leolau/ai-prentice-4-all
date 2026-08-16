/**
 * The project list's filter vocabulary and its URL codec.
 *
 * Deliberately **not** a `"use client"` module: `/projects/page.tsx` renders
 * on the server and needs the real values. Exports of a client module reach a
 * server component as client-reference proxies, so reading `DEFAULT_VIEW`
 * there would fail at runtime in the production build only — the exact
 * incident the to-dos filters carry the scar of.
 */

/**
 * The seven chips from the design (§13): one narrows by status, cadence or
 * health, and "All" lifts every restriction including the archive. They are
 * the list's only narrowing controls, so each maps to exactly one query
 * parameter — a shared `/projects?cadence=standing` link is a shared filter.
 */
export type ProjectListView =
  | "active"
  | "repeatable"
  | "standing"
  | "attention"
  | "paused"
  | "done"
  | "all";

export const FILTER_CHIPS: { view: ProjectListView; label: string }[] = [
  { view: "active", label: "Active" },
  { view: "repeatable", label: "Repeatable" },
  { view: "standing", label: "Standing" },
  { view: "attention", label: "Attention" },
  { view: "paused", label: "Paused" },
  { view: "done", label: "Done" },
  { view: "all", label: "All" },
];

/** The list defaults to the live work (acceptance §16 Frontend). */
export const DEFAULT_VIEW: ProjectListView = "active";

export interface ProjectsFilterState {
  view: ProjectListView;
  q: string;
}

export const EMPTY_FILTERS: ProjectsFilterState = {
  view: DEFAULT_VIEW,
  q: "",
};

/** Read the filter state out of a shared `/projects?…` URL. */
export function filtersFromParams(
  params: URLSearchParams,
): ProjectsFilterState {
  let view: ProjectListView = DEFAULT_VIEW;
  if (params.get("archived") === "true") view = "all";
  else if (params.get("cadence") === "repeatable") view = "repeatable";
  else if (params.get("cadence") === "standing") view = "standing";
  else if (params.get("health") === "attention") view = "attention";
  else if (params.get("status") === "paused") view = "paused";
  else if (params.get("status") === "done") view = "done";
  // `status=active` and anything unrecognised both land on the default view.
  return { view, q: params.get("q") ?? "" };
}

/** The inverse, for the address bar and for the BFF call. */
export function filtersToParams(
  value: ProjectsFilterState,
  cursor?: string | null,
): URLSearchParams {
  const params = new URLSearchParams();
  switch (value.view) {
    case "repeatable":
      params.set("cadence", "repeatable");
      break;
    case "standing":
      params.set("cadence", "standing");
      break;
    case "attention":
      params.set("health", "attention");
      break;
    case "paused":
      params.set("status", "paused");
      break;
    case "done":
      params.set("status", "done");
      break;
    case "all":
      params.set("archived", "true");
      break;
    default:
      params.set("status", "active");
  }
  if (value.q) params.set("q", value.q);
  if (cursor) params.set("cursor", cursor);
  return params;
}
