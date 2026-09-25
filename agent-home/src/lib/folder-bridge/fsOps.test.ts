import { describe, expect, it } from "vitest";

import {
  fileMetadata,
  listDirectoryEntries,
  nameMatcher,
  pathMatcher,
  readFileContent,
  resolveDirectory,
  resolveFile,
  searchFolder,
} from "@/lib/folder-bridge/fsOps";

/**
 * A minimal in-memory stand-in for FileSystemDirectoryHandle/FileHandle —
 * just enough of the API's shape (`entries()`, `getDirectoryHandle`,
 * `getFileHandle`, `getFile().slice().arrayBuffer()`) for fsOps to run
 * against, with no real browser or disk involved.
 */
type FakeTree = { [name: string]: string | FakeTree };

function encode(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer as ArrayBuffer;
}

class FakeFile {
  constructor(private text: string, public lastModified = 1_700_000_000_000) {}
  get size(): number {
    return new TextEncoder().encode(this.text).length;
  }
  async arrayBuffer(): Promise<ArrayBuffer> {
    return encode(this.text);
  }
  slice(start: number, end: number): FakeFile {
    const bytes = new TextEncoder().encode(this.text).slice(start, end);
    return new FakeFile(new TextDecoder().decode(bytes), this.lastModified);
  }
}

class FakeFileHandle {
  kind = "file" as const;
  constructor(private text: string) {}
  async getFile(): Promise<FakeFile> {
    return new FakeFile(this.text);
  }
}

class FakeDirHandle {
  kind = "directory" as const;
  constructor(private tree: FakeTree) {}

  async *entries(): AsyncGenerator<[string, FakeDirHandle | FakeFileHandle]> {
    for (const [name, value] of Object.entries(this.tree)) {
      yield [
        name,
        typeof value === "string" ? new FakeFileHandle(value) : new FakeDirHandle(value),
      ];
    }
  }

  async getDirectoryHandle(name: string): Promise<FakeDirHandle> {
    const value = this.tree[name];
    if (typeof value !== "object") throw new Error(`Not a directory: ${name}`);
    return new FakeDirHandle(value);
  }

  async getFileHandle(name: string): Promise<FakeFileHandle> {
    const value = this.tree[name];
    if (typeof value !== "string") throw new Error(`Not a file: ${name}`);
    return new FakeFileHandle(value);
  }
}

// Cast through unknown: the fake implements the subset fsOps actually calls,
// not the full lib.dom FileSystemDirectoryHandle interface.
function fakeRoot(tree: FakeTree): FileSystemDirectoryHandle {
  return new FakeDirHandle(tree) as unknown as FileSystemDirectoryHandle;
}

const TREE: FakeTree = {
  "notes.md": "# Budget notes\nQ1 budget is tight this year.",
  "image.png": "not-real-binary-but-fine-for-tests",
  Reports: {
    "Q1.txt": "quarterly report body",
    "Q2.txt": "another report",
  },
  Archive: {
    Old: {
      "ancient.txt": "very old file",
    },
  },
};

describe("resolveDirectory / resolveFile", () => {
  it("navigates nested paths", async () => {
    const root = fakeRoot(TREE);
    const reports = await resolveDirectory(root, "Reports");
    expect(reports).toBeInstanceOf(FakeDirHandle);
    const file = await resolveFile(root, "Reports/Q1.txt");
    const content = await (await file.getFile()).arrayBuffer();
    expect(new TextDecoder().decode(content)).toBe("quarterly report body");
  });

  it("rejects an empty file path", async () => {
    await expect(resolveFile(fakeRoot(TREE), "")).rejects.toThrow("Empty file path");
  });
});

describe("listDirectoryEntries", () => {
  it("lists the root with file sizes and directory markers", async () => {
    const entries = await listDirectoryEntries(fakeRoot(TREE), "");
    const byName = Object.fromEntries(entries.map((e) => [e.name, e]));
    expect(byName["notes.md"].kind).toBe("file");
    expect(byName["notes.md"].size).toBeGreaterThan(0);
    expect(byName["Reports"].kind).toBe("directory");
    expect(byName["Reports"].size).toBeUndefined();
  });

  it("lists a nested directory using its relative path", async () => {
    const entries = await listDirectoryEntries(fakeRoot(TREE), "Reports");
    expect(entries.map((e) => e.path).sort()).toEqual(["Reports/Q1.txt", "Reports/Q2.txt"]);
  });
});

describe("searchFolder", () => {
  it("matches by filename, case-insensitively, recursively", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "q1",
      extensions: [],
      limit: 50,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].path).toBe("Reports/Q1.txt");
    expect(matches[0].folderId).toBe("f1");
    expect(matches[0].folderLabel).toBe("Work Docs");
  });

  it("treats * and ? as a glob over the whole filename", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "*.txt",
      extensions: [],
      limit: 50,
    });
    expect(matches.map((m) => m.path).sort()).toEqual([
      "Archive/Old/ancient.txt",
      "Reports/Q1.txt",
      "Reports/Q2.txt",
    ]);
    const one = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "q?.TXT",
      extensions: [],
      limit: 50,
    });
    expect(one.map((m) => m.path).sort()).toEqual(["Reports/Q1.txt", "Reports/Q2.txt"]);
  });

  it("narrows by extension", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "",
      extensions: [".txt"],
      limit: 50,
    });
    expect(matches.map((m) => m.path).sort()).toEqual([
      "Archive/Old/ancient.txt",
      "Reports/Q1.txt",
      "Reports/Q2.txt",
    ]);
  });

  it("attaches a text snippet for text-like extensions only", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "notes",
      extensions: [],
      limit: 50,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].snippet).toContain("Budget notes");
  });

  it("omits a snippet for a non-text-like extension", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "image",
      extensions: [],
      limit: 50,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].snippet).toBeUndefined();
  });

  it("stops at the requested limit", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "",
      extensions: [".txt"],
      limit: 1,
    });
    expect(matches).toHaveLength(1);
  });
});

describe("pathMatcher", () => {
  it("matches folder names on the path as well as the filename", () => {
    const m = pathMatcher("old", "Work Docs");
    expect(m("Archive/Old/ancient.txt")).toBe(true);
    expect(m("Reports/Q1.txt")).toBe(false);
    expect(pathMatcher("work", "Work Docs")("Reports/Q1.txt")).toBe(true);
    expect(pathMatcher("*.txt", "Work Docs")("Reports/Q1.txt")).toBe(true);
    expect(pathMatcher("arch*", "Work Docs")("Archive/Old/ancient.txt")).toBe(true);
  });

  it("matches the whole label/path when the query contains a slash", () => {
    const m = pathMatcher("*/reports/*.txt", "Work Docs");
    expect(m("Reports/Q1.txt")).toBe(true);
    expect(m("Archive/Old/ancient.txt")).toBe(false);
    expect(pathMatcher("docs/rep", "Work Docs")("Reports/Q1.txt")).toBe(true);
    expect(pathMatcher("/Work Docs/Reports/Q1.txt", "Work Docs")("Reports/Q1.txt")).toBe(true);
    expect(pathMatcher("", "Work Docs")("x")).toBe(true);
  });
});

describe("searchFolder by folder name", () => {
  it("finds files inside a matching directory", async () => {
    const matches = await searchFolder(fakeRoot(TREE), "f1", "Work Docs", {
      query: "reports",
      extensions: [],
      limit: 50,
    });
    expect(matches.map((m) => m.path).sort()).toEqual(["Reports/Q1.txt", "Reports/Q2.txt"]);
  });
});

describe("nameMatcher", () => {
  it("substring when no wildcard, anchored glob otherwise, regex chars literal", () => {
    expect(nameMatcher("voice")("Invoice.PDF")).toBe(true);
    expect(nameMatcher("*.pdf")("Invoice.PDF")).toBe(true);
    expect(nameMatcher("*.pdf")("Invoice.pdf.bak")).toBe(false);
    expect(nameMatcher("inv-2026-??.xlsx")("inv-2026-09.xlsx")).toBe(true);
    expect(nameMatcher("a+b*")("a+b.txt")).toBe(true);
    expect(nameMatcher("a+b*")("aab.txt")).toBe(false);
    expect(nameMatcher("  ")("anything")).toBe(true);
  });
});

describe("readFileContent", () => {
  it("reads the whole file when under maxBytes", async () => {
    const result = await readFileContent(fakeRoot(TREE), "Reports/Q1.txt", { maxBytes: 1000 });
    expect(result.content).toBe("quarterly report body");
    expect(result.truncated).toBe(false);
  });

  it("truncates and reports it when the file exceeds maxBytes", async () => {
    const result = await readFileContent(fakeRoot(TREE), "notes.md", { maxBytes: 5 });
    expect(result.truncated).toBe(true);
    expect(result.content.length).toBeLessThanOrEqual(5);
    expect(result.size).toBeGreaterThan(5);
  });
});

describe("fileMetadata", () => {
  it("reports size and a modified timestamp without reading content", async () => {
    const meta = await fileMetadata(fakeRoot(TREE), "Reports/Q2.txt");
    expect(meta.size).toBe("another report".length);
    expect(typeof meta.modifiedAt).toBe("string");
  });
});
