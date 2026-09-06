"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { friendlyError } from "@/components/projects/errors";
import { draftPlanPrompt } from "@/components/projects/panels/PlanPanel";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectAutonomy, ProjectCadence } from "@/types";

/** The §2.2 mandatory fields a 422's `missing` list can name. */
type MandatoryField = "goal" | "description" | "outputs" | "host_profile";

/** Each choice says what the user will see happen, not what the enum means. */
export const CADENCES: { value: ProjectCadence; label: string; explain: string }[] = [
  {
    value: "one_off",
    label: "One-off — finish it once",
    explain:
      "You start each run yourself. Once every required output is accepted the project offers to close.",
  },
  {
    value: "repeatable",
    label: "Repeatable — runs on a schedule",
    explain:
      "Runs fire on a schedule you set in Settings after creating (nothing fires until you do); each run delivers the outputs again.",
  },
  {
    value: "standing",
    label: "Standing — an ongoing duty",
    explain:
      "Never “done”: cards keep arriving and progress is measured by recent deliveries, not a finish line.",
  },
];

export const AUTONOMIES: { value: ProjectAutonomy; label: string; explain: string }[] = [
  {
    value: "manual",
    label: "Manual — never runs itself",
    explain:
      "Nothing happens without you: every step waits in triage until you make it ready, a schedule never fires, and only Run now starts a run.",
  },
  {
    value: "supervised",
    label: "Supervised — runs, then reports",
    explain:
      "The agent works through the plan and pauses at checkpoints for you; you accept the outputs.",
  },
  {
    value: "autonomous",
    label: "Autonomous — runs and decides",
    explain:
      "The agent promotes its own steps and runs to the end without stopping; irreversible acts still ask for approval and you still accept the outputs.",
  },
];

type PlanChoice = "agent" | "self";

/**
 * The two-step create form (§13): step 1 is *what* — goal, description and
 * at least one output (everything mandatory); step 2 is *how it runs* —
 * cadence and autonomy, skippable into a `supervised` `one_off`. Ten
 * optional fields on one form would guarantee nobody fills in the four
 * that matter.
 *
 * A refusal maps onto the field that is blank — never a toast — and what
 * was typed survives it: the inputs are state, so a 422 costs nothing.
 */
export function NewProjectForm({ servingProfile }: { servingProfile: string }) {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);
  const [goal, setGoal] = useState("");
  const [description, setDescription] = useState("");
  const [name, setName] = useState("");
  const [outputs, setOutputs] = useState<string[]>([""]);
  const [cadence, setCadence] = useState<ProjectCadence>("one_off");
  const [autonomy, setAutonomy] = useState<ProjectAutonomy>("supervised");
  const [plan, setPlan] = useState<PlanChoice>("agent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<MandatoryField, string>>>({});

  const validateStep1 = (): boolean => {
    const next: Partial<Record<MandatoryField, string>> = {};
    if (!goal.trim()) next.goal = "A project needs a goal sentence.";
    if (!description.trim()) {
      next.description = "A project needs a description.";
    }
    if (!outputs.some((title) => title.trim())) {
      next.outputs = "A project declares at least one output.";
    }
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          goal: goal.trim(),
          description: description.trim(),
          name: name.trim() || undefined,
          host_profile: servingProfile,
          outputs: outputs
            .map((title) => title.trim())
            .filter(Boolean)
            .map((title) => ({ title })),
          cadence,
          autonomy,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        slug?: string;
        detail?: unknown;
        error?: string;
        missing?: unknown;
      };
      if (res.ok && data.slug) {
        // The project exists but cannot run until a plan is active. Either
        // hand the brief to the agent now, or land on the detail page where
        // the readiness checklist points at the Plan panel.
        if (plan === "agent") {
          const params = new URLSearchParams({
            profile: servingProfile,
            draft: draftPlanPrompt(data.slug, name.trim() || goal.trim()),
          });
          router.push(`/chat?${params.toString()}`);
          return;
        }
        router.push(`/projects/${data.slug}#panel-plan`);
        return;
      }
      // The 422 names the blank field(s) — the BFF pre-check and the
      // widened bridge both carry the `missing` list at the top level
      // beside the string `detail` (U3) — map it onto the form.
      const missing = Array.isArray(data.missing)
        ? (data.missing as MandatoryField[])
        : null;
      if (missing && missing.length > 0) {
        const next: Partial<Record<MandatoryField, string>> = {};
        for (const field of missing) {
          next[field] = "This field is mandatory.";
        }
        setFieldErrors(next);
        setError(
          typeof data.detail === "string" && data.detail ? data.detail : null,
        );
        if (missing.includes("goal") || missing.includes("description") || missing.includes("outputs")) {
          setStep(1);
        }
        return;
      }
      setError(
        friendlyError(
          { status: res.status, detail: data.detail },
          "That didn't go through — check the fields and try again.",
        ),
      );
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const inputClass = (invalid: boolean) =>
    `w-full rounded-xl border bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)] ${
      invalid ? "border-red-400" : "border-[var(--color-border)]"
    }`;

  return (
    <BusyRegion busy={busy} label="Creating the project…">
      <form
        data-component="NewProjectForm"
        className="flex flex-col gap-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (step === 1) {
            if (validateStep1()) setStep(2);
          } else {
            void submit();
          }
        }}
      >
        {step === 1 ? (
          <>
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Step 1 of 2 — what this project is
            </p>

            <label className="flex flex-col gap-1 text-sm">
              <span>Goal — what success means *</span>
              <input
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="Ship the Monday digest to every subscriber"
                className={inputClass(Boolean(fieldErrors.goal))}
              />
              {fieldErrors.goal ? (
                <span role="alert" className="text-xs text-red-400">
                  {fieldErrors.goal}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span>Description — the brief the agent works from *</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={5}
                placeholder="A weekly digest compiled from arrivals and emailed each Monday…"
                className={inputClass(Boolean(fieldErrors.description))}
              />
              {fieldErrors.description ? (
                <span role="alert" className="text-xs text-red-400">
                  {fieldErrors.description}
                </span>
              ) : null}
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span>
                Name <span className="text-[var(--color-muted)]">(optional — derived from the goal)</span>
              </span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Monday digest"
                className={inputClass(false)}
              />
            </label>

            <div className="flex flex-col gap-1 text-sm">
              <span>Outputs — what it delivers * </span>
              {outputs.map((title, index) => (
                <div key={index} className="flex gap-2">
                  <input
                    value={title}
                    onChange={(e) =>
                      setOutputs((prev) =>
                        prev.map((t, i) => (i === index ? e.target.value : t)),
                      )
                    }
                    placeholder="The Monday digest email"
                    className={inputClass(Boolean(fieldErrors.outputs))}
                  />
                  {outputs.length > 1 ? (
                    <button
                      type="button"
                      aria-label={`Remove output ${index + 1}`}
                      onClick={() =>
                        setOutputs((prev) => prev.filter((_t, i) => i !== index))
                      }
                      className="rounded-xl border border-[var(--color-border)] px-3 text-sm text-[var(--color-muted)]"
                    >
                      ×
                    </button>
                  ) : null}
                </div>
              ))}
              {fieldErrors.outputs ? (
                <span role="alert" className="text-xs text-red-400">
                  {fieldErrors.outputs}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => setOutputs((prev) => [...prev, ""])}
                className="self-start rounded-xl border border-[var(--color-border)] px-3 py-1.5 text-xs"
              >
                Add another output
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              Step 2 of 2 — how it runs
            </p>

            <label className="flex flex-col gap-1 text-sm">
              <span>Cadence</span>
              <select
                value={cadence}
                onChange={(e) => setCadence(e.target.value as ProjectCadence)}
                aria-describedby="new-project-cadence-help"
                className={inputClass(false)}
              >
                {CADENCES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span
                id="new-project-cadence-help"
                className="text-xs text-[var(--color-muted)]"
              >
                {CADENCES.find((option) => option.value === cadence)?.explain}
              </span>
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span>Autonomy</span>
              <select
                value={autonomy}
                onChange={(e) => setAutonomy(e.target.value as ProjectAutonomy)}
                aria-describedby="new-project-autonomy-help"
                className={inputClass(false)}
              >
                {AUTONOMIES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span
                id="new-project-autonomy-help"
                className="text-xs text-[var(--color-muted)]"
              >
                {AUTONOMIES.find((option) => option.value === autonomy)?.explain}
              </span>
            </label>

            <fieldset
              data-component="PlanChoice"
              className="flex flex-col gap-2 rounded-xl border border-[var(--color-border)] p-3 text-sm"
            >
              <legend className="px-1 text-sm">Plan — a run needs one</legend>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="plan"
                  value="agent"
                  checked={plan === "agent"}
                  onChange={() => setPlan("agent")}
                  className="mt-1"
                />
                <span>
                  Ask the agent to draft it
                  <span className="block text-xs text-[var(--color-muted)]">
                    Opens a chat with the brief; the agent proposes steps and
                    you activate the plan when it looks right.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="plan"
                  value="self"
                  checked={plan === "self"}
                  onChange={() => setPlan("self")}
                  className="mt-1"
                />
                <span>
                  I&rsquo;ll write it myself
                  <span className="block text-xs text-[var(--color-muted)]">
                    Lands on the project page with the readiness checklist and
                    the Plan editor.
                  </span>
                </span>
              </label>
            </fieldset>

            <label className="flex flex-col gap-1 text-sm">
              <span>Host profile</span>
              <input
                value={servingProfile}
                readOnly
                aria-readonly="true"
                className="w-full cursor-default rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm text-[var(--color-muted)]"
              />
              <span className="text-xs text-[var(--color-muted)]">
                Fixed to the profile serving this page — the record lives
                where you can see it.
              </span>
            </label>
          </>
        )}

        {error ? (
          <p role="alert" className="text-sm text-red-400">
            {error}
          </p>
        ) : null}

        <div className="flex items-center justify-between gap-2">
          {step === 2 ? (
            <button
              type="button"
              onClick={() => setStep(1)}
              className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
            >
              Back
            </button>
          ) : (
            <span />
          )}
          <button
            type="submit"
            disabled={busy}
            className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
          >
            {step === 1
              ? "Next"
              : plan === "agent"
                ? "Create and draft the plan"
                : "Create project"}
          </button>
        </div>
      </form>
    </BusyRegion>
  );
}
