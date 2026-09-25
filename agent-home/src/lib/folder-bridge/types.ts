/**
 * Shared types for the Folder Bridge (`/files/bridge`): the browser-side
 * File System Access API bridge that lets the Hermes agent list/search/read
 * within local Mac folders the user has explicitly approved.
 *
 * These mirror the shapes the app-mcp service's `folder_bridge_*` MCP tools
 * send/expect (`app-mcp/app_mcp/server.py`) — kept here rather than
 * generated, since the wire vocabulary is small and stable.
 */

export type FolderPermission = "granted" | "prompt" | "denied";

/** One user-approved folder, as shown in the UI and reported to the agent. */
export interface FolderRecord {
  id: string;
  label: string;
  permission: FolderPermission;
  /**
   * User opted this folder into agent-initiated imports without a per-file
   * approval prompt. Held in the browser only (never persisted), so it is
   * re-decided every session, and enforced in the browser — the server
   * cannot mark a folder trusted.
   */
  trusted: boolean;
}

/**
 * The slice of the File System Access API the bridge actually uses. A real
 * `FileSystemDirectoryHandle` satisfies it; so does the `webkitdirectory`
 * snapshot used where the picker API is missing (`snapshotHandle.ts`).
 */
export interface FileHandleLike {
  readonly kind: "file";
  readonly name: string;
  getFile(): Promise<File>;
}

export interface DirectoryHandleLike {
  readonly kind: "directory";
  readonly name: string;
  entries(): AsyncIterableIterator<[string, DirectoryHandleLike | FileHandleLike]>;
  getDirectoryHandle(name: string): Promise<DirectoryHandleLike>;
  getFileHandle(name: string): Promise<FileHandleLike>;
  queryPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
  requestPermission(descriptor?: { mode?: "read" | "readwrite" }): Promise<PermissionState>;
}

/** In-memory registry entry — the handle itself never leaves the browser. */
export interface FolderEntry extends FolderRecord {
  handle: DirectoryHandleLike;
}

export interface DirectoryEntryInfo {
  name: string;
  path: string;
  kind: "file" | "directory";
  size?: number;
  modifiedAt?: string;
}

export interface SearchMatch {
  folderId: string;
  folderLabel: string;
  path: string;
  size: number;
  modifiedAt: string;
  snippet?: string;
}

export interface FileMetadata {
  folderId: string;
  path: string;
  size: number;
  modifiedAt: string;
}

export interface FileContentResult {
  folderId: string;
  path: string;
  encoding: "utf-8";
  content: string;
  truncated: boolean;
  size: number;
  /** Set when the bytes are not UTF-8 text; `content` is then empty. */
  binary?: boolean;
}

/** Outcome of copying one local file into the file store via the BFF. */
export interface ImportResult {
  folderId: string;
  path: string;
  assetId: string;
  filename: string;
  size: number;
  sha256: string;
  storageBucket: string;
  storagePath: string;
  /** The BFF's SHA-256 of what it stored equals the browser's SHA-256 of the original. */
  verified: boolean;
}

/** A command the server relays to the browser (matches `_folder_command` in app_mcp/server.py). */
export type FolderCommand =
  | { type: "listFolders" }
  | { type: "listDirectory"; folderId: string; path: string }
  | {
      type: "searchFiles";
      folderIds: string[];
      query: string;
      extensions: string[];
      limit: number;
    }
  | { type: "readFile"; folderId: string; path: string; encoding: "utf-8"; maxBytes: number }
  | { type: "getFileMetadata"; folderId: string; path: string }
  /**
   * `approved` is set by the server when the call came through the
   * approval-gated tool; the browser accepts an unapproved import only for
   * a folder the user marked trusted.
   */
  | { type: "importFile"; folderId: string; path: string; approved: boolean };

export type FolderCommandResult =
  | { ok: true; folders: FolderRecord[] }
  | { ok: true; entries: DirectoryEntryInfo[] }
  | { ok: true; matches: SearchMatch[] }
  | ({ ok: true } & FileContentResult)
  | ({ ok: true } & FileMetadata)
  | ({ ok: true } & ImportResult)
  | { ok: false; detail: string; needsApproval?: boolean };

export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "reconnecting";
