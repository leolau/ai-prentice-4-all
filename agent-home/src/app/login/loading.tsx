import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/**
 * Route-level loading UI for **Sign in**.
 *
 * Present so the sign-in route doesn't fall through to the root skeleton, which
 * renders the navigation — a signed-out visitor should never see the app nav
 * flash before the login form.
 */
export default function Loading() {
  return (
    <MobileShell title="Sign in" showNav={false}>
      <PageSkeleton rows={1} label="Loading sign in…" />
    </MobileShell>
  );
}
