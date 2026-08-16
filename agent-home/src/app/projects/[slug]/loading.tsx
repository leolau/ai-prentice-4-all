import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/**
 * Route-level loading UI for the project detail page — the shell and a
 * skeleton paint on tap while the detail + fan-out reads resolve.
 */
export default function Loading() {
  return (
    <MobileShell title="Project">
      <PageSkeleton rows={6} />
    </MobileShell>
  );
}
