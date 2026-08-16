import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/** Route-level loading UI for the card page (§13). */
export default function Loading() {
  return (
    <MobileShell title="Card">
      <PageSkeleton rows={4} />
    </MobileShell>
  );
}
