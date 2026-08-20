// @vitest-environment jsdom
/**
 * Handler-level tests for the create form (U4): submit → redirect, a 422's
 * `missing` list mapped onto the field that is blank, and the typed input
 * surviving the refusal — the inputs are state, so a 422 costs nothing.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

import { NewProjectForm } from "@/components/projects/NewProjectForm";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  router.push.mockClear();
  router.refresh.mockClear();
});

function fillStep1(getByPlaceholderText: (p: string) => HTMLElement) {
  fireEvent.change(getByPlaceholderText("Ship the Monday digest to every subscriber"), {
    target: { value: "The team starts Monday already briefed" },
  });
  fireEvent.change(
    getByPlaceholderText("A weekly digest compiled from arrivals and emailed each Monday…"),
    { target: { value: "A weekly digest compiled each Monday." } },
  );
  fireEvent.change(getByPlaceholderText("The Monday digest email"), {
    target: { value: "The Monday digest email" },
  });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("NewProjectForm handlers", () => {
  it("submits and redirects to the created project", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { slug: "monday-digest" }));
    vi.stubGlobal("fetch", fetchMock);

    const { getByPlaceholderText, getByText } = render(
      <NewProjectForm servingProfile="default" />,
    );
    fillStep1(getByPlaceholderText);
    fireEvent.click(getByText("Next"));
    fireEvent.click(getByText("Create project"));

    await waitFor(() =>
      expect(router.push).toHaveBeenCalledWith("/projects/monday-digest"),
    );
    const call = fetchMock.mock.calls[0];
    expect(call[0]).toBe("/api/projects");
    const body = JSON.parse((call[1] as RequestInit).body as string) as {
      goal: string;
      host_profile: string;
      outputs: { title: string }[];
    };
    expect(body.goal).toBe("The team starts Monday already briefed");
    expect(body.host_profile).toBe("default");
    expect(body.outputs).toEqual([{ title: "The Monday digest email" }]);
  });

  it("maps a 422's missing list onto the blank field and keeps what was typed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(422, {
        error: "invalid_request",
        detail: "A project needs its mandatory fields before it can start.",
        missing: ["goal"],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { getByPlaceholderText, getByText, findByText } = render(
      <NewProjectForm servingProfile="default" />,
    );
    fillStep1(getByPlaceholderText);
    fireEvent.click(getByText("Next"));
    fireEvent.click(getByText("Create project"));

    // The refusal names the field — never a bare toast…
    await findByText("This field is mandatory.");
    expect(
      getByText("A project needs its mandatory fields before it can start."),
    ).toBeTruthy();
    // …sends the writer back to step 1…
    expect(getByText(/Step 1 of 2/)).toBeTruthy();
    // …and what was typed survives the refusal.
    expect(
      (getByPlaceholderText("Ship the Monday digest to every subscriber") as HTMLInputElement)
        .value,
    ).toBe("The team starts Monday already briefed");
  });
});
