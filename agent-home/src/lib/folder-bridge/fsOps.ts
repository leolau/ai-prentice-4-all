/**
 * File System Access API operations, scoped to one already-approved
 * `FileSystemDirectoryHandle`. Pure with respect to everything except the
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
import type { DirectoryEntryInfo, SearchMatch } from "@/lib/folder-bridge/types";

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
  root: FileSystemDirectoryHandle,
  path: string,
): Promise<FileSystemDirectoryHandle> {
  let dir = root;
  for (const segment of path.split("/").filter(Boolean)) {
    dir = await dir.getDirectoryHandle(segment);
  }
  return dir;
}

/** Navigate a `/`-separated relative path to a file handle. */
export async function resolveFile(
  root: FileSystemDirectoryHandle,
  path: string,
): Promise<FileSystemFileHandle> {
  const parts = path.split("/").filter(Boolean);
  const fileName = parts.pop();
  if (!fileName) throw new Error("Empty file path.");
  const dir = parts.length ? await resolveDirectory(root, parts.join("/")) : root;
  return dir.getFileHandle(fileName);
}

/** One directory's immediate contents (not recursive). */
export async function listDirectoryEntries(
  root: FileSystemDirectoryHandle,
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

async function readSnippet(handle: FileSystemFileHandle, name: string): Promise<string | undefined> {
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
 * Recursively search one folder for files whose name contains `query`
 * (case-insensitive), optionally narrowed by extension. Stops at
 * `opts.limit` matches or the safety caps above, whichever comes first.
 */
export async function searchFolder(
  root: FileSystemDirectoryHandle,
  folderId: string,
  folderLabel: string,
  opts: SearchOptions,
): Promise<SearchMatch[]> {
  const needle = opts.query.trim().toLowerCase();
  const wantExt = new Set(opts.extensions.map((e) => e.toLowerCase()));
  const matches: SearchMatch[] = [];
  let visited = 0;

  async function walk(dir: FileSystemDirectoryHandle, path: string, depth: number): Promise<void> {
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
      if (needle && !name.toLowerCase().includes(needle)) continue;
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

export async function readFileContent(
  root: FileSystemDirectoryHandle,
  path: string,
  opts: ReadFileOptions,
): Promise<{ content: string; truncated: boolean; size: number }> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  const truncated = file.size > opts.maxBytes;
  const buf = await file.slice(0, opts.maxBytes).arrayBuffer();
  const content = new TextDecoder("utf-8", { fatal: false }).decode(buf);
  return { content, truncated, size: file.size };
}

export async function fileMetadata(
  root: FileSystemDirectoryHandle,
  path: string,
): Promise<{ size: number; modifiedAt: string }> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  return { size: file.size, modifiedAt: new Date(file.lastModified).toISOString() };
}
