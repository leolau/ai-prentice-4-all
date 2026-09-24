import { describe, expect, it } from "vitest";

import {
  grantedScopes,
  serviceGranted,
} from "@/components/settings/ConnectedAccounts";
import type { CredentialEntry } from "@/types";

function entry(scopes: string[], services: string[] = []): CredentialEntry {
  return {
    owner_user_id: "leo_owner",
    provider: "google",
    name: "a@x.co",
    kind: "oauth_authorized_user",
    visibility: "private:leo_owner",
    services,
    payload: { scopes },
    created_at: null,
    updated_at: null,
  };
}

describe("grantedScopes", () => {
  it("returns the payload scopes list", () => {
    expect(grantedScopes(entry(["openid", "x"]))).toEqual(["openid", "x"]);
  });
  it("tolerates a missing/non-array scopes field", () => {
    const e = entry([]);
    e.payload = {};
    expect(grantedScopes(e)).toEqual([]);
  });
});

describe("serviceGranted", () => {
  const full = entry([
    "openid",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
  ]);

  it("is true when every required scope is granted", () => {
    expect(serviceGranted(full, "email")).toBe(true);
    expect(serviceGranted(full, "calendar")).toBe(true);
    expect(serviceGranted(full, "drive")).toBe(true);
  });

  it("is false when the flag is set but the grant lacks the scope", () => {
    // A restored legacy token: calendar+drive scopes, no mail.google.com.
    const legacy = entry([
      "https://www.googleapis.com/auth/calendar",
      "https://www.googleapis.com/auth/drive",
    ]);
    expect(serviceGranted(legacy, "email")).toBe(false);
    expect(serviceGranted(legacy, "calendar")).toBe(true);
  });

  it("requires the full workspace scope set", () => {
    expect(serviceGranted(full, "workspace")).toBe(false);
  });
});
