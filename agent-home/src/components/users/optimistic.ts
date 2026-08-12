import { errorMessage } from "@/components/users/api";
import type { MembersResponse, Role } from "@/types";

/** The page with one member's role rewritten — the optimistic projection. */
export function withRole(
  page: MembersResponse,
  userId: string,
  role: Role,
): MembersResponse {
  return {
    ...page,
    members: page.members.map((m) => (m.user_id === userId ? { ...m, role } : m)),
  };
}

/**
 * Apply a role change optimistically and **undo it on refusal**.
 *
 * A `<select>` that snaps back after a round-trip reads as a broken control, so
 * the new role is shown immediately. But several refusals are expected here and
 * not exceptional — the last-admin guard, self-demotion, a 403 for a member who
 * lost admin between page load and click — and after any of them the row must
 * show the role the server still holds, not the one that was refused. Hence the
 * snapshot-restore rather than a re-fetch: a re-fetch is a second round-trip
 * that can also fail, leaving the lie on screen.
 *
 * Lives outside the component so this contract is testable without a DOM.
 */
export async function optimisticRoleChange({
  page,
  userId,
  role,
  send,
  setPage,
}: {
  page: MembersResponse;
  userId: string;
  role: Role;
  send: () => Promise<unknown>;
  setPage: (next: MembersResponse) => void;
}): Promise<string | null> {
  setPage(withRole(page, userId, role));
  try {
    await send();
    return null;
  } catch (err) {
    setPage(page);
    return errorMessage(err, "Could not change the role.");
  }
}
