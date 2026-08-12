import { MobileShell } from "@/components/MobileShell";
import { PageSkeleton } from "@/components/ui/PageSkeleton";

/**
 * Route-level loading UI for **Users**. The page is `force-dynamic` and blocks
 * on the directory (plus the roster for an admin), so without this the browser
 * would sit on the previous screen with no sign anything was happening.
 */
export default function Loading() {
  return (
    <MobileShell title="Users">
      <PageSkeleton rows={4} />
    </MobileShell>
  );
}
