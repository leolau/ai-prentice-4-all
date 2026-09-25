/**
 * File System Access API operations, scoped to one already-approved
 * `DirectoryHandleLike`. Pure with respect to everything except the
 * handle itself, so it's testable against fake handle objects that
 * implement the same minimal async-iteration shape the real API does — no
 * real browser or real disk needed.
 *
 * Safety: traversal is depth- and count-capped (`MAX_DEPTH`, `MAX_VISITED`)
 * so a pathological folder (a huge node_modules, a deep symlink-like nest)
 * can't hang the tab — the File System Access API does not expose real
 * filesystem symlinks to the browser, so a depth cap is sufficient cycle
 * protection without needing an inode/seen-set.
 */
import type {
  DirectoryEntryInfo,
  DirectoryHandleLike,
  FileHandleLike,
  ImportResult,
  SearchMatch,
} from "@/lib/folder-bridge/types";

const MAX_DEPTH = 12;
const MAX_VISITED = 5000;
const SNIPPET_BYTES = 400;
const TEXT_LIKE_EXTENSIONS = new Set([
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".yaml",
  ".yml",
  ".csv",
  ".log",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".py",
  ".rs",
  ".go",
  ".java",
  ".c",
  ".cpp",
  ".h",
  ".css",
  ".html",
  ".xml",
  ".sh",
]);

function extensionOf(name: string): string {
  const idx = name.lastIndexOf(".");
  return idx === -1 ? "" : name.slice(idx).toLowerCase();
}

function joinPath(base: string, name: string): string {
  return base ? `${base}/${name}` : name;
}

/** Navigate a `/`-separated relative path down to its directory handle. */
export async function resolveDirectory(
  root: DirectoryHandleLike,
  path: string,
): Promise<DirectoryHandleLike> {
  let dir = root;
  for (const segment of path.split("/").filter(Boolean)) {
    dir = await dir.getDirectoryHandle(segment);
  }
  return dir;
}

/** Navigate a `/`-separated relative path to a file handle. */
export async function resolveFile(
  root: DirectoryHandleLike,
  path: string,
): Promise<FileHandleLike> {
  const parts = path.split("/").filter(Boolean);
  const fileName = parts.pop();
  if (!fileName) throw new Error("Empty file path.");
  const dir = parts.length ? await resolveDirectory(root, parts.join("/")) : root;
  return dir.getFileHandle(fileName);
}

/** One directory's immediate contents (not recursive). */
export async function listDirectoryEntries(
  root: DirectoryHandleLike,
  path: string,
): Promise<DirectoryEntryInfo[]> {
  const dir = await resolveDirectory(root, path);
  const out: DirectoryEntryInfo[] = [];
  for await (const [name, handle] of dir.entries()) {
    const entryPath = joinPath(path, name);
    if (handle.kind === "file") {
      const file = await handle.getFile();
      out.push({
        name,
        path: entryPath,
        kind: "file",
        size: file.size,
        modifiedAt: new Date(file.lastModified).toISOString(),
      });
    } else {
      out.push({ name, path: entryPath, kind: "directory" });
    }
  }
  return out;
}

async function readSnippet(handle: FileHandleLike, name: string): Promise<string | undefined> {
  if (!TEXT_LIKE_EXTENSIONS.has(extensionOf(name))) return undefined;
  try {
    const file = await handle.getFile();
    const buf = await file.slice(0, SNIPPET_BYTES).arrayBuffer();
    return new TextDecoder("utf-8", { fatal: false }).decode(buf);
  } catch {
    return undefined;
  }
}

interface SearchOptions {
  query: string;
  extensions: string[];
  limit: number;
}

/**
 * Build a case-insensitive filename matcher. A query containing `*` or `?`
 * is a glob matched against the whole filename (`*.pdf`, `inv-2026-??.xlsx`);
 * anything else is a plain substring match.
 */
export function nameMatcher(query: string): (name: string) => boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return () => true;
  if (!/[*?]/.test(needle)) return (name) => name.toLowerCase().includes(needle);
  const pattern = needle
    .split(/([*?])/)
    .map((part) => (part === "*" ? ".*" : part === "?" ? "." : part.replace(/[.+^${}()|[\]\\]/g, "\\$&")))
    .join("");
  const re = new RegExp(`^${pattern}$`, "i");
  return (name) => re.test(name);
}

/**
 * Build a matcher over a file's location. A query without `/` is tried
 * against the filename and against every directory name on the way down
 * (so `invoices` finds everything inside an `Invoices` folder). A query
 * containing `/` is matched against the full `Folder label/relative/path`.
 * Each is substring or `*`/`?` glob, case-insensitive, as in `nameMatcher`.
 */
export function pathMatcher(
  query: string,
  folderLabel: string,
): (relPath: string) => boolean {
  const needle = query.trim();
  if (!needle) return () => true;
  if (needle.includes("/")) {
    const full = nameMatcher(needle.replace(/^\/+/, ""));
    return (relPath) => full(`${folderLabel}/${relPath}`);
  }
  const seg = nameMatcher(needle);
  return (relPath) => [folderLabel, ...relPath.split("/")].some(seg);
}

/**
 * Recursively search one folder for files whose name, or any folder on
 * their path, matches `query` (substring or `*`/`?` glob, case-insensitive;
 * see `pathMatcher`), optionally narrowed by extension. Stops at
 * `opts.limit` matches or the safety caps above, whichever comes first.
 */
export async function searchFolder(
  root: DirectoryHandleLike,
  folderId: string,
  folderLabel: string,
  opts: SearchOptions,
): Promise<SearchMatch[]> {
  const matchesPath = pathMatcher(opts.query, folderLabel);
  const wantExt = new Set(opts.extensions.map((e) => e.toLowerCase()));
  const matches: SearchMatch[] = [];
  let visited = 0;

  async function walk(dir: DirectoryHandleLike, path: string, depth: number): Promise<void> {
    if (depth > MAX_DEPTH) return;
    for await (const [name, handle] of dir.entries()) {
      if (matches.length >= opts.limit || visited >= MAX_VISITED) return;
      visited += 1;
      const entryPath = joinPath(path, name);
      if (handle.kind === "directory") {
        await walk(handle, entryPath, depth + 1);
        continue;
      }
      if (wantExt.size > 0 && !wantExt.has(extensionOf(name))) continue;
      if (!matchesPath(entryPath)) continue;
      const file = await handle.getFile();
      matches.push({
        folderId,
        folderLabel,
        path: entryPath,
        size: file.size,
        modifiedAt: new Date(file.lastModified).toISOString(),
        snippet: await readSnippet(handle, name),
      });
    }
  }

  await walk(root, "", 0);
  return matches;
}

export interface ReadFileOptions {
  maxBytes: number;
}

/**
 * Read as UTF-8 text. Bytes that are not valid UTF-8 (PDF, zip-based office
 * documents, images, ...) are reported as `binary` with empty `content`
 * rather than decoded lossily into U+FFFD — a mangled copy is worse than
 * none. Binary files reach Hermes through `importFile`, never through here.
 */
export async function readFileContent(
  root: DirectoryHandleLike,
  path: string,
  opts: ReadFileOptions,
): Promise<{ content: string; truncated: boolean; size: number; binary?: boolean }> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  const truncated = file.size > opts.maxBytes;
  const buf = await file.slice(0, opts.maxBytes).arrayBuffer();
  try {
    // A cut at maxBytes may split a multi-byte sequence; trim the tail so a
    // truncated text file is not misreported as binary.
    const probe = truncated ? buf.slice(0, Math.max(0, buf.byteLength - 4)) : buf;
    const content = new TextDecoder("utf-8", { fatal: true }).decode(probe);
    return { content, truncated, size: file.size };
  } catch {
    return { content: "", truncated: false, size: file.size, binary: true };
  }
}

interface ImportResponse {
  asset?: { id?: string; filename?: string };
  sha256?: string;
  size?: number;
  storage_bucket?: string;
  storage_path?: string;
  error?: string;
  detail?: string;
}

/**
 * Copy one file, byte for byte, into the file store via the BFF
 * (`POST /api/files/import`) and return the registry row.
 *
 * The `File` is posted directly as the request body — the browser streams it
 * from disk without loading it into memory, so this works for files of any
 * size. The BFF computes the SHA-256 server-side while streaming to Storage;
 * `verified` means the upload completed and the server returned a hash.
 */
export async function importFile(
  root: DirectoryHandleLike,
  folderId: string,
  folderLabel: string,
  path: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ImportResult> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  const res = await fetchImpl("/api/files/import", {
    method: "POST",
    body: file,
    headers: {
      "content-type": file.type || "application/octet-stream",
      "x-file-name": encodeURIComponent(file.name),
      "x-source-path": encodeURIComponent(path),
      "x-folder-label": encodeURIComponent(folderLabel),
    },
  });
  let body: ImportResponse = {};
  try {
    body = (await res.json()) as ImportResponse;
  } catch {
    // Non-JSON error page; the status code below carries the failure.
  }
  if (!res.ok) {
    throw new Error(body.detail || body.error || `Import failed (HTTP ${res.status}).`);
  }
  if (!body.asset?.id || !body.sha256) {
    throw new Error("Import failed: the file store returned no registry row.");
  }
  return {
    folderId,
    path,
    assetId: body.asset.id,
    filename: body.asset.filename ?? file.name,
    size: body.size ?? file.size,
    sha256: body.sha256,
    storageBucket: body.storage_bucket ?? "",
    storagePath: body.storage_path ?? "",
    verified: true,
  };
}

export async function fileMetadata(
  root: DirectoryHandleLike,
  path: string,
): Promise<{ size: number; modifiedAt: string }> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  return { size: file.size, modifiedAt: new Date(file.lastModified).toISOString() };
}
