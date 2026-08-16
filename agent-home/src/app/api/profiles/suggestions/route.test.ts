/**
 * BFF route tests for the FG-30 suggestion queue (§4.2 T1).
 *
 * The defining property here is the #253 defect, re-asserted in a new layer:
 * adopt/dismiss must run as the **requesting** principal, not silently as the
 * owner. The BFF does not re-derive authority — it forwards under the bridged
 * token, and the Python layer's 403 is the real gate. So a member's adopt is
 * the upstream's 403 passed through, never a 200 from running as the owner.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/profiles/suggestions/route";
import { POST as adoptPOST } from "@/app/api/profiles/suggestions/[suggestionId]/adopt/route";
import { POST as dismissPOST } from "@/app/api/profiles/suggestions/[suggestionId]/dismiss/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const profileSuggestions = vi.fn();
const adoptProfileSuggestion = vi.fn();
const dismissProfileSuggestion = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({
    profileSuggestions,
    adoptProfileSuggestion,
    dismissProfileSuggestion,
  }),
}));

const OWNER: Principal = {
  user_id: "leo",
  display: "Leo",
  role: "owner",
  channels: [],
  is_owner: true,
};
const MEMBER: Principal = {
  user_id: "mia",
  display: "Mia",
  role: "member",
  channels: [],
  is_owner: false,
};

function ctx(id: string) {
  return { params: Promise.resolve({ suggestionId: id }) };
}

describe("GET /api/profiles/suggestions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await GET();
    expect(res.status).toBe(401);
  });

  it("forwards to client.profileSuggestions under the bridged principal", async () => {
    getPrincipal.mockResolvedValue(MEMBER);
    profileSuggestions.mockResolvedValueOnce({
      suggestions: [
        {
          id: "s1",
          proposed_name: "finance",
          proposed_role: "CFO",
          proposed_goal: "improve cashflow",
          parent_goal_id: null,
          rationale: "invoicing clusters apart",
          evidence: { top_skills: [{ name: "invoice", uses: 9 }] },
          dedup_key: "k",
          origin_profile: "default",
          status: "proposed",
          reviewed_by: null,
          reviewed_at: null,
          created_at: null,
        },
      ],
    });
    const res = await GET();
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.suggestions).toHaveLength(1);
    expect(data.suggestions[0].proposed_role).toBe("CFO");
    expect(profileSuggestions).toHaveBeenCalledOnce();
  });

  it("passes an upstream 403 through (does not substitute the owner)", async () => {
    getPrincipal.mockResolvedValue(MEMBER);
    profileSuggestions.mockRejectedValueOnce(new HermesApiError(403, "no"));
    const res = await GET();
    expect(res.status).toBe(403);
  });

  it("returns 502 when the AI layer is unreachable", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    profileSuggestions.mockRejectedValueOnce(new Error("down"));
    const res = await GET();
    expect(res.status).toBe(502);
  });
});

describe("POST /api/profiles/suggestions/{id}/adopt", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await adoptPOST(new Request("http://x", { method: "POST" }), ctx("s1"));
    expect(res.status).toBe(401);
  });

  it("passes a member's adopt through as the upstream 403, not a 200 as the owner", async () => {
    getPrincipal.mockResolvedValue(MEMBER);
    adoptProfileSuggestion.mockRejectedValueOnce(
      new HermesApiError(403, "only the owner may adopt"),
    );
    const res = await adoptPOST(new Request("http://x", { method: "POST" }), ctx("s1"));
    expect(res.status).toBe(403);
    expect(adoptProfileSuggestion).toHaveBeenCalledWith("s1");
  });

  it("forwards an owner adopt and returns the new profile's path and goal", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    adoptProfileSuggestion.mockResolvedValueOnce({
      ok: true,
      name: "finance",
      path: "/home/.hermes/profiles/finance",
      goal: "improve cashflow",
    });
    const res = await adoptPOST(new Request("http://x", { method: "POST" }), ctx("s1"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.name).toBe("finance");
    expect(data.goal).toBe("improve cashflow");
  });
});

describe("POST /api/profiles/suggestions/{id}/dismiss", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await dismissPOST(
      new Request("http://x", { method: "POST" }),
      ctx("s1"),
    );
    expect(res.status).toBe(401);
  });

  it("passes a member's dismiss through as the upstream 403", async () => {
    getPrincipal.mockResolvedValue(MEMBER);
    dismissProfileSuggestion.mockRejectedValueOnce(
      new HermesApiError(403, "only the owner may dismiss"),
    );
    const res = await dismissPOST(
      new Request("http://x", { method: "POST" }),
      ctx("s1"),
    );
    expect(res.status).toBe(403);
  });

  it("forwards the optional reason to the audit trail", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    dismissProfileSuggestion.mockResolvedValueOnce({ ok: true, name: "finance" });
    const res = await dismissPOST(
      new Request("http://x", {
        method: "POST",
        body: JSON.stringify({ reason: "not a real sub-goal" }),
      }),
      ctx("s1"),
    );
    expect(res.status).toBe(200);
    expect(dismissProfileSuggestion).toHaveBeenCalledWith("s1", "not a real sub-goal");
  });

  it("forwards an empty-body dismiss too", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    dismissProfileSuggestion.mockResolvedValueOnce({ ok: true, name: "finance" });
    const res = await dismissPOST(new Request("http://x", { method: "POST" }), ctx("s1"));
    expect(res.status).toBe(200);
    expect(dismissProfileSuggestion).toHaveBeenCalledWith("s1", undefined);
  });
});