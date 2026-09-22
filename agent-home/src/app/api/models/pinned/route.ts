/**
 * GET /api/models/pinned — cards whose `model_override` pins them to a
 * model other than whatever the slots say. No upstream endpoint exposes a
 * cross-project task query, so this fans out: projects list → each
 * project's board → filter to non-terminal cards with an override.
 * Loaded lazily by the Models page (post-paint) so the N+1 fan-out never
 * gates the page itself.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromUrl } from "@/lib/chat/profile";
import type { PinnedModelCard } from "@/types";

/** Terminal statuses — an override on a finished card can't bite anyone. */
const LIVE_STATUSES = new Set(["triage", "todo", "scheduled", "ready", "running", "blocked", "review"]);

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const { items } = await client.projects({ limit: 100 });
    const boards = await Promise.all(
      items
        .filter((p) => !p.archived)
        .map((p) =>
          client
            .projectBoard(p.slug)
            .then((board) => ({ project: p, board }))
            // One unreadable board must not blank the whole section.
            .catch(() => null),
        ),
    );
    const pinned: PinnedModelCard[] = [];
    for (const entry of boards) {
      if (!entry) continue;
      for (const col of entry.board.columns) {
        for (const task of col.tasks) {
          if (task.model_override && LIVE_STATUSES.has(task.status)) {
            pinned.push({
              project_slug: entry.project.slug,
              project_name: entry.project.name,
              task_id: task.id,
              title: task.title,
              model: task.model_override,
              status: task.status,
            });
          }
        }
      }
    }
    return NextResponse.json({ pinned });
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
