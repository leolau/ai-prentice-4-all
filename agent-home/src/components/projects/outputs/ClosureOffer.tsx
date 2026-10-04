"use client";

import { useState } from "react";

import { ActionButton } from "@/components/projects/ActionButton";
import { ActionError } from "@/components/projects/outputs/ActionError";
import { useProjectAction } from "@/components/projects/useProjectAction";

/** Every required output accepted: offer to mark the project done. */
export function ClosureOffer({ slug, onDismiss }: { slug: string; onDismiss: () => void }) {
  const action = useProjectAction();
  const [closed, setClosed] = useState(false);
  return (
    <div
      data-component="ClosureOffer"
      className="mt-2 rounded-lg border border-[var(--color-accent)]/40 bg-[var(--color-surface-2)] px-3 py-2 text-sm"
    >
      {closed ? (
        "This project is marked done. It stays on the record; archive it from the ⋯ menu when you want it off the list."
      ) : (
        <>
          Every required output is now accepted — this project can be closed.
          Mark it done here, or keep it open for another run.
          <span className="mt-2 flex flex-wrap gap-2">
            <ActionButton
              busy={action.busy}
              pendingLabel="Marking done…"
              onClick={() =>
                void action.run(`/api/projects/${encodeURIComponent(slug)}`, {
                  method: "PATCH",
                  body: { status: "done" },
                  onSuccess: () => setClosed(true),
                })
              }
              className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent-fg)] disabled:opacity-50"
            >
              Mark project done
            </ActionButton>
            <button
              type="button"
              onClick={onDismiss}
              disabled={action.busy}
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs disabled:opacity-50"
            >
              Keep open
            </button>
          </span>
          <ActionError action={action} />
        </>
      )}
    </div>
  );
}
