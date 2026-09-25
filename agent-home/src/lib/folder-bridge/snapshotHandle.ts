/**
 * Fallback for browsers without `showDirectoryPicker()` (Safari, Firefox):
 * a `<input type="file" webkitdirectory>` selection yields a flat `FileList`
 * whose entries carry `webkitRelativePath` ("Root/sub/file.txt"). This
 * module folds that list into a tree that implements the same minimal
 * `FileSystemDirectoryHandle` / `FileSystemFileHandle` surface `fsOps.ts`
 * uses, so list/search/read work unchanged.
 *
 * Differences from a real handle: the directory *listing* is a snapshot
 * taken when the folder was picked (files added later are invisible until
 * the folder is re-added), and there is nothing to persist across a reload —
 * the user re-picks the folder instead of re-approving it. File bytes are
 * still read lazily from disk at call time.
 */
import type { DirectoryHandleLike, FileHandleLike } from "@/lib/folder-bridge/types";

export class SnapshotFileHandle implements FileHandleLike {
  readonly kind = "file" as const;
  constructor(
    readonly name: string,
    private readonly file: File,
  ) {}

  async getFile(): Promise<File> {
    return this.file;
  }
}

export class SnapshotDirectoryHandle implements DirectoryHandleLike {
  readonly kind = "directory" as const;
  private readonly children = new Map<string, SnapshotDirectoryHandle | SnapshotFileHandle>();

  constructor(readonly name: string) {}

  /** Insert `file` at `segments` (relative to this directory), creating intermediate dirs. */
  insert(segments: string[], file: File): void {
    const [head, ...rest] = segments;
    if (!head) return;
    if (rest.length === 0) {
      this.children.set(head, new SnapshotFileHandle(head, file));
      return;
    }
    let child = this.children.get(head);
    if (!child || child.kind !== "directory") {
      child = new SnapshotDirectoryHandle(head);
      this.children.set(head, child);
    }
    child.insert(rest, file);
  }

  async *entries(): AsyncIterableIterator<
    [string, DirectoryHandleLike | FileHandleLike]
  > {
    for (const entry of this.children) yield entry;
  }

  async getDirectoryHandle(name: string): Promise<DirectoryHandleLike> {
    const child = this.children.get(name);
    if (!child || child.kind !== "directory") {
      throw new DOMException(`No directory named '${name}'.`, "NotFoundError");
    }
    return child;
  }

  async getFileHandle(name: string): Promise<FileHandleLike> {
    const child = this.children.get(name);
    if (!child || child.kind !== "file") {
      throw new DOMException(`No file named '${name}'.`, "NotFoundError");
    }
    return child;
  }

  async queryPermission(): Promise<PermissionState> {
    return "granted";
  }

  async requestPermission(): Promise<PermissionState> {
    return "granted";
  }
}

/**
 * Build a directory handle from a `webkitdirectory` selection. The first
 * path segment (the picked folder's own name) becomes the root's name and
 * is stripped from every entry so paths match what a real handle reports.
 */
export function snapshotFromFileList(files: Iterable<File>): SnapshotDirectoryHandle | null {
  let root: SnapshotDirectoryHandle | null = null;
  for (const file of files) {
    const rel = file.webkitRelativePath || file.name;
    const segments = rel.split("/").filter(Boolean);
    if (segments.length === 0) continue;
    // Finder writes .DS_Store into every folder; nobody wants it in results.
    if (segments[segments.length - 1] === ".DS_Store") continue;
    const rootName = segments.length > 1 ? segments[0] : "";
    if (!root) root = new SnapshotDirectoryHandle(rootName || "Folder");
    root.insert(segments.length > 1 ? segments.slice(1) : segments, file);
  }
  return root;
}

/**
 * Open the browser's directory chooser via a transient `<input
 * type="file" webkitdirectory>`. Must be called from a user gesture.
 * Resolves null if the user cancels.
 */
export function pickDirectoryViaInput(): Promise<SnapshotDirectoryHandle | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.setAttribute("webkitdirectory", "");
    input.setAttribute("directory", "");
    input.multiple = true;
    input.style.display = "none";
    let settled = false;
    const finish = (value: SnapshotDirectoryHandle | null) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("focus", onFocus);
      input.remove();
      resolve(value);
    };
    // Safari fires no `cancel` event; when the dialog closes without a
    // selection the window regains focus and `change` never comes.
    const onFocus = () => {
      setTimeout(() => {
        if (!settled && (!input.files || input.files.length === 0)) finish(null);
      }, 500);
    };
    input.addEventListener("change", () => {
      finish(input.files ? snapshotFromFileList(Array.from(input.files)) : null);
    });
    input.addEventListener("cancel", () => finish(null));
    window.addEventListener("focus", onFocus);
    document.body.appendChild(input);
    input.click();
  });
}
