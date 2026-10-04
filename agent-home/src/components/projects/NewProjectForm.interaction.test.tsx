// @vitest-environment jsdom
/**
 * Handler-level tests for the four-step create form: step gating, Back
 * keeping state, the explicit skip, and the create sequence — the project
 * is created once, each file uploaded once (a second drop is ignored), a
 * failed upload retried with the same key without re-creating anything,
 * memory and links attached after create, and a 422's `missing` list mapped
 * onto the blank field with what was typed surviving the refusal.
 */
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

import { NewProjectForm } from "@/components/projects/NewProjectForm";
import type { UploadFn, UploadRequest } from "@/components/projects/inputs/uploadProjectFile";
import type { MemoryRow } from "@/types";

type Utils = ReturnType<typeof render>;

const GOAL = "The team starts Monday already briefed";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function memory(id: string, text: string): MemoryRow {
  return {
    id,
    owner_user_id: "leo",
    visibility: "private",
    kind: "fact",
    topic: null,
    text,
    truncated: false,
    created_at: null,
    uses: 0,
    last_used: null,
    elevated: false,
    provenance: "user",
    score: 0.9,
  };
}

/** A fetch that answers by URL; records every call. */
function routedFetch(
  opts: {
    create?: () => Response;
    memories?: MemoryRow[];
    link?: () => Response;
  } = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/projects") {
      return opts.create?.() ?? jsonResponse(200, { slug: "monday-digest" });
    }
    if (url.startsWith("/api/memory/rows")) {
      return jsonResponse(200, { rows: opts.memories ?? [], total: 0, limit: 6, offset: 0 });
    }
    if (url.endsWith("/links")) {
      const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      return opts.link?.() ?? jsonResponse(200, { ...body, profile: "default" });
    }
    return jsonResponse(200, {});
  });
}

function calls(fetchMock: ReturnType<typeof routedFetch>, match: (url: string) => boolean) {
  return fetchMock.mock.calls.filter(([url]) => match(String(url)));
}

function okUpload(): ReturnType<typeof vi.fn<UploadFn>> {
  return vi.fn<UploadFn>(async (req: UploadRequest) => ({
    ok: true,
    status: 200,
    data: { kind: req.kind, ref: `leo/monday-digest/${req.file.name}`, profile: "default", label: req.file.name },
    error: null,
  }));
}

function fillStep1({ getByPlaceholderText }: Utils) {
  fireEvent.change(getByPlaceholderText("Ship the Monday digest to every subscriber"), {
    target: { value: GOAL },
  });
  fireEvent.change(
    getByPlaceholderText("A weekly digest compiled from arrivals and emailed each Monday…"),
    { target: { value: "A weekly digest compiled each Monday." } },
  );
  fireEvent.change(getByPlaceholderText("The Monday digest email"), {
    target: { value: "The Monday digest email" },
  });
}

function file(name: string, size = 10): File {
  return new File(["x".repeat(size)], name, { type: "application/pdf", lastModified: 1 });
}

function pickFiles(utils: Utils, files: File[]) {
  fireEvent.change(utils.getByTestId("file-input"), { target: { files } });
}

/** Fill step 1 and skip inputs: lands on step 3. */
function toStep3(utils: Utils) {
  fillStep1(utils);
  fireEvent.click(utils.getByText("Next: inputs"));
  fireEvent.click(utils.getByText("I have no inputs — skip"));
}

beforeEach(() => {
  router.push.mockClear();
  router.refresh.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("NewProjectForm — steps", () => {
  it("step 1 refuses to advance with blank mandatory fields", () => {
    vi.stubGlobal("fetch", routedFetch());
    const utils = render(<NewProjectForm servingProfile="default" />);
    fireEvent.click(utils.getByText("Next: inputs"));
    expect(utils.getByText("A project needs a goal sentence.")).toBeTruthy();
    expect(utils.getByText(/Step 1 of 4/)).toBeTruthy();
    expect(utils.queryByText("Do you have anything the agent should use?")).toBeNull();
  });

  it("step 2 must be answered: Next is not offered until something is added or skipped", () => {
    vi.stubGlobal("fetch", routedFetch());
    const utils = render(<NewProjectForm servingProfile="default" />);
    fillStep1(utils);
    fireEvent.click(utils.getByText("Next: inputs"));

    expect(utils.getByText(/Step 2 of 4/)).toBeTruthy();
    expect(utils.getByText("Do you have anything the agent should use?")).toBeTruthy();
    const gated = utils.getByText("Add something or skip").closest("button")!;
    expect(gated.disabled).toBe(true);
    expect(utils.queryByText("Next: how it runs")).toBeNull();
    // Pressing Enter (form submit) does not slip past the gate either.
    fireEvent.submit(gated.closest("form")!);
    expect(utils.getByText(/Step 2 of 4/)).toBeTruthy();

    // Adding a note answers the step.
    fireEvent.change(utils.getByLabelText("Link or note"), {
      target: { value: "Use the 2025 pricing" },
    });
    fireEvent.click(utils.getByText("Add"));
    expect(utils.getByText("Use the 2025 pricing")).toBeTruthy();
    expect(utils.queryByText("I have no inputs — skip")).toBeNull();
    const next = utils.getByText("Next: how it runs").closest("button")!;
    expect(next.disabled).toBe(false);
    fireEvent.click(next);
    expect(utils.getByText(/Step 3 of 4/)).toBeTruthy();
  });

  it("skip is explicit, and Back keeps everything that was entered", () => {
    vi.stubGlobal("fetch", routedFetch());
    const utils = render(<NewProjectForm servingProfile="default" />);
    toStep3(utils);
    expect(utils.getByText(/Step 3 of 4/)).toBeTruthy();
    fireEvent.change(utils.getByDisplayValue("Supervised — runs, then reports"), {
      target: { value: "manual" },
    });
    fireEvent.click(utils.getByText("Next: review"));
    expect(utils.getByText(/Step 4 of 4/)).toBeTruthy();
    expect(utils.getByText(/No inputs/)).toBeTruthy();

    fireEvent.click(utils.getByText("Back"));
    expect((utils.getByDisplayValue("Manual — never runs itself") as HTMLSelectElement).value).toBe(
      "manual",
    );
    fireEvent.click(utils.getByText("Back"));
    // Skipped once: step 2 now offers plain Next.
    expect(utils.getByText("Next: how it runs").closest("button")!.disabled).toBe(false);
    fireEvent.click(utils.getByText("Back"));
    expect(
      (utils.getByPlaceholderText("Ship the Monday digest to every subscriber") as HTMLInputElement)
        .value,
    ).toBe(GOAL);
  });

  it("Back from step 3 keeps the files, roles and notes picked in step 2", () => {
    vi.stubGlobal("fetch", routedFetch());
    const utils = render(<NewProjectForm servingProfile="default" upload={okUpload()} />);
    fillStep1(utils);
    fireEvent.click(utils.getByText("Next: inputs"));
    pickFiles(utils, [file("MOU_template.docx")]);
    fireEvent.change(utils.getByLabelText("Use MOU_template.docx as"), {
      target: { value: "template" },
    });
    fireEvent.change(utils.getByLabelText("Link or note"), {
      target: { value: "drive.google.com/agreements" },
    });
    fireEvent.click(utils.getByText("Add"));
    fireEvent.click(utils.getByText("Next: how it runs"));
    fireEvent.click(utils.getByText("Back"));

    expect(utils.getByText("MOU_template.docx")).toBeTruthy();
    expect((utils.getByLabelText("Use MOU_template.docx as") as HTMLSelectElement).value).toBe(
      "template",
    );
    expect(utils.getByText("drive.google.com/agreements")).toBeTruthy();
  });
});

describe("NewProjectForm — create sequence", () => {
  it("submits and redirects to the created project (inputs skipped)", async () => {
    const fetchMock = routedFetch();
    vi.stubGlobal("fetch", fetchMock);
    const utils = render(<NewProjectForm servingProfile="default" />);
    toStep3(utils);
    fireEvent.click(utils.getByText("Next: review"));
    fireEvent.click(utils.getByText("I’ll write it myself"));
    fireEvent.click(utils.getByText("Create project"));

    await waitFor(() =>
      expect(router.push).toHaveBeenCalledWith("/projects/monday-digest#panel-plan"),
    );
    const creates = calls(fetchMock, (u) => u === "/api/projects");
    expect(creates).toHaveLength(1);
    const init = creates[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
    const body = JSON.parse(init.body as string) as {
      goal: string;
      host_profile: string;
      outputs: { title: string }[];
    };
    expect(body.goal).toBe(GOAL);
    expect(body.host_profile).toBe("default");
    expect(body.outputs).toEqual([{ title: "The Monday digest email" }]);
    // The user wrote the plan: no draft.
    expect(calls(fetchMock, (u) => u.endsWith("/playbook/draft"))).toHaveLength(0);
  });

  it("creates, starts the agent draft server-side and lands on the project page (default choice)", async () => {
    const fetchMock = routedFetch();
    vi.stubGlobal("fetch", fetchMock);
    const utils = render(<NewProjectForm servingProfile="default" />);
    toStep3(utils);
    // Each cadence/autonomy choice explains itself in user terms.
    expect(utils.getByText(/You start each run yourself/)).toBeTruthy();
    fireEvent.change(utils.getByDisplayValue("Supervised — runs, then reports"), {
      target: { value: "supervised" },
    });
    expect(utils.getByText(/pauses at checkpoints/)).toBeTruthy();
    fireEvent.click(utils.getByText("Next: review"));
    expect(utils.getByText(/Nothing is created until/)).toBeTruthy();
    fireEvent.click(utils.getByText("Create project and draft the plan"));

    await waitFor(() => expect(router.push).toHaveBeenCalled());
    const draft = calls(fetchMock, (u) => u === "/api/projects/monday-digest/playbook/draft");
    expect(draft).toHaveLength(1);
    expect((draft[0][1] as RequestInit).method).toBe("POST");
    expect(router.push).toHaveBeenCalledWith("/projects/monday-digest#panel-plan");
  });

  it("creates once, uploads each file once with its role's link kind, then links memory and notes", async () => {
    const fetchMock = routedFetch({ memories: [memory("mem_1", "ConnectAR Ltd company details")] });
    vi.stubGlobal("fetch", fetchMock);
    const upload = okUpload();
    const utils = render(<NewProjectForm servingProfile="default" upload={upload} />);
    fillStep1(utils);
    fireEvent.click(utils.getByText("Next: inputs"));

    const template = file("MOU_template.docx", 20);
    const terms = file("Licensing_terms.pdf", 30);
    pickFiles(utils, [template, terms]);
    // The same files dropped again are ignored — no duplicate rows.
    fireEvent.drop(utils.container.querySelector('[data-component="FileDropZone"]')!, {
      dataTransfer: { files: [template, terms] },
    });
    expect(utils.container.querySelectorAll('[data-component="FileQueueList"] li')).toHaveLength(2);
    fireEvent.change(utils.getByLabelText("Use MOU_template.docx as"), {
      target: { value: "template" },
    });

    // The goal seeds a memory suggestion; tick it.
    const box = await utils.findByLabelText("ConnectAR Ltd company details");
    expect(utils.getByText(/Suggested from your goal/)).toBeTruthy();
    fireEvent.click(box);

    fireEvent.change(utils.getByLabelText("Link or note"), {
      target: { value: "Keep it under one page" },
    });
    fireEvent.click(utils.getByText("Add"));

    fireEvent.click(utils.getByText("Next: how it runs"));
    fireEvent.click(utils.getByText("Next: review"));
    expect(utils.getByText("2 files · 1 memory · 1 link or note")).toBeTruthy();

    const create = utils.getByText("Create project and draft the plan").closest("button")!;
    fireEvent.click(create);
    fireEvent.click(create);
    await waitFor(() => expect(router.push).toHaveBeenCalledTimes(1));

    expect(calls(fetchMock, (u) => u === "/api/projects")).toHaveLength(1);
    expect(upload).toHaveBeenCalledTimes(2);
    const byName = Object.fromEntries(
      upload.mock.calls.map(([req]) => [req.file.name, req]),
    );
    expect(byName["MOU_template.docx"].kind).toBe("sample");
    expect(byName["Licensing_terms.pdf"].kind).toBe("reference");
    expect(byName["MOU_template.docx"].slug).toBe("monday-digest");

    const linkBodies = calls(fetchMock, (u) => u === "/api/projects/monday-digest/links").map(
      ([, init]) => JSON.parse(String((init as RequestInit).body)),
    );
    expect(linkBodies).toEqual(
      expect.arrayContaining([
        { kind: "memory", ref: "mem_1", label: "ConnectAR Ltd company details" },
        { kind: "reference", ref: "note:Keep it under one page", label: "Keep it under one page" },
      ]),
    );
    expect(linkBodies).toHaveLength(2);
    // The draft starts only after inputs are attached.
    const order = fetchMock.mock.calls.map(([u]) => String(u));
    expect(order.indexOf("/api/projects/monday-digest/playbook/draft")).toBeGreaterThan(
      order.lastIndexOf("/api/projects/monday-digest/links"),
    );
  });

  it("a failed upload is shown and retried with the same key — never re-creating or re-uploading the rest", async () => {
    const fetchMock = routedFetch();
    vi.stubGlobal("fetch", fetchMock);
    let failNext = true;
    const upload = vi.fn<UploadFn>(async (req) => {
      if (req.file.name === "broken.pdf" && failNext) {
        failNext = false;
        return { ok: false, status: 502, data: null, error: "Storage is down." };
      }
      return {
        ok: true,
        status: 200,
        data: { kind: req.kind, ref: `p/${req.file.name}`, profile: "default", label: req.file.name },
        error: null,
      };
    });
    const utils = render(<NewProjectForm servingProfile="default" upload={upload} />);
    fillStep1(utils);
    fireEvent.click(utils.getByText("Next: inputs"));
    pickFiles(utils, [file("good.pdf"), file("broken.pdf")]);
    fireEvent.click(utils.getByText("Next: how it runs"));
    fireEvent.click(utils.getByText("Next: review"));
    fireEvent.click(utils.getByText("Create project and draft the plan"));

    await utils.findByText(/some inputs didn’t attach/);
    expect(utils.getByText("✗ Storage is down.")).toBeTruthy();
    expect(utils.getByText("✓ Attached")).toBeTruthy();
    expect(router.push).not.toHaveBeenCalled();
    // The project exists now: no Back, no plan choice to change.
    expect(utils.queryByText("Back")).toBeNull();

    const firstKey = upload.mock.calls.find(([r]) => r.file.name === "broken.pdf")![0].idempotencyKey;
    fireEvent.click(utils.getByText("Retry failed inputs"));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/projects/monday-digest#panel-plan"));

    expect(calls(fetchMock, (u) => u === "/api/projects")).toHaveLength(1);
    const goodCalls = upload.mock.calls.filter(([r]) => r.file.name === "good.pdf");
    const brokenCalls = upload.mock.calls.filter(([r]) => r.file.name === "broken.pdf");
    expect(goodCalls).toHaveLength(1);
    expect(brokenCalls).toHaveLength(2);
    expect(brokenCalls[1][0].idempotencyKey).toBe(firstKey);
  });

  it("a failed upload can be retried from its own row, and the project page is reachable without it", async () => {
    const fetchMock = routedFetch();
    vi.stubGlobal("fetch", fetchMock);
    const upload = vi.fn<UploadFn>(async () => ({
      ok: false,
      status: 502,
      data: null,
      error: "Storage is down.",
    }));
    const utils = render(
      <NewProjectForm servingProfile="default" upload={upload} />,
    );
    fillStep1(utils);
    fireEvent.click(utils.getByText("Next: inputs"));
    pickFiles(utils, [file("broken.pdf")]);
    fireEvent.click(utils.getByText("Next: how it runs"));
    fireEvent.click(utils.getByText("Next: review"));
    fireEvent.click(utils.getByText("I’ll write it myself"));
    fireEvent.click(utils.getByText("Create project"));
    await utils.findByText(/some inputs didn’t attach/);

    fireEvent.click(utils.getByText("Retry"));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(calls(fetchMock, (u) => u === "/api/projects")).toHaveLength(1);

    fireEvent.click(utils.getByText("Go to the project without them"));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/projects/monday-digest#panel-plan"));
  });

  it("maps a 422's missing list onto the blank field and keeps what was typed", async () => {
    const fetchMock = routedFetch({
      create: () =>
        jsonResponse(422, {
          error: "invalid_request",
          detail: "A project needs its mandatory fields before it can start.",
          missing: ["goal"],
        }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const upload = okUpload();
    const utils = render(<NewProjectForm servingProfile="default" upload={upload} />);
    toStep3(utils);
    fireEvent.click(utils.getByText("Next: review"));
    fireEvent.click(utils.getByText("Create project and draft the plan"));

    // The refusal names the field — never a bare toast…
    await utils.findByText("This field is mandatory.");
    expect(
      utils.getByText("A project needs its mandatory fields before it can start."),
    ).toBeTruthy();
    // …sends the writer back to step 1…
    expect(utils.getByText(/Step 1 of 4/)).toBeTruthy();
    // …and what was typed survives the refusal; nothing was uploaded.
    expect(
      (utils.getByPlaceholderText("Ship the Monday digest to every subscriber") as HTMLInputElement)
        .value,
    ).toBe(GOAL);
    expect(upload).not.toHaveBeenCalled();
  });

  it("an unreachable server says so and keeps the form", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/projects") throw new TypeError("offline");
      return jsonResponse(200, { rows: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const utils = render(<NewProjectForm servingProfile="default" />);
    toStep3(utils);
    fireEvent.click(utils.getByText("Next: review"));
    await act(async () => {
      fireEvent.click(utils.getByText("Create project and draft the plan"));
    });
    expect(await utils.findByText("Could not reach the server.")).toBeTruthy();
    expect(utils.getByText(/Step 4 of 4/)).toBeTruthy();
    expect(router.push).not.toHaveBeenCalled();
  });
});
