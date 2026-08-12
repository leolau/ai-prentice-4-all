import { describe, expect, it } from "vitest";

import { ForwardError } from "@/components/users/api";
import { optimisticRoleChange, withRole } from "@/components/users/optimistic";
import type { Member, MembersResponse } from "@/types";

const MEMBER: Member = {
  user_id: "a1b2c3",
  display: "Mia",
  role: "member",
  email: "mia@x.io",
  active: true,
  enrolled: true,
  channels: [],
  is_owner: false,
  invitation: null,
};

const PAGE: MembersResponse = {
  configured: true,
  members: [MEMBER],
  total: 1,
  limit: 25,
  offset: 0,
  profile: "acme",
};

describe("withRole", () => {
  it("rewrites only the targeted member and leaves the page frozen", () => {
    const next = withRole(PAGE, "a1b2c3", "admin");
    expect(next.members[0].role).toBe("admin");
    // The snapshot handed to the rollback must not have been mutated.
    expect(PAGE.members[0].role).toBe("member");
  });
});

describe("optimisticRoleChange", () => {
  it("shows the new role immediately and keeps it when the server accepts", async () => {
    const seen: MembersResponse[] = [];
    const failure = await optimisticRoleChange({
      page: PAGE,
      userId: "a1b2c3",
      role: "admin",
      send: async () => ({ ok: true }),
      setPage: (p) => seen.push(p),
    });
    expect(failure).toBeNull();
    expect(seen).toHaveLength(1);
    expect(seen[0].members[0].role).toBe("admin");
  });

  it("restores the previous page and surfaces the detail on a 403", async () => {
    const seen: MembersResponse[] = [];
    const failure = await optimisticRoleChange({
      page: PAGE,
      userId: "a1b2c3",
      role: "admin",
      send: async () => {
        throw new ForwardError(403, "Only an owner or admin may manage members.");
      },
      setPage: (p) => seen.push(p),
    });
    expect(failure).toBe("Only an owner or admin may manage members.");
    // Optimistic apply, then rollback to exactly the pre-click page.
    expect(seen).toHaveLength(2);
    expect(seen[0].members[0].role).toBe("admin");
    expect(seen[1]).toBe(PAGE);
  });

  it("rolls back the last-admin guard refusal too", async () => {
    const seen: MembersResponse[] = [];
    const failure = await optimisticRoleChange({
      page: PAGE,
      userId: "a1b2c3",
      role: "viewer",
      send: async () => {
        throw new ForwardError(409, "This is the last admin in this profile.");
      },
      setPage: (p) => seen.push(p),
    });
    expect(failure).toContain("last admin");
    expect(seen[1].members[0].role).toBe("member");
  });
});
