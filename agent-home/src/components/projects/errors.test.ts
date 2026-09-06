import { describe, expect, it } from "vitest";

import { friendlyError } from "@/components/projects/errors";
import { outputsLabel } from "@/components/projects/outputsLabel";

describe("friendlyError", () => {
  it("never leaks the schedule route's spec references", () => {
    const said = friendlyError({
      status: 409,
      detail: "a schedule needs an active playbook — save and activate a method first (§3.1)",
    });
    expect(said).not.toMatch(/§|playbook|method/);
    expect(said).toMatch(/Plan panel.*activate/i);
    expect(
      friendlyError({ status: 409, detail: "project needs a host profile before it can be scheduled" }),
    ).toMatch(/People/);
    expect(
      friendlyError({ status: 422, detail: "a project needs a recurring schedule, not a one-off time" }),
    ).toMatch(/recurring/i);
  });

  it("turns the registry's lifecycle refusals into what to do next", () => {
    expect(
      friendlyError({
        status: 409,
        detail: "project 'x' is 'paused': only an active project can run",
      }),
    ).toMatch(/paused.*resume/i);
    expect(friendlyError({ status: 409, detail: "project 'x' has no playbook" })).toContain(
      "Plan panel",
    );
    expect(
      friendlyError({ status: 409, detail: "project has no profiles — add one before running" }),
    ).toContain("People");
    expect(friendlyError({ status: 409, detail: "project 'x' is archived" })).toContain(
      "archived",
    );
  });

  it("explains run, card and human-gated refusals", () => {
    expect(friendlyError({ status: 409, detail: "run 14 is already cancelled" })).toBe(
      "Run 14 has already cancelled — refresh to see its current state.",
    );
    expect(friendlyError({ status: 409, detail: "run 14 is not waiting" })).toContain(
      "not waiting for you",
    );
    expect(
      friendlyError({ status: 409, detail: "only a ready or running card can be blocked" }),
    ).toContain("can be blocked");
    expect(friendlyError({ status: 403, detail: "accepting an output is a human act" })).toMatch(
      /signed-in person/i,
    );
  });

  it("names the field on 422s and keeps unknown details verbatim", () => {
    expect(friendlyError({ status: 422, detail: "title must not be empty" })).toBe(
      "Title is required.",
    );
    expect(friendlyError({ status: 422, detail: "body must be at most 4000 characters" })).toBe(
      "Keep it under 4000 characters.",
    );
    expect(friendlyError({ status: 422, detail: "something we have not seen" })).toBe(
      "something we have not seen",
    );
  });

  it("falls back on the status when there is no detail", () => {
    expect(friendlyError({ status: 403 })).toContain("permission");
    expect(friendlyError({ status: 404 })).toContain("no longer exists");
    expect(friendlyError({ status: 409 })).toContain("not in a state");
    expect(friendlyError({ status: 503 })).toContain("try again");
    expect(friendlyError({}, "Nope.")).toBe("Nope.");
  });
});

describe("outputsLabel", () => {
  it("says nothing without outputs", () => {
    expect(outputsLabel(undefined)).toBeNull();
    expect(
      outputsLabel({ total: 0, required: 0, delivered: 0, accepted: 0, awaiting_acceptance: 0 }),
    ).toBeNull();
  });

  it("leads with acceptance and names what is still owed", () => {
    expect(
      outputsLabel({ total: 3, required: 3, delivered: 2, accepted: 1, awaiting_acceptance: 1 }),
    ).toBe("1 of 3 outputs accepted · 1 to accept");
    expect(
      outputsLabel({ total: 3, required: 3, delivered: 1, accepted: 1, awaiting_acceptance: 0 }),
    ).toBe("1 of 3 outputs accepted · 2 not delivered yet");
  });
});
