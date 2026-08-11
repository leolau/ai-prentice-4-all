import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/**
 * Route-level loading UI for **To-dos**.
 *
 * The page is a `force-dynamic` server component, so navigating to it blocks on
 * the upstream reads. The shell and a skeleton paint on tap, and the real
 * content replaces them when it arrives.
 */
export default function Loading() {
  return (
    <MobileShell title="To-dos">
      <PageSkeleton rows={3} />
    </MobileShell>
  );
}
