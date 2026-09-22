"use client";

import { useEffect, useMemo, useState } from "react";

import type { ModelSlot } from "@/components/models/ModelsView";
import { Spinner } from "@/components/ui/Spinner";
import { withProfileBody, withProfileQuery } from "@/lib/chat/profile";
import type {
  AuxTaskAssignment,
  ModelOptionsResponse,
  ModelSetResponse,
} from "@/types";

type Phase =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "confirm-expensive"; message: string }
  | { kind: "confirm-disconnect" };

/**
 * The model picker bottom sheet, opened by tapping the main-model card or a
 * task-role row. Covers three provider states:
 *   authenticated            → model list + Set (+ Disconnect at the bottom)
 *   unauthenticated, api_key → inline "Add API key" (validate → save → list)
 *   unauthenticated, other   → pointer to onboarding (OAuth stays elsewhere)
 *
 * All writes go through `/api/models/*` BFF routes; `confirm_required`
 * responses become an inline confirm that re-sends with
 * `confirm_expensive_model: true`.
 */
export function ModelPickerSheet({
  slot,
  currentMain,
  currentAssignment,
  profile,
  onClose,
  onChanged,
  onError,
}: {
  slot: ModelSlot;
  currentMain: { provider: string; model: string };
  currentAssignment?: AuxTaskAssignment;
  profile?: string;
  onClose: () => void;
  onChanged: () => void;
  onError: (message: string | null) => void;
}) {
  const title = slot.kind === "main" ? "Main model" : `${slot.label} model`;
  const subtitle =
    slot.kind === "main"
      ? "the brain every session starts with"
      : `${slot.hint} · currently ${describeAssignment(currentAssignment, currentMain)}`;

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [options, setOptions] = useState<ModelOptionsResponse | null>(null);
  const [providerSlug, setProviderSlug] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [keyValue, setKeyValue] = useState("");
  const [busy, setBusy] = useState<"set" | "key" | "reset" | "disconnect" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadOptions = async () => {
    try {
      const res = await fetch(withProfileQuery("/api/models/options", profile), {
        cache: "no-store",
      });
      const body = (await res.json()) as ModelOptionsResponse & { detail?: string };
      if (!res.ok) throw new Error(body.detail ?? "Failed to load model options.");
      setOptions(body);
      return body;
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load model options.");
      return null;
    }
  };

  useEffect(() => {
    let active = true;
    (async () => {
      const body = await loadOptions();
      if (!active || !body) return;
      // Seed the provider dropdown from the slot's current assignment.
      const initial =
        slot.kind === "main"
          ? currentMain.provider || body.provider || body.providers[0]?.slug || ""
          : currentAssignment && currentAssignment.provider !== "auto"
            ? currentAssignment.provider
            : body.provider || body.providers[0]?.slug || "";
      setProviderSlug(initial);
      setSelectedModel(
        slot.kind === "main" ? currentMain.model : currentAssignment?.model ?? "",
      );
      setPhase({ kind: "ready" });
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const provider = useMemo(
    () => options?.providers.find((p) => p.slug === providerSlug) ?? null,
    [options, providerSlug],
  );
  const authenticated = provider?.authenticated === true;
  const keyBased = provider?.auth_type === "api_key" && !!provider?.key_env;

  async function setModel(confirmExpensive: boolean) {
    if (!provider || busy) return;
    setBusy("set");
    setNote(null);
    try {
      const res = await fetch("/api/models/set", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          withProfileBody(
            {
              scope: slot.kind === "main" ? "main" : "auxiliary",
              task: slot.kind === "aux" ? slot.task : undefined,
              provider: provider.slug,
              model: selectedModel,
              confirm_expensive_model: confirmExpensive,
            },
            profile,
          ),
        ),
      });
      const body = (await res.json()) as ModelSetResponse;
      if (!res.ok) throw new Error(body.detail ?? "Couldn't save the model.");
      if (body.confirm_required) {
        setPhase({ kind: "confirm-expensive", message: body.confirm_message ?? "" });
        return;
      }
      if (body.stale_aux?.length) {
        onError(
          `Saved — but ${body.stale_aux.length} role(s) are still pinned to ` +
            `${body.stale_aux[0].provider}; they won't follow the new main model.`,
        );
      }
      onChanged();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Couldn't save the model.");
    } finally {
      setBusy(null);
    }
  }

  async function resetToAuto() {
    if (slot.kind !== "aux" || busy) return;
    setBusy("reset");
    setNote(null);
    try {
      const res = await fetch("/api/models/set", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          withProfileBody(
            { scope: "auxiliary", task: slot.task, provider: "auto", model: "" },
            profile,
          ),
        ),
      });
      const body = (await res.json()) as ModelSetResponse;
      if (!res.ok) throw new Error(body.detail ?? "Couldn't reset the role.");
      onChanged();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Couldn't reset the role.");
    } finally {
      setBusy(null);
    }
  }

  async function saveKey() {
    if (!provider?.key_env || busy) return;
    setBusy("key");
    setNote(null);
    try {
      const res = await fetch("/api/models/provider-key", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          withProfileBody({ key: provider.key_env, value: keyValue }, profile),
        ),
      });
      const body = (await res.json()) as {
        ok?: boolean;
        verified?: boolean;
        detail?: string;
      };
      if (!res.ok) throw new Error(body.detail ?? "Couldn't save the key.");
      setKeyValue("");
      setNote(
        body.verified
          ? "Key saved and verified."
          : "Key saved — couldn't verify it remotely; the catalog below is the real test.",
      );
      // The provider now authenticates: refetch so its model list appears.
      await loadOptions();
      setPhase({ kind: "ready" });
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Couldn't save the key.");
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    if (!provider?.key_env || busy) return;
    setBusy("disconnect");
    setNote(null);
    try {
      const res = await fetch("/api/models/provider-key", {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(withProfileBody({ key: provider.key_env }, profile)),
      });
      const body = (await res.json()) as { ok?: boolean; detail?: string };
      if (!res.ok) throw new Error(body.detail ?? "Couldn't disconnect the provider.");
      await loadOptions();
      setPhase({ kind: "ready" });
      setNote(`${provider.name} disconnected — slots pinned to it fall back to auto.`);
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Couldn't disconnect the provider.");
      setPhase({ kind: "ready" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      data-component="ModelPickerSheet"
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85dvh] w-full max-w-sm flex-col rounded-t-2xl border border-[var(--color-border)] bg-[var(--color-bg)] p-4 sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mx-auto mb-3 h-1 w-9 rounded-full bg-[var(--color-border)] sm:hidden" />
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">{title}</h2>
            <p className="mt-0.5 text-xs text-[var(--color-muted)]">{subtitle}</p>
          </div>
          <button type="button" onClick={onClose} className="text-sm text-[var(--color-muted)]">
            Close
          </button>
        </div>

        {loadError ? (
          <p className="mb-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs text-red-300">
            {loadError}
          </p>
        ) : null}
        {note ? (
          <p className="mb-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-xs text-[var(--color-muted)]">
            {note}
          </p>
        ) : null}

        {phase.kind === "loading" ? (
          <p
            role="status"
            aria-live="polite"
            className="flex items-center justify-center gap-2 py-8 text-sm text-[var(--color-accent)]"
          >
            <Spinner size="md" />
            Loading providers…
          </p>
        ) : phase.kind === "confirm-expensive" ? (
          <div className="space-y-3">
            <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              {phase.message || "This model may be expensive."}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPhase({ kind: "ready" })}
                className="rounded-full border border-[var(--color-border)] px-4 py-1.5 text-xs"
              >
                Back
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => {
                  setPhase({ kind: "ready" });
                  void setModel(true);
                }}
                className="ml-auto rounded-full bg-[var(--color-accent)] px-4 py-1.5 text-xs font-semibold text-[var(--color-accent-fg)] disabled:opacity-60"
              >
                Use it anyway
              </button>
            </div>
          </div>
        ) : phase.kind === "confirm-disconnect" && provider ? (
          <div className="space-y-3">
            <p className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-300">
              Disconnect {provider.name}? Its key is removed from .env — any
              roles pinned to it fall back to the main model.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPhase({ kind: "ready" })}
                className="rounded-full border border-[var(--color-border)] px-4 py-1.5 text-xs"
              >
                Keep it
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void disconnect()}
                className="ml-auto inline-flex items-center gap-2 rounded-full bg-red-500/80 px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
              >
                {busy === "disconnect" ? <Spinner /> : null}
                Disconnect
              </button>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <label className="mb-1 text-xs text-[var(--color-muted)]">Provider</label>
            <select
              value={providerSlug}
              onChange={(e) => {
                setProviderSlug(e.target.value);
                setSelectedModel("");
                setNote(null);
              }}
              className="mb-3 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-sm"
            >
              {(options?.providers ?? []).map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                  {p.authenticated ? "" : " — not connected"}
                </option>
              ))}
            </select>

            {provider && !authenticated && keyBased ? (
              <div>
                <p className="mb-2 rounded-lg bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-muted)]">
                  <span className="font-medium text-[var(--color-fg)]">{provider.name}</span>{" "}
                  isn&apos;t connected yet. Paste its API key — it&apos;s
                  validated, then stored in Hermes&apos; .env.
                </p>
                <input
                  type="password"
                  value={keyValue}
                  onChange={(e) => setKeyValue(e.target.value)}
                  placeholder={provider.key_env ?? "API key"}
                  autoComplete="off"
                  className="mb-2 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 font-mono text-xs"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={onClose}
                    className="rounded-full border border-[var(--color-border)] px-4 py-1.5 text-xs"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!keyValue.trim() || busy !== null}
                    onClick={() => void saveKey()}
                    className="ml-auto inline-flex items-center gap-2 rounded-full bg-[var(--color-accent)] px-4 py-1.5 text-xs font-semibold text-[var(--color-accent-fg)] disabled:opacity-60"
                  >
                    {busy === "key" ? <Spinner /> : null}
                    Save key
                  </button>
                </div>
              </div>
            ) : provider && !authenticated && !keyBased ? (
              <p className="rounded-lg bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-muted)]">
                {provider.name} uses {provider.auth_type || "a dedicated"} sign-in —
                connect it from <span className="font-medium">Getting started</span>,
                then pick its model here.
              </p>
            ) : (
              <>
                <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]">
                  {(provider?.models ?? []).length === 0 ? (
                    <p className="p-3 text-xs text-[var(--color-muted)]">
                      No models listed for this provider.
                    </p>
                  ) : (
                    (provider?.models ?? []).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setSelectedModel(m)}
                        className={`flex w-full items-center justify-between px-3 py-2.5 text-left text-sm ${
                          selectedModel === m
                            ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]"
                            : ""
                        }`}
                      >
                        <span className="min-w-0 truncate">{m}</span>
                        {selectedModel === m ? <span>✓</span> : null}
                      </button>
                    ))
                  )}
                </div>
                <div className="mt-3 flex gap-2">
                  {slot.kind === "aux" ? (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void resetToAuto()}
                      className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border)] px-4 py-1.5 text-xs disabled:opacity-60"
                    >
                      {busy === "reset" ? <Spinner /> : null}
                      Reset to auto
                    </button>
                  ) : null}
                  <button
                    type="button"
                    disabled={!selectedModel || busy !== null}
                    onClick={() => void setModel(false)}
                    className="ml-auto inline-flex items-center gap-2 rounded-full bg-[var(--color-accent)] px-4 py-1.5 text-xs font-semibold text-[var(--color-accent-fg)] disabled:opacity-60"
                  >
                    {busy === "set" ? <Spinner /> : null}
                    Set model
                  </button>
                </div>
                {authenticated && keyBased ? (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => setPhase({ kind: "confirm-disconnect" })}
                    className="mt-3 border-t border-[var(--color-border)] pt-3 text-left text-xs text-red-300"
                  >
                    Disconnect {provider?.name} — remove its key from .env
                  </button>
                ) : null}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function describeAssignment(
  a: AuxTaskAssignment | undefined,
  main: { provider: string; model: string },
): string {
  if (!a || a.provider === "auto" || !a.provider) {
    return `auto → ${main.model || "main"}`;
  }
  return a.model || a.provider;
}
