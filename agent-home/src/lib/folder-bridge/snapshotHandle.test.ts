import { describe, expect, it } from "vitest";

import {
  listDirectoryEntries,
  readFileContent,
  searchFolder,
} from "@/lib/folder-bridge/fsOps";
import { snapshotFromFileList } from "@/lib/folder-bridge/snapshotHandle";

function fakeFile(relPath: string, text: string): File {
  const name = relPath.split("/").pop() ?? relPath;
  const file = new File([text], name, { lastModified: 1_700_000_000_000 });
  Object.defineProperty(file, "webkitRelativePath", { value: relPath });
  return file;
}

describe("snapshotFromFileList", () => {
  const files = [
    fakeFile("Notes/todo.md", "buy milk"),
    fakeFile("Notes/.DS_Store", "junk"),
    fakeFile("Notes/sub/deep/report.txt", "quarterly"),
    fakeFile("Notes/sub/image.png", "png"),
  ];

  it("names the root after the picked folder and strips it from paths", async () => {
    const root = snapshotFromFileList(files);
    expect(root?.name).toBe("Notes");
    const entries = await listDirectoryEntries(root!, "");
    expect(entries.map((e) => `${e.kind}:${e.path}`).sort()).toEqual([
      "directory:sub",
      "file:todo.md",
    ]);
  });

  it("supports nested resolution, search and read through fsOps", async () => {
    const root = snapshotFromFileList(files)!;
    const nested = await listDirectoryEntries(root, "sub/deep");
    expect(nested).toEqual([
      expect.objectContaining({ name: "report.txt", path: "sub/deep/report.txt", size: 9 }),
    ]);
    const matches = await searchFolder(root, "f1", "Notes", {
      query: "report",
      extensions: [],
      limit: 10,
    });
    expect(matches).toHaveLength(1);
    expect(matches[0].snippet).toBe("quarterly");
    const read = await readFileContent(root, "todo.md", { maxBytes: 100 });
    expect(read).toEqual({ content: "buy milk", truncated: false, size: 8 });
  });

  it("rejects unknown paths and reports permission as granted", async () => {
    const root = snapshotFromFileList(files)!;
    await expect(root.getDirectoryHandle("nope")).rejects.toThrow();
    await expect(root.getFileHandle("sub")).rejects.toThrow();
    await expect(root.queryPermission()).resolves.toBe("granted");
  });

  it("returns null for an empty selection", () => {
    expect(snapshotFromFileList([])).toBeNull();
  });
});
