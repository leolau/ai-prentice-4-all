import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/** Route-level loading UI for the run page (§7). */
export default function Loading() {
  return (
    <MobileShell title="Run">
      <PageSkeleton rows={5} />
    </MobileShell>
  );
}
