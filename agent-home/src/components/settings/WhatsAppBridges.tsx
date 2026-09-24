"use client";

/**
 * Settings → WhatsApp bridges: manage the deployment's bridge sidecars
 * (hermes-wa-bridge-<name>.service). Refresh shows live unit + pairing
 * state; rebind wipes the session and restarts so the bridge emits a fresh
 * QR; the QR renders server-side as SVG — the raw payload never reaches
 * client JS.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import type { WaBridge } from "@/types";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,30}$/;
//: Baileys rotates the QR payload about every 60s; poll often enough to
//: show a live code without hammering the box.
const QR_POLL_MS = 20_000;

type QrState = {
  svg: string;
  ageSeconds: number;
  stale: boolean;
};

function statusLabel(bridge: WaBridge): { text: string; tone: string } {
  if (bridge.active === "active" && bridge.qr_pending)
    return { text: "waiting for QR scan", tone: "text-amber-400" };
  if (bridge.active === "active")
    return { text: bridge.paired ? "connected" : "active", tone: "text-green-400" };
  if (bridge.active === "activating")
    return { text: "restarting", tone: "text-amber-400" };
  return { text: bridge.active, tone: "text-red-400" };
}

export function WhatsAppBridges() {
  const [bridges, setBridges] = useState<WaBridge[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [qrFor, setQrFor] = useState<string | null>(null);
  const [qr, setQr] = useState<QrState | null>(null);
  const [newName, setNewName] = useState("");
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState<{
    bridge: WaBridge;
    action: "stop" | "rebind";
  } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/wa-bridges");
      if (res.ok) {
        const data = (await res.json()) as { bridges: WaBridge[] };
        setBridges(data.bridges);
        setError(null);
      } else {
        setError("Could not load bridges.");
      }
    } catch {
      setError("Could not load bridges.");
    }
    setLoaded(true);
  }, []);

  const fetchQr = useCallback(async (name: string) => {
    const res = await fetch(`/api/wa-bridges/${encodeURIComponent(name)}/qr`);
    if (res.ok) {
      const data = (await res.json()) as {
        svg: string;
        age_seconds: number;
        stale: boolean;
      };
      setQr({ svg: data.svg, ageSeconds: data.age_seconds, stale: data.stale });
      return;
    }
    // 404 = no pending QR — usually means the phone just scanned and the
    // bridge deleted qr.txt.
    setQr(null);
    setQrFor(null);
    if (res.status !== 404) setError("Could not fetch the QR code.");
    const r = await fetch("/api/wa-bridges");
    if (r.ok) {
      setBridges(((await r.json()) as { bridges: WaBridge[] }).bridges);
    }
  }, []);

  // While a QR panel is open, keep refreshing it — the payload rotates.
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (qrFor) {
      pollRef.current = setInterval(() => void fetchQr(qrFor), QR_POLL_MS);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [qrFor, fetchQr]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = useCallback(
    async (name: string, action: "restart" | "stop" | "rebind") => {
      setBusy(`${name}:${action}`);
      setError(null);
      try {
        const res = await fetch(
          `/api/wa-bridges/${encodeURIComponent(name)}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ action }),
          },
        );
        if (!res.ok) {
          const data = (await res.json().catch(() => ({}))) as {
            detail?: string;
          };
          setError(data.detail ?? `Could not ${action} ${name}.`);
        }
      } catch {
        setError(`Could not ${action} ${name}.`);
      }
      setBusy(null);
      await reload();
      if (action === "rebind") {
        // The bridge needs a few seconds to emit the first QR.
        setQrFor(name);
        setQr(null);
        setTimeout(() => void fetchQr(name), 4000);
      }
    },
    [reload, fetchQr],
  );

  const add = useCallback(async () => {
    const name = newName.trim().toLowerCase();
    if (!NAME_RE.test(name)) {
      setError("Bridge name: lowercase letters, digits, hyphens.");
      return;
    }
    setAdding(true);
    setError(null);
    try {
      const res = await fetch("/api/wa-bridges", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: string;
      };
      if (!res.ok) {
        setError(data.detail ?? "Could not provision the bridge.");
      } else {
        setNewName("");
        setQrFor(name);
        setQr(null);
        setTimeout(() => void fetchQr(name), 4000);
      }
    } catch {
      setError("Could not provision the bridge.");
    }
    setAdding(false);
    await reload();
  }, [newName, reload, fetchQr]);

  return (
    <section data-component="WhatsAppBridges">
      <h2 className="text-sm font-semibold">WhatsApp bridges</h2>
      <p className="mb-3 text-xs text-[var(--color-muted)]">
        Linked WhatsApp phones. Rebind clears a logged-out session and shows
        a fresh QR to scan (WhatsApp → Linked devices → Link a device).
      </p>

      {error && <p className="mb-2 text-xs text-red-400">{error}</p>}

      <ul className="space-y-2">
        {loaded && bridges.length === 0 && (
          <li className="text-xs text-[var(--color-muted)]">
            No bridges configured.
          </li>
        )}
        {bridges.map((b) => {
          const status = statusLabel(b);
          const key = b.name;
          return (
            <li
              key={key}
              className="rounded border border-[var(--color-surface-2)] p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold">
                  {b.name}{" "}
                  <span className={`font-normal ${status.tone}`}>
                    {status.text}
                  </span>
                  {b.paired && b.active === "active" && !b.qr_pending && (
                    <span className="font-normal text-[var(--color-muted)]">
                      {" "}
                      (paired)
                    </span>
                  )}
                </span>
                <span className="flex gap-2 text-xs">
                  {b.qr_pending && (
                    <button
                      type="button"
                      className="text-[var(--color-accent)]"
                      onClick={() => {
                        setQrFor(qrFor === b.name ? null : b.name);
                        setQr(null);
                        if (qrFor !== b.name) void fetchQr(b.name);
                      }}
                    >
                      {qrFor === b.name ? "Hide QR" : "Show QR"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void act(b.name, "restart")}
                  >
                    Restart
                  </button>
                  <button
                    type="button"
                    className="text-[var(--color-accent)]"
                    disabled={busy !== null}
                    onClick={() =>
                      setConfirm({ bridge: b, action: "rebind" })
                    }
                  >
                    Rebind
                  </button>
                  <button
                    type="button"
                    className="text-red-400"
                    disabled={busy !== null || b.active !== "active"}
                    onClick={() => setConfirm({ bridge: b, action: "stop" })}
                  >
                    Stop
                  </button>
                </span>
              </div>
              {qrFor === b.name && (
                <div className="mt-2 space-y-1">
                  {qr ? (
                    <>
                      {/* SVG rendered server-side from the pairing payload. */}
                      <div
                        className="inline-block rounded bg-white p-2"
                        dangerouslySetInnerHTML={{ __html: qr.svg }}
                      />
                      <p className="text-xs text-[var(--color-muted)]">
                        QR age {qr.ageSeconds}s — refreshes automatically.
                        {qr.stale &&
                          " This code looks stale; hit Restart if it doesn't rotate."}
                      </p>
                    </>
                  ) : (
                    <p className="text-xs text-[var(--color-muted)]">
                      Waiting for the bridge to emit a QR…
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <div className="mt-3 flex items-center gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="new bridge name (e.g. work)"
          className="rounded border border-[var(--color-surface-2)] bg-transparent px-2 py-1 text-xs"
        />
        <button
          type="button"
          disabled={adding || !newName.trim()}
          onClick={() => void add()}
          className="rounded bg-[var(--color-accent)] px-2 py-1 text-xs font-semibold text-black"
        >
          {adding ? "Adding…" : "Add bridge"}
        </button>
        <button
          type="button"
          onClick={() => void reload()}
          className="rounded px-2 py-1 text-xs"
        >
          Refresh
        </button>
      </div>

      {confirm && (
        <ConfirmDialog
          title={
            confirm.action === "rebind"
              ? "Rebind bridge?"
              : "Stop bridge?"
          }
          body={
            confirm.action === "rebind"
              ? `Rebind ${confirm.bridge.name}? The current session is moved aside and the bridge restarts unpaired — you'll need to scan the new QR with the phone.`
              : `Stop ${confirm.bridge.name}? WhatsApp messages stop flowing until it's started again (Restart).`
          }
          confirmLabel={
            confirm.action === "rebind" ? "Rebind" : "Stop"
          }
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const { bridge, action } = confirm;
            setConfirm(null);
            void act(bridge.name, action);
          }}
        />
      )}
    </section>
  );
}
