/**
 * The folder registry: `Map<folderId, FileSystemDirectoryHandle>` plus
 * labels, kept in memory and mirrored into IndexedDB so a page reload can
 * offer "reconnect" without re-running the picker for folders the user
 * already approved (the browser still re-verifies permission on restore —
 * this only saves the picker step, never the permission itself, matching
 * the File System Access API's own persistence model).
 *
 * Read-only, v1 (per spec §3.1 / §10): nothing here ever calls a write API.
 */
import { pickDirectoryViaInput } from "@/lib/folder-bridge/snapshotHandle";
import type {
  DirectoryHandleLike,
  FolderEntry,
  FolderPermission,
  FolderRecord,
} from "@/lib/folder-bridge/types";

const DB_NAME = "folder-bridge";
const DB_VERSION = 1;
const STORE = "folders";

interface StoredFolder {
  id: string;
  label: string;
  handle: FileSystemDirectoryHandle;
}

const registry = new Map<string, FolderEntry>();
let nextSeq = 1;

function newFolderId(): string {
  return `f${Date.now().toString(36)}${(nextSeq++).toString(36)}`;
}

function openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}

async function persist(entry: StoredFolder): Promise<void> {
  const db = await openDb();
  if (!db) return;
  try {
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(entry);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } finally {
    db.close();
  }
}

async function removePersisted(id: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  try {
    await new Promise<void>((resolve) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
    });
  } finally {
    db.close();
  }
}

async function loadPersisted(): Promise<StoredFolder[]> {
  const db = await openDb();
  if (!db) return [];
  try {
    return await new Promise<StoredFolder[]>((resolve) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => resolve((req.result as StoredFolder[]) ?? []);
      req.onerror = () => resolve([]);
    });
  } finally {
    db.close();
  }
}

async function permissionOf(handle: DirectoryHandleLike): Promise<FolderPermission> {
  try {
    const state = await handle.queryPermission({ mode: "read" });
    if (state === "granted") return "granted";
    if (state === "denied") return "denied";
    return "prompt";
  } catch {
    return "prompt";
  }
}

/** True in Chromium-based browsers; false elsewhere (Safari, Firefox). */
export function fileSystemAccessSupported(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

/**
 * True when the browser can pick a folder at all — via the File System
 * Access API or the `webkitdirectory` input fallback (Safari, Firefox).
 */
export function folderPickerSupported(): boolean {
  if (typeof document === "undefined") return false;
  return fileSystemAccessSupported() || "webkitdirectory" in document.createElement("input");
}

/**
 * Folders to show when the bridge page mounts. The registry is module
 * state, so folders added earlier in this page load (including snapshot
 * folders and their trust flags) are still live after in-app navigation
 * and are returned as-is; only folders not already live are restored from
 * storage. Restored handles still require the browser to re-verify
 * permission (usually "prompt" until the user interacts with the page
 * again) — this never auto-grants access.
 */
export async function restoreFolders(): Promise<FolderRecord[]> {
  const out = listFolders();
  const stored = await loadPersisted();
  for (const { id, label, handle } of stored) {
    if (registry.has(id)) continue;
    const permission = await permissionOf(handle);
    registry.set(id, { id, label, handle, permission, trusted: false });
    out.push({ id, label, permission, trusted: false });
  }
  return out;
}

/**
 * Open the native folder picker and register the chosen folder. Must be
 * called from a real user gesture (a click handler) or the browser refuses
 * it. Returns null if the user cancels the picker.
 */
export async function addFolder(label?: string): Promise<FolderRecord | null> {
  if (!folderPickerSupported()) {
    throw new Error("This browser cannot open a folder picker.");
  }
  const id = newFolderId();
  if (!fileSystemAccessSupported()) {
    // Snapshot handles hold plain `File` objects, not durable handles, so
    // they are never persisted — the user re-picks after a reload.
    const handle = await pickDirectoryViaInput();
    if (!handle) return null;
    const record: FolderRecord = {
      id,
      label: label || handle.name,
      permission: "granted",
      trusted: false,
    };
    registry.set(id, { ...record, handle });
    return record;
  }
  let handle: FileSystemDirectoryHandle;
  try {
    handle = await window.showDirectoryPicker({ mode: "read" });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return null;
    throw err;
  }
  const record: FolderRecord = {
    id,
    label: label || handle.name,
    permission: "granted",
    trusted: false,
  };
  registry.set(id, { ...record, handle });
  await persist({ id, label: record.label, handle });
  return record;
}

/** Re-request permission for a folder whose access was revoked/lapsed. */
export async function reapproveFolder(id: string): Promise<FolderPermission> {
  const entry = registry.get(id);
  if (!entry) return "denied";
  try {
    const state = await entry.handle.requestPermission({ mode: "read" });
    const permission: FolderPermission =
      state === "granted" ? "granted" : state === "denied" ? "denied" : "prompt";
    entry.permission = permission;
    return permission;
  } catch {
    return "denied";
  }
}

export async function removeFolder(id: string): Promise<void> {
  registry.delete(id);
  await removePersisted(id);
}

/**
 * Opt a folder into (or out of) agent-initiated imports without per-file
 * approval. Session-only by design: trust is never written to IndexedDB, so
 * every page load starts with nothing trusted.
 */
export function setFolderTrusted(id: string, trusted: boolean): FolderRecord | null {
  const entry = registry.get(id);
  if (!entry) return null;
  entry.trusted = trusted;
  const { label, permission } = entry;
  return { id, label, permission, trusted };
}

export function isFolderTrusted(id: string): boolean {
  return registry.get(id)?.trusted ?? false;
}

export function getFolderLabel(id: string): string | null {
  return registry.get(id)?.label ?? null;
}

export function listFolders(): FolderRecord[] {
  return Array.from(registry.values(), ({ id, label, permission, trusted }) => ({
    id,
    label,
    permission,
    trusted,
  }));
}

export function getFolderHandle(id: string): DirectoryHandleLike | null {
  return registry.get(id)?.handle ?? null;
}

/** Test-only: reset module state between cases. */
export function __resetRegistryForTest(): void {
  registry.clear();
  nextSeq = 1;
}
