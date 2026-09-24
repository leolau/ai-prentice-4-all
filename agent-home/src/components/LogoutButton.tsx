"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";

/** Clears the `agent-home` session then returns to the login page. */
export function LogoutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function signOut() {
    setConfirming(false);
    setBusy(true);
    try {
      await fetch("/api/session/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <>
      <button
        data-component="LogoutButton"
        type="button"
        disabled={busy}
        onClick={() => setConfirming(true)}
        className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--color-border)] px-4 py-3 text-center text-sm text-[var(--color-muted)] disabled:opacity-60"
      >
        {busy ? (
          <>
            <Spinner />
            Signing out…
          </>
        ) : (
          "Sign out"
        )}
      </button>
      {confirming && (
        <ConfirmDialog
          title="Sign out?"
          body="You'll need to log in again on this device."
          confirmLabel="Sign out"
          onCancel={() => setConfirming(false)}
          onConfirm={() => void signOut()}
        />
      )}
    </>
  );
}
