/**
 * API client tests for the Projects methods (step 6).
 *
 * Same contract as the to-do tests: the client is a URL builder, so the path,
 * method and body *are* the behaviour. Every id/slug is opaque and goes
 * through `encodeURIComponent`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

/** A fresh 200 per call: a `Response` body can only be read once. */
function ok(body: unknown) {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
}

function client() {
  return new HermesApiClient({ baseUrl: "http://api.test" });
}

describe("HermesApiClient projects", () => {
  it("projects sends only the filters that were set", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(ok({ items: [], next_cursor: null }));

    await client().projects({ cadence: "repeatable", health: "stalled" });

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/registry/projects");
    expect(url.searchParams.get("cadence")).toBe("repeatable");
    expect(url.searchParams.get("health")).toBe("stalled");
    // An unset filter is absent, not empty.
    expect(url.searchParams.has("q")).toBe(false);
    expect(url.searchParams.has("archived")).toBe(false);
    // No filters at all means no querystring.
    await client().projects();
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      "http://api.test/api/registry/projects",
    );
  });

  it("doctor list and per-project doctor hit their own routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.projectsDoctor();
    await c.projectsDoctor("monday digest");
    await c.projectDoctor("monday digest");

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toBe("http://api.test/api/registry/projects/doctor");
    expect(urls[1]).toBe(
      "http://api.test/api/registry/projects/doctor?slug=monday%20digest",
    );
    expect(urls[2]).toBe(
      "http://api.test/api/registry/projects/monday%20digest/doctor",
    );
  });

  it("createProject POSTs the §2.2 contract body", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));

    await client().createProject({
      goal: "Land Q3 revenue",
      description: "Acme rollout",
      outputs: ["The onboarding doc"],
      host_profile: "default",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://api.test/api/registry/projects");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      goal: "Land Q3 revenue",
      description: "Acme rollout",
      outputs: ["The onboarding doc"],
      host_profile: "default",
    });
  });

  it("schedule PUT/DELETE wrap the host profile's cron job", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.setProjectSchedule("digest", "every 60m");
    await c.clearProjectSchedule("digest");

    const first = fetchMock.mock.calls[0];
    expect(String(first[0])).toBe(
      "http://api.test/api/registry/projects/digest/schedule",
    );
    expect(first[1]?.method).toBe("PUT");
    expect(JSON.parse(String(first[1]?.body))).toEqual({ schedule: "every 60m" });
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("run verbs address the run by number", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.projectRun("digest", 14);
    await c.continueProjectRun("digest", 14);
    await c.cancelProjectRun("digest", 14);
    await c.writeProjectRetro("digest", 14, "tone was off");

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toBe("http://api.test/api/registry/projects/digest/runs/14");
    expect(urls[1]).toBe(
      "http://api.test/api/registry/projects/digest/runs/14/continue",
    );
    expect(urls[2]).toBe(
      "http://api.test/api/registry/projects/digest/runs/14/cancel",
    );
    expect(urls[3]).toBe(
      "http://api.test/api/registry/projects/digest/runs/14/retro",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[3][1]?.body))).toEqual({
      retro: "tone was off",
    });
  });

  it("playbook save/activate and directive retire use the method routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.saveProjectPlaybook("digest", {
      body: "The Monday digest",
      steps: [{ key: "collect", title: "Collect" }],
    });
    await c.activateProjectPlaybook("digest", 2, "approved by leo");
    await c.retireProjectDirective("digest", "d1");

    const calls = fetchMock.mock.calls;
    expect(String(calls[0][0])).toBe(
      "http://api.test/api/registry/projects/digest/playbook",
    );
    expect(calls[0][1]?.method).toBe("POST");
    expect(String(calls[1][0])).toBe(
      "http://api.test/api/registry/projects/digest/playbook/2/activate",
    );
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      note: "approved by leo",
    });
    expect(String(calls[2][0])).toBe(
      "http://api.test/api/registry/projects/digest/directives/d1/retire",
    );
  });

  it("output accept + deliver and links hit the judgement routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.deliverProjectOutput("digest", "o1", { run_id: "r1" });
    await c.acceptProjectOutput("digest", "o1");
    await c.linkToProject("digest", { kind: "todo", ref: "t9" });
    await c.unlinkFromProject("digest", { kind: "todo", ref: "t9" });

    const calls = fetchMock.mock.calls;
    expect(String(calls[0][0])).toBe(
      "http://api.test/api/registry/projects/digest/outputs/o1/deliver",
    );
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ run_id: "r1" });
    expect(String(calls[1][0])).toBe(
      "http://api.test/api/registry/projects/digest/outputs/o1/accept",
    );
    expect(calls[1][1]?.method).toBe("POST");
    expect(String(calls[2][0])).toBe(
      "http://api.test/api/registry/projects/digest/links",
    );
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({
      kind: "todo",
      ref: "t9",
    });
    // Unlink sends the pointer in the DELETE body — the route has no path id.
    expect(calls[3][1]?.method).toBe("DELETE");
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual({
      kind: "todo",
      ref: "t9",
    });
  });

  it("autonomy, tools and card reads use their own surfaces", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(ok({}));
    const c = client();

    await c.setProjectAutonomy("digest", "autonomous");
    await c.setProjectTools("digest", { toolsets: ["web"] });
    await c.projectCard("digest", "task 1");
    await c.projectBoard("digest");

    const calls = fetchMock.mock.calls;
    expect(String(calls[0][0])).toBe(
      "http://api.test/api/registry/projects/digest/autonomy",
    );
    expect(calls[0][1]?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({
      autonomy: "autonomous",
    });
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      toolsets: ["web"],
    });
    // Card ids are opaque and go in the path encoded.
    expect(String(calls[2][0])).toBe(
      "http://api.test/api/registry/projects/digest/cards/task%201",
    );
    expect(String(calls[3][0])).toBe(
      "http://api.test/api/registry/projects/digest/board",
    );
  });
});
