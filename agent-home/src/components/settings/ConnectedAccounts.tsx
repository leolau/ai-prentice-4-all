"use client";

/**
 * Settings → Connected accounts: each login manages its own entries in the
 * unified credential store (docs/design/unified-credential-store.md).
 *
 * Connect flow is the skill's proven manual code-paste OAuth: ask for the
 * services to enable, open the consent URL, paste the code/redirect URL the
 * browser lands on. The response echoes the granted account + scopes so a
 * wrong-account consent on a shared browser is visible immediately.
 */
import { useCallback, useEffect, useState } from "react";

import type { CredentialEntry } from "@/types";

type ConnectPhase = "idle" | "consent" | "busy";

const SERVICE_OPTIONS = [
  { id: "email", label: "Email (IMAP + Gmail API)" },
  { id: "calendar", label: "Calendar" },
  { id: "workspace", label: "Full workspace (Drive, Docs, Sheets)" },
] as const;

export function ConnectedAccounts() {
  const [entries, setEntries] = useState<CredentialEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [phase, setPhase] = useState<ConnectPhase>("idle");
  const [services, setServices] = useState<string[]>(["email", "calendar"]);
  const [hint, setHint] = useState("");
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/credentials");
      if (res.ok) {
        const data = (await res.json()) as { credentials: CredentialEntry[] };
        setEntries(data.credentials);
        setError(null);
      } else {
        setError("Could not load connected accounts.");
      }
    } catch {
      setError("Could not load connected accounts.");
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const start = useCallback(async () => {
    setPhase("busy");
    setError(null);
    try {
      const res = await fetch("/api/credentials/google/start", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: hint.trim() || undefined,
          services,
        }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        setError(data.detail ?? "Could not start the Google connection.");
        setPhase("idle");
        return;
      }
      const data = (await res.json()) as { auth_url: string };
      setAuthUrl(data.auth_url);
      setPasted("");
      setResult(null);
      setPhase("consent");
    } catch {
      setError("Could not start the Google connection.");
      setPhase("idle");
    }
  }, [hint, services]);

  const complete = useCallback(async () => {
    setPhase("busy");
    setError(null);
    try {
      const res = await fetch("/api/credentials/google/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code_or_url: pasted.trim() }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: string;
        account_email?: string | null;
        granted_scopes?: string[];
      };
      if (!res.ok) {
        setError(data.detail ?? "Google rejected the code; try again.");
        setPhase("consent");
        return;
      }
      const hasMail = (data.granted_scopes ?? []).includes(
        "https://mail.google.com/",
      );
      setResult(
        `Connected ${data.account_email ?? "account"}` +
          (hasMail ? "" : " — without the Mail scope, email polling stays off"),
      );
      setPhase("idle");
      setAuthUrl(null);
      setPasted("");
      await reload();
    } catch {
      setError("Could not finish the Google connection.");
      setPhase("idle");
    }
  }, [pasted, reload]);

  const toggleService = useCallback(
    async (entry: CredentialEntry, service: string, on: boolean) => {
      const next = on
        ? [...new Set([...entry.services, service])]
        : entry.services.filter((s) => s !== service);
      setEntries((prev) =>
        prev.map((e) =>
          e.provider === entry.provider && e.name === entry.name
            ? { ...e, services: next }
            : e,
        ),
      );
      const res = await fetch(
        `/api/credentials/${encodeURIComponent(entry.provider)}/${encodeURIComponent(entry.name)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ services: next }),
        },
      );
      if (!res.ok) setError("Could not update the account; reload to resync.");
    },
    [],
  );

  const setVisibility = useCallback(
    async (entry: CredentialEntry, visibility: string) => {
      const res = await fetch(
        `/api/credentials/${encodeURIComponent(entry.provider)}/${encodeURIComponent(entry.name)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ visibility }),
        },
      );
      if (res.ok) {
        setEntries((prev) =>
          prev.map((e) =>
            e.provider === entry.provider && e.name === entry.name
              ? { ...e, visibility }
              : e,
          ),
        );
      } else {
        setError("Could not update the account; reload to resync.");
      }
    },
    [],
  );

  const disconnect = useCallback(
    async (entry: CredentialEntry) => {
      const res = await fetch(
        `/api/credentials/${encodeURIComponent(entry.provider)}/${encodeURIComponent(entry.name)}`,
        { method: "DELETE" },
      );
      if (res.ok) {
        setEntries((prev) =>
          prev.filter(
            (e) => !(e.provider === entry.provider && e.name === entry.name),
          ),
        );
      } else {
        setError("Could not disconnect; reload to resync.");
      }
    },
    [],
  );

  return (
    <section>
      <h2 className="text-sm font-semibold">Connected accounts</h2>
      <p className="mb-3 text-xs text-[var(--color-muted)]">
        Your own Google (and other) credentials, stored per login. Background
        email/calendar polling uses an account only while its service toggle
        is on.
      </p>

      {error && <p className="mb-2 text-xs text-red-400">{error}</p>}
      {result && <p className="mb-2 text-xs text-green-400">{result}</p>}

      {loaded && entries.length === 0 && phase === "idle" && (
        <p className="mb-2 text-xs text-[var(--color-muted)]">
          Nothing connected yet for your login.
        </p>
      )}

      <ul className="mb-3 space-y-2">
        {entries.map((entry) => (
          <li
            key={`${entry.provider}/${entry.name}`}
            className="rounded border border-[var(--color-surface-2)] p-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold">
                {entry.name}{" "}
                <span className="font-normal text-[var(--color-muted)]">
                  ({entry.kind})
                </span>
              </span>
              <button
                type="button"
                className="text-xs text-red-400"
                onClick={() => void disconnect(entry)}
              >
                Disconnect
              </button>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs">
              {SERVICE_OPTIONS.filter((s) => s.id !== "workspace").map((s) => (
                <label key={s.id} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={entry.services.includes(s.id)}
                    onChange={(e) =>
                      void toggleService(entry, s.id, e.target.checked)
                    }
                  />
                  {s.label}
                </label>
              ))}
              <label className="flex items-center gap-1">
                <select
                  value={entry.visibility.startsWith("private") ? "private" : "shared"}
                  onChange={(e) =>
                    void setVisibility(
                      entry,
                      e.target.value === "shared"
                        ? "shared"
                        : `private:${entry.owner_user_id}`,
                    )
                  }
                  className="rounded border border-[var(--color-surface-2)] bg-transparent px-1 text-xs"
                >
                  <option value="private">private</option>
                  <option value="shared">shared</option>
                </select>
              </label>
            </div>
          </li>
        ))}
      </ul>

      {phase === "idle" && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-3 text-xs">
            {SERVICE_OPTIONS.map((s) => (
              <label key={s.id} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={services.includes(s.id)}
                  onChange={(e) =>
                    setServices((prev) =>
                      e.target.checked
                        ? [...prev, s.id]
                        : prev.filter((x) => x !== s.id),
                    )
                  }
                />
                {s.label}
              </label>
            ))}
          </div>
          <input
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder="Google account email (optional hint)"
            className="w-full rounded border border-[var(--color-surface-2)] bg-transparent px-2 py-1 text-xs"
          />
          <button
            type="button"
            disabled={services.length === 0}
            onClick={() => void start()}
            className="rounded bg-[var(--color-accent)] px-2 py-1 text-xs font-semibold text-black"
          >
            Connect Google account
          </button>
        </div>
      )}

      {phase !== "idle" && authUrl && (
        <div className="space-y-2 text-xs">
          <p>
            1. Open the consent link and approve (keep every checked service
            selected):{" "}
            <a
              href={authUrl}
              target="_blank"
              rel="noreferrer"
              className="underline text-[var(--color-accent)] break-all"
            >
              consent link
            </a>
          </p>
          <p>2. The browser lands on an unreachable localhost page — copy the
            code (or the whole URL) from its address bar:</p>
          <input
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder="paste code or redirect URL"
            className="w-full rounded border border-[var(--color-surface-2)] bg-transparent px-2 py-1"
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={phase === "busy" || !pasted.trim()}
              onClick={() => void complete()}
              className="rounded bg-[var(--color-accent)] px-2 py-1 font-semibold text-black"
            >
              {phase === "busy" ? "Working…" : "Complete"}
            </button>
            <button
              type="button"
              disabled={phase === "busy"}
              onClick={() => {
                setPhase("idle");
                setAuthUrl(null);
              }}
              className="rounded px-2 py-1"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {phase === "busy" && !authUrl && (
        <p className="text-xs text-[var(--color-muted)]">Working…</p>
      )}
    </section>
  );
}
