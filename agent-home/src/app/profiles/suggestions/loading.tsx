import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

export default function Loading() {
  return (
    <MobileShell title="Profile suggestions">
      <PageSkeleton rows={3} />
    </MobileShell>
  );
}