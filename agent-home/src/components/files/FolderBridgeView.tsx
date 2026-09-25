"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { searchFolder } from "@/lib/folder-bridge/fsOps";
import {
  addFolder,
  fileSystemAccessSupported,
  folderPickerSupported,
  getFolderHandle,
  reapproveFolder,
  removeFolder,
  restoreFolders,
} from "@/lib/folder-bridge/handles";
import {
  connectFolderBridge,
  disconnectFolderBridge,
  getStatus,
  subscribeStatus,
} from "@/lib/folder-bridge/transport";
import type { ConnectionStatus, FolderRecord, SearchMatch } from "@/lib/folder-bridge/types";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  disconnected: "Disconnected",
  connecting: "Connecting…",
  connected: "Connected",
  reconnecting: "Reconnecting…",
};

const STATUS_TONE: Record<ConnectionStatus, string> = {
  disconnected: "text-[var(--color-muted)]",
  connecting: "text-amber-400",
  connected: "text-emerald-400",
  reconnecting: "text-amber-400",
};

function useConnectionStatus(): ConnectionStatus {
  return useSyncExternalStore(subscribeStatus, getStatus, () => "disconnected");
}

const noopSubscribe = () => () => {};

// `null` on the server and during hydration: the checks touch
// `window`/`document`, so reading them during SSR would make the server and
// client markup disagree.
function usePickerSupport(): boolean | null {
  return useSyncExternalStore(noopSubscribe, folderPickerSupported, () => null);
}

/**
 * `/files/bridge` — the Folder Bridge (per `folder-bridge-web-app-spec.md`).
 *
 * Lets the user grant the running agent read-only access to local Mac
 * folders via the File System Access API, over a ticket-authenticated
 * WebSocket to the `app-mcp` service's folder hub. Chromium-based browsers
 * get live handles that survive a reload; Safari/Firefox fall back to a
 * `webkitdirectory` snapshot that must be re-picked after a reload. The
 * picker and the connection both need the tab open to work — this is a
 * live bridge, not an upload.
 */
export function FolderBridgeView() {
  const status = useConnectionStatus();
  const supported = usePickerSupport();
  const snapshotMode = supported === true && !fileSystemAccessSupported();
  const [folders, setFolders] = useState<FolderRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchMatch[] | null>(null);
  const [preview, setPreview] = useState<SearchMatch | null>(null);

  useEffect(() => {
    if (!supported) return;
    void restoreFolders().then(setFolders);
  }, [supported]);

  const handleAddFolder = async () => {
    setError(null);
    setBusy(true);
    try {
      const record = await addFolder();
      if (record) setFolders((prev) => [...prev, record]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add that folder.");
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveFolder = async (id: string) => {
    await removeFolder(id);
    setFolders((prev) => prev.filter((f) => f.id !== id));
  };

  const handleReapprove = async (id: string) => {
    const permission = await reapproveFolder(id);
    setFolders((prev) => prev.map((f) => (f.id === id ? { ...f, permission } : f)));
  };

  // A local, in-browser search over the same approved folders the agent
  // would query — lets the user try the feature without needing a chat
  // turn, and confirms what the agent will see.
  const handleSearch = async () => {
    if (!query.trim() || folders.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const matches: SearchMatch[] = [];
      for (const folder of folders) {
        if (folder.permission !== "granted") continue;
        const handle = getFolderHandle(folder.id);
        if (!handle) continue;
        matches.push(
          ...(await searchFolder(handle, folder.id, folder.label, {
            query,
            extensions: [],
            limit: 50 - matches.length,
          })),
        );
        if (matches.length >= 50) break;
      }
      setResults(matches);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  };

  if (supported === null) return null;

  if (!supported) {
    return (
      <div
        data-component="FolderBridgeUnsupported"
        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
      >
        Folder Bridge needs a browser that can open a folder picker (Safari,
        Chrome, Edge, Brave or Firefox on a Mac). It is not available in this
        browser.
      </div>
    );
  }

  return (
    <div data-component="FolderBridgeView" className="flex flex-col gap-4">
      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm">
            <span aria-hidden className={STATUS_TONE[status]}>
              ●
            </span>
            <span className={STATUS_TONE[status]}>{STATUS_LABEL[status]}</span>
          </div>
          {status === "disconnected" || status === "reconnecting" ? (
            <button
              type="button"
              onClick={() => connectFolderBridge()}
              className="rounded-xl bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-[var(--color-accent-fg)]"
            >
              Connect
            </button>
          ) : (
            <button
              type="button"
              onClick={() => disconnectFolderBridge()}
              className="rounded-xl border border-[var(--color-border)] px-4 py-2 text-sm"
            >
              Disconnect
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-[var(--color-muted)]">
          Connecting lets the agent list, search and read files in the
          folders you approve below — nothing else on your Mac. Closing this
          tab or pressing Disconnect ends its access immediately.
        </p>
      </section>

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            Folders
          </h2>
          <button
            type="button"
            onClick={() => void handleAddFolder()}
            disabled={busy}
            className="rounded-xl border border-[var(--color-border)] px-3 py-1.5 text-xs disabled:opacity-50"
          >
            Add folder
          </button>
        </div>
        {folders.length === 0 ? (
          <p className="mt-2 text-sm text-[var(--color-muted)]">
            No folders yet — Add folder to approve one for the agent to use.
          </p>
        ) : null}
        {snapshotMode ? (
          <p className="mt-2 text-xs text-[var(--color-muted)]">
            This browser reads the folder as it was when you picked it — add
            it again to see new files, and after reloading this page.
          </p>
        ) : null}
        {folders.length > 0 ? (
          <ul className="mt-2 flex flex-col gap-1.5">
            {folders.map((folder) => (
              <li
                key={folder.id}
                className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <span className="min-w-0 flex-1 truncate">{folder.label}</span>
                {folder.permission !== "granted" ? (
                  <button
                    type="button"
                    onClick={() => void handleReapprove(folder.id)}
                    className="rounded-lg border border-amber-400/50 px-2 py-1 text-xs text-amber-400"
                  >
                    Needs re-approval
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void handleRemoveFolder(folder.id)}
                  className="rounded-lg border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-muted)]"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Search
        </h2>
        <div className="mt-2 flex gap-2">
          <input
            data-component="FolderBridgeSearch"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSearch();
            }}
            placeholder="Search by filename"
            className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
          />
          <button
            type="button"
            onClick={() => void handleSearch()}
            disabled={busy || folders.length === 0}
            className="rounded-xl border border-[var(--color-border)] px-3 py-2 text-sm disabled:opacity-50"
          >
            Search
          </button>
        </div>

        {error ? (
          <p role="alert" className="mt-2 text-sm text-red-400">
            {error}
          </p>
        ) : null}

        {results ? (
          <ul className="mt-3 flex flex-col gap-2" data-component="FolderBridgeResults">
            {results.length === 0 ? (
              <li className="text-sm text-[var(--color-muted)]">No matches.</li>
            ) : (
              results.map((match) => (
                <li key={`${match.folderId}/${match.path}`}>
                  <button
                    type="button"
                    onClick={() => setPreview(match)}
                    className="w-full rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-left text-sm"
                  >
                    <span className="block font-medium">{match.path}</span>
                    <span className="block text-xs text-[var(--color-muted)]">
                      {match.folderLabel} · {match.size} bytes ·{" "}
                      {new Date(match.modifiedAt).toLocaleDateString()}
                    </span>
                    {match.snippet ? (
                      <span className="mt-1 block truncate text-xs text-[var(--color-muted)]">
                        {match.snippet}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
      </section>

      {preview ? (
        <section
          data-component="FolderBridgePreview"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
              {preview.path}
            </h2>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="rounded-lg border border-[var(--color-border)] px-2 py-1 text-xs"
            >
              Close
            </button>
          </div>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-[var(--color-muted)]">
            {preview.snippet ?? "No preview available for this file type."}
          </pre>
        </section>
      ) : null}
    </div>
  );
}
