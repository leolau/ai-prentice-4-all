import { MobileShell } from "@/components/MobileShell";
import { ProjectDetailView } from "@/components/projects/ProjectDetailView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type {
  ProjectBoardView,
  ProjectDetail,
  ProjectDirectivesResponse,
  ProjectPlaybookResponse,
} from "@/types";

// Reads the live principal + the project record per request — never at build
// time.
export const dynamic = "force-dynamic";

/**
 * **The one place** (§13): everything a project knows, on one page.
 *
 * The detail read is the only mandatory fetch; the board, playbook and
 * directives are fan-out reads (§16) — each in its own try/catch so a dead
 * profile only blanks its own panel, never the page.
 */
export default async function Page({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  await requirePrincipal();
  const { slug } = await params;
  const client = await apiClientForRequest();

  // Fetch under the try, render outside it — JSX built inside a try/catch
  // never reaches the catch (the render happens later).
  let project: ProjectDetail | null = null;
  let detailError: string | null = null;
  try {
    project = await client.project(slug);
  } catch (err) {
    detailError = err instanceof Error ? err.message : "Failed to load";
  }

  if (!project) {
    return (
      <MobileShell title="Project">
        <div
          data-component="ProjectPageError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          Couldn&apos;t load this project ({detailError}).
        </div>
      </MobileShell>
    );
  }

  const [board, playbook, directives] = await Promise.all([
    client.projectBoard(slug).catch((): ProjectBoardView | null => null),
    client.projectPlaybook(slug).catch(
      (): ProjectPlaybookResponse | null => null,
    ),
    client.projectDirectives(slug).catch(
      (): ProjectDirectivesResponse | null => null,
    ),
  ]);

  return (
    <MobileShell title={project.name}>
      <ProjectDetailView
        project={project}
        board={board}
        playbook={playbook}
        directives={directives}
      />
    </MobileShell>
  );
}
