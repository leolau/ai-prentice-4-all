// @vitest-environment jsdom
/**
 * Handler-level tests for the model picker sheet: the provider dropdown
 * covers the whole catalog, unauthenticated key-based providers swap the
 * model list for an inline key field, saving a key refetches options so the
 * models appear, and a `confirm_required` response becomes an explicit
 * confirm that re-sends with `confirm_expensive_model`.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelPickerSheet } from "@/components/models/ModelPickerSheet";

const OPTIONS = {
  model: "glm-5.2",
  provider: "alibaba",
  providers: [
    {
      slug: "alibaba",
      name: "Alibaba",
      models: ["glm-5.2", "qwen3.8-max"],
      authenticated: true,
      auth_type: "api_key",
      key_env: "ALIBABA_API_KEY",
    },
    {
      slug: "opencode-go",
      name: "OpenCode Go",
      models: [],
      authenticated: false,
      auth_type: "api_key",
      key_env: "OPENCODE_GO_API_KEY",
    },
    {
      slug: "anthropic",
      name: "Anthropic",
      models: [],
      authenticated: false,
      auth_type: "oauth",
      key_env: null,
    },
  ],
};

const OPTIONS_CONNECTED = {
  ...OPTIONS,
  providers: OPTIONS.providers.map((p) =>
    p.slug === "opencode-go"
      ? { ...p, authenticated: true, models: ["kimi-for-coding", "glm-5"] }
      : p,
  ),
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderSheet(overrides: Record<string, unknown> = {}) {
  const props = {
    slot: { kind: "main" as const },
    currentMain: { provider: "alibaba", model: "glm-5.2" },
    profile: undefined,
    onClose: vi.fn(),
    onChanged: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  return { ...render(<ModelPickerSheet {...props} />), props };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelPickerSheet", () => {
  it("lists every catalog provider, marking unauthenticated ones", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, OPTIONS));
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, container } = renderSheet();
    await findByText("OpenCode Go — not connected");
    const options = Array.from(container.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual([
      "Alibaba",
      "OpenCode Go — not connected",
      "Anthropic — not connected",
    ]);
    // The authenticated provider's models render as selectable rows.
    expect((await findByText("qwen3.8-max")).textContent).toBe("qwen3.8-max");
  });

  it("offers an inline key field for an unconnected api_key provider", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, OPTIONS));
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, findByPlaceholderText, container } = renderSheet();
    await findByText("OpenCode Go — not connected");
    fireEvent.change(container.querySelector("select")!, {
      target: { value: "opencode-go" },
    });
    const input = await findByPlaceholderText("OPENCODE_GO_API_KEY");
    expect(input).toBeTruthy();
    expect(await findByText("Save key")).toBeTruthy();
    // No model list while unauthenticated.
    expect(container.querySelectorAll("option").length).toBe(3);
  });

  it("points OAuth providers at onboarding instead of a key field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, OPTIONS));
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, container } = renderSheet();
    await findByText("Anthropic — not connected");
    fireEvent.change(container.querySelector("select")!, {
      target: { value: "anthropic" },
    });
    expect(await findByText(/Getting started/)).toBeTruthy();
    expect(container.querySelector("input[type=password]")).toBeNull();
  });

  it("saves a key, refetches options and reveals the provider's models", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, OPTIONS)) // initial options
      .mockResolvedValueOnce(jsonResponse(200, { ok: true, verified: true })) // save key
      .mockResolvedValueOnce(jsonResponse(200, OPTIONS_CONNECTED)); // refetch
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, findByPlaceholderText, container } = renderSheet();
    await findByText("OpenCode Go — not connected");
    fireEvent.change(container.querySelector("select")!, {
      target: { value: "opencode-go" },
    });
    fireEvent.change(await findByPlaceholderText("OPENCODE_GO_API_KEY"), {
      target: { value: "sk-live-key" },
    });
    fireEvent.click(await findByText("Save key"));

    // Key went to the provider-key route with the env var name — and the
    // value only ever travels in the request body, never into the DOM.
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/models/provider-key",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const keyCall = JSON.parse(
      (fetchMock.mock.calls[1][1] as RequestInit).body as string,
    ) as { key: string; value: string };
    expect(keyCall).toEqual({ key: "OPENCODE_GO_API_KEY", value: "sk-live-key" });
    expect(container.innerHTML).not.toContain("sk-live-key");

    // The refetched catalog now shows the provider's models.
    expect(await findByText("kimi-for-coding")).toBeTruthy();
  });

  it("turns confirm_required into an explicit confirm that re-sends", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, OPTIONS)) // options
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ok: false,
          confirm_required: true,
          confirm_message: "qwen3.8-max may be expensive",
        }),
      ) // first set attempt
      .mockResolvedValueOnce(jsonResponse(200, { ok: true })); // confirmed resend
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, props } = renderSheet();
    fireEvent.click(await findByText("qwen3.8-max"));
    fireEvent.click(await findByText("Set model"));

    // The expensive-model warning becomes a confirm step…
    expect(await findByText(/may be expensive/)).toBeTruthy();
    fireEvent.click(await findByText("Use it anyway"));

    // …which re-sends with confirm_expensive_model and then closes via onChanged.
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    const resend = JSON.parse(
      (fetchMock.mock.calls[2][1] as RequestInit).body as string,
    ) as { confirm_expensive_model: boolean; provider: string; model: string };
    expect(resend.confirm_expensive_model).toBe(true);
    expect(resend.provider).toBe("alibaba");
    expect(resend.model).toBe("qwen3.8-max");
  });

  it("resets an auxiliary slot to auto through the same set route", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, OPTIONS))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const { findByText, props } = renderSheet({
      slot: { kind: "aux", task: "vision", label: "Vision", hint: "image analysis" },
      currentAssignment: {
        task: "vision",
        provider: "alibaba",
        model: "qwen3.8-max",
        base_url: "",
      },
    });
    fireEvent.click(await findByText("Reset to auto"));

    await waitFor(() => expect(props.onChanged).toHaveBeenCalled());
    const call = JSON.parse(
      (fetchMock.mock.calls[1][1] as RequestInit).body as string,
    ) as { scope: string; task: string; provider: string; model: string };
    expect(call).toEqual({
      scope: "auxiliary",
      task: "vision",
      provider: "auto",
      model: "",
    });
  });
});
