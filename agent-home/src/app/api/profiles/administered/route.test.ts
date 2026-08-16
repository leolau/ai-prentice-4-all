/**
 * BFF route tests for `GET /api/profiles/administered` (FG-28 switcher feed).
 *
 * BFF behavioural contract: the route forwards the bridged C1 principal and
 * never re-derives authority — the Python layer's 401 (no owner-fallback on
 * console routes) and 403 are the real gates. The cross-profile iteration
 * lives upstream (`administered_profiles` + `probe_registry_health`); the
 * BFF only proxies, so the test holds the proxy's contract not the reg's.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/profiles/administered/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const administeredProfiles = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ administeredProfiles }),
}));

const OWNER: Principal = {
  user_id: "leo",
  display: "Leo",
  role: "owner",
  channels: [],
  is_owner: true,
};

describe("GET /api/profiles/administered", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when the caller has no verified session", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await GET();
    expect(res.status).toBe(401);
    expect(administeredProfiles).not.toHaveBeenCalled();
  });

  it("forwards to client.administeredProfiles under the bridged principal", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    administeredProfiles.mockResolvedValueOnce({
      profiles: [
        {
          name: "default",
          is_default: true,
          served: true,
          base_url: "",
          schema: "app_prod",
          health: "ok",
          health_detail: "",
        },
        {
          name: "engineers",
          is_default: false,
          served: true,
          base_url: "/p/engineers/",
          schema: "app_prod_engineers",
          health: "claimed-by-other",
          health_detail: "schema app_prod_engineers owned by hr",
        },
      ],
    });
    const res = await GET();
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.profiles).toHaveLength(2);
    expect(data.profiles[1].health).toBe("claimed-by-other");
    expect(administeredProfiles).toHaveBeenCalledOnce();
  });

  it("passes an upstream 401 through (does not substitute the owner)", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    administeredProfiles.mockRejectedValueOnce(
      new HermesApiError(401, "no subject"),
    );
    const res = await GET();
    expect(res.status).toBe(401);
  });

  it("passes an upstream 503 through (datastore not configured)", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    administeredProfiles.mockRejectedValueOnce(
      new HermesApiError(503, "user management not configured"),
    );
    const res = await GET();
    expect(res.status).toBe(503);
  });

  it("returns 502 when the AI layer is unreachable", async () => {
    getPrincipal.mockResolvedValue(OWNER);
    administeredProfiles.mockRejectedValueOnce(new Error("down"));
    const res = await GET();
    expect(res.status).toBe(502);
  });
});