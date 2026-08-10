import { notFound } from "next/navigation";

import { MobileShell } from "@/components/MobileShell";
import { IncomingDetailView } from "@/components/inbox/IncomingDetailView";
import { apiClientForRequest, requirePrincipal } from "@/lib/auth/principal";
import type { IncomingDetail } from "@/types";

// The arrival is C2-scoped, so it is read per request under the live principal.
export const dynamic = "force-dynamic";

/**
 * `/inbox/:id` — one arrival in full.
 *
 * An upstream 404 covers both "no such item" and "not yours"; this page
 * renders the same not-found either way rather than confirming that somebody
 * else's message exists.
 */
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  await requirePrincipal();
  const { id } = await params;

  // The fetch is kept outside the JSX so a render error cannot be mistaken
  // for a missing item and turned into a 404.
  let item: IncomingDetail;
  try {
    const client = await apiClientForRequest();
    item = await client.incoming(id);
  } catch {
    notFound();
  }

  return (
    <MobileShell title="Inbox">
      <IncomingDetailView item={item} />
    </MobileShell>
  );
}
