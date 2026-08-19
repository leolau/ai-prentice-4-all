import { MobileShell } from "@/components/MobileShell";
import { NewProjectForm } from "@/components/projects/NewProjectForm";
import { requirePrincipal } from "@/lib/auth/principal";

// Reads the live principal per request — never at build time.
export const dynamic = "force-dynamic";

/**
 * **New project** — the two-step create form (§13).
 *
 * A server-rendered route, not a modal: the two mandatory Markdown-ish
 * fields need room, and a refused submit must be linkable and reloadable
 * without losing what was typed. The host profile is fixed to the profile
 * serving this request — the Projects page addresses one `HERMES_HOME`,
 * and a create form that names another would file the record where the
 * caller cannot see it.
 */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="New project">
      <NewProjectForm servingProfile="default" />
    </MobileShell>
  );
}
