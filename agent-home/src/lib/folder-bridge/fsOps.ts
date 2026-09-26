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
  received?: number;
  error?: string;
  detail?: string;
}

/**
 * Chunked import. Browsers abort any single request that runs long enough
 * (Safari kills a ~1 GiB POST at ~300 s on every network tried), so files
 * above the threshold go up in sequential slices the BFF appends to a spool
 * file; a final empty request at offset == total makes it stream the
 * assembled file to Storage and register it. Every request is short, every
 * `{received}` response is the server's authoritative byte count (lost
 * responses resync via 409 instead of re-sending), and each chunk retries
 * with backoff — so a killed connection costs one chunk, not the import.
 */
const DEFAULT_IMPORT_CHUNK_SIZE = 16 * 1024 * 1024;
let importChunkSize = DEFAULT_IMPORT_CHUNK_SIZE;
const IMPORT_CHUNK_MAX_ATTEMPTS = 4;

/** Test-only: override the chunk size so multi-chunk paths run on tiny files. */
export function __setImportChunkSizeForTest(value: number | undefined): void {
  importChunkSize = value ?? DEFAULT_IMPORT_CHUNK_SIZE;
}

function newImportId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Reported as bytes leave this tab's network stack, throttled by the caller. */
export type ImportProgressCallback = (sent: number, total: number) => void;

/**
 * Whether `fetch` in this browser can actually send a `ReadableStream`
 * request body. **Do not infer this from browser identity** — WebKit
 * (Safari, and any WKWebView-based browser) throws `NotSupportedError:
 * "ReadableStream uploading is not supported"` for *any* streaming body,
 * unconditionally, regardless of file size or the `duplex` option; other
 * engines support it fine. This is the standard feature-detection idiom
 * (the browser only sets a `Content-Type` header on a streaming body if it
 * *doesn't* understand streaming bodies, and only reads `duplex` if it does).
 * Cached after first call; `undefined` means detection itself threw
 * (treated as unsupported — never break the fallback path).
 */
let streamingBodySupport: boolean | undefined;

export function supportsStreamingRequestBody(): boolean {
  if (streamingBodySupport !== undefined) return streamingBodySupport;
  try {
    let duplexUsed = false;
    const req = new Request("https://example.invalid", {
      method: "POST",
      body: new ReadableStream(),
      get duplex() {
        duplexUsed = true;
        return "half";
      },
    } as RequestInit);
    streamingBodySupport = duplexUsed && !req.headers.has("Content-Type");
  } catch {
    streamingBodySupport = false;
  }
  return streamingBodySupport;
}

/** Test-only: force the next `supportsStreamingRequestBody()` result. */
export function __setStreamingBodySupportForTest(value: boolean | undefined): void {
  streamingBodySupport = value;
}

/**
 * Copy one file, byte for byte, into the file store via the BFF
 * (`POST /api/files/import`) and return the registry row.
 *
 * Files larger than the chunk threshold go up as sequential chunk requests
 * (see `importFileChunked`) — no single request runs long enough to hit the
 * ~300 s request watchdog browsers impose, `onProgress` reflects
 * server-acknowledged bytes, and a dropped connection retries one chunk,
 * not the whole file.
 *
 * Below the threshold it's a single POST: when the browser supports
 * streaming request bodies, the file is posted as a `ReadableStream` (not
 * the bare `File`) so upload progress is observable — the browser only pulls
 * the next chunk once the network is ready to accept it (fetch applies real
 * backpressure to a streaming body), so counting bytes as they are *read* is
 * an accurate proxy for bytes actually sent. Otherwise (Safari/WebKit — see
 * `supportsStreamingRequestBody`) it falls back to posting the `File`
 * directly, which every browser supports; that path has no live progress
 * (`onProgress` fires only at 0% and 100%) but still streams from disk with
 * no extra memory cost. The BFF computes the SHA-256 server-side while
 * streaming to Storage; `verified` means the upload completed and the
 * server returned a hash.
 */
export async function importFile(
  root: DirectoryHandleLike,
  folderId: string,
  folderLabel: string,
  path: string,
  fetchImpl: typeof fetch = fetch,
  onProgress?: ImportProgressCallback,
): Promise<ImportResult> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  onProgress?.(0, file.size);

  if (file.size > importChunkSize) {
    return importFileChunked(file, folderId, path, folderLabel, fetchImpl, onProgress);
  }

  const canStream = supportsStreamingRequestBody();
  let sent = 0;
  const requestBody = canStream
    ? file.stream().pipeThrough(
        new TransformStream<Uint8Array, Uint8Array>({
          transform(chunk, controller) {
            sent += chunk.byteLength;
            onProgress?.(sent, file.size);
            controller.enqueue(chunk);
          },
        }),
      )
    : file;

  const res = await fetchImpl("/api/files/import", {
    method: "POST",
    body: requestBody,
    // Required by the fetch spec for a streaming request body; irrelevant
    // (and untyped) for a plain File/Blob body, so only set it when needed.
    ...(canStream ? { duplex: "half" } : {}),
    headers: {
      "content-type": file.type || "application/octet-stream",
      "x-file-name": encodeURIComponent(file.name),
      "x-source-path": encodeURIComponent(path),
      "x-folder-label": encodeURIComponent(folderLabel),
      "content-length": String(file.size),
    },
    // TS's DOM lib doesn't type `duplex` yet, though it's required by the
    // fetch spec for a streaming request body and every relevant browser
    // implements it.
  } as RequestInit);
  onProgress?.(file.size, file.size);
  const body = await readImportResponse(res);
  if (!res.ok) {
    throw new Error(body.detail || body.error || `Import failed (HTTP ${res.status}).`);
  }
  return importResultFromBody(body, folderId, path, file);
}

async function readImportResponse(res: Response): Promise<ImportResponse> {
  try {
    return (await res.json()) as ImportResponse;
  } catch {
    // Non-JSON error page; the status code carries the failure.
    return {};
  }
}

function importResultFromBody(
  body: ImportResponse,
  folderId: string,
  path: string,
  file: File,
): ImportResult {
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

/**
 * Upload `file` in importChunkSize slices. `offset` is always the count
 * the server has confirmed (`received`), so retries and resyncs never
 * duplicate bytes: a 409 means "your offset is stale — here's the truth".
 */
async function importFileChunked(
  file: File,
  folderId: string,
  path: string,
  folderLabel: string,
  fetchImpl: typeof fetch,
  onProgress?: ImportProgressCallback,
): Promise<ImportResult> {
  const importId = newImportId();
  const chunkHeaders = (offset: number): Record<string, string> => ({
    "content-type": file.type || "application/octet-stream",
    "x-file-name": encodeURIComponent(file.name),
    "x-source-path": encodeURIComponent(path),
    "x-folder-label": encodeURIComponent(folderLabel),
    "x-import-id": importId,
    "x-import-offset": String(offset),
    "x-import-total": String(file.size),
  });

  const post = async (
    offset: number,
    body: Blob | null,
  ): Promise<{ res: Response; data: ImportResponse }> => {
    const res = await fetchImpl("/api/files/import", {
      method: "POST",
      ...(body ? { body } : {}),
      headers: chunkHeaders(offset),
    });
    return { res, data: await readImportResponse(res) };
  };

  let offset = 0;
  while (offset < file.size) {
    const end = Math.min(offset + importChunkSize, file.size);
    for (let attempt = 0; ; attempt++) {
      try {
        const { res, data } = await post(offset, file.slice(offset, end));
        if (res.status === 409 && typeof data.received === "number") {
          offset = data.received; // resync to the server's byte count
          break;
        }
        if (!res.ok) {
          throw new Error(
            data.detail || data.error || `Import failed (HTTP ${res.status}).`,
          );
        }
        offset = typeof data.received === "number" ? data.received : end;
        break;
      } catch (err) {
        if (attempt + 1 >= IMPORT_CHUNK_MAX_ATTEMPTS) throw err;
        await sleep(500 * 2 ** attempt);
      }
    }
    onProgress?.(Math.min(offset, file.size), file.size);
  }

  // Finalize: empty request at offset == total → server streams the spool to
  // Storage and registers the asset. Idempotent server-side, so it can be
  // retried like any chunk.
  for (let attempt = 0; ; attempt++) {
    try {
      const { res, data } = await post(file.size, null);
      if (res.status === 409 && typeof data.received === "number") {
        if (data.received >= file.size) continue;
        throw new Error(
          `Import failed: the server only received ${data.received} of ${file.size} bytes.`,
        );
      }
      if (!res.ok) {
        throw new Error(
          data.detail || data.error || `Import failed (HTTP ${res.status}).`,
        );
      }
      onProgress?.(file.size, file.size);
      return importResultFromBody(data, folderId, path, file);
    } catch (err) {
      if (attempt + 1 >= IMPORT_CHUNK_MAX_ATTEMPTS) throw err;
      await sleep(500 * 2 ** attempt);
    }
  }
}

export async function fileMetadata(
  root: DirectoryHandleLike,
  path: string,
): Promise<{ size: number; modifiedAt: string }> {
  const handle = await resolveFile(root, path);
  const file = await handle.getFile();
  return { size: file.size, modifiedAt: new Date(file.lastModified).toISOString() };
}
