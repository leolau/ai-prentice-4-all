import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/** Route-level loading UI for **New project** — mirrors `/projects`. */
export default function Loading() {
  return (
    <MobileShell title="New project">
      <PageSkeleton rows={4} />
    </MobileShell>
  );
}
