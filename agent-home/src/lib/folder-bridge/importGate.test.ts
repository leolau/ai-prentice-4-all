import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetRegistryForTest,
  addFolder,
  listFolders,
  setFolderTrusted,
} from "@/lib/folder-bridge/handles";
import { runCommand } from "@/lib/folder-bridge/transport";

const importSpy = vi.hoisted(() => vi.fn());

vi.mock("@/lib/folder-bridge/fsOps", async (importOriginal) => {
  const mod =
    await importOriginal<typeof import("@/lib/folder-bridge/fsOps")>();
  return { ...mod, importFile: importSpy };
});

vi.mock("@/lib/folder-bridge/snapshotHandle", async (importOriginal) => {
  const mod =
    await importOriginal<typeof import("@/lib/folder-bridge/snapshotHandle")>();
  return {
    ...mod,
    pickDirectoryViaInput: async () =>
      mod.snapshotFromFileList([
        fakeFile("Inv/a.pdf", new Uint8Array([0x25, 0xff])),
      ]),
  };
});

function fakeFile(relPath: string, bytes: Uint8Array | string): File {
  const name = relPath.split("/").pop() ?? relPath;
  const file = new File([bytes as BlobPart], name);
  Object.defineProperty(file, "webkitRelativePath", { value: relPath });
  return file;
}

/** Register a snapshot folder the same way addFolder does in Safari mode. */
async function registerSnapshotFolder(): Promise<string> {
  const record = await addFolder();
  return record!.id;
}

describe("importFile command gating", () => {
  beforeEach(() => {
    __resetRegistryForTest();
    importSpy.mockReset();
    importSpy.mockResolvedValue({
      folderId: "x",
      path: "a.pdf",
      assetId: "asset-1",
      filename: "a.pdf",
      size: 2,
      sha256: "ab",
      storageBucket: "b",
      storagePath: "p",
      verified: true,
    });
    // Safari-like: no showDirectoryPicker, but webkitdirectory inputs exist.
    vi.stubGlobal("window", {});
    vi.stubGlobal("document", {
      createElement: () => ({ webkitdirectory: false }),
    });
  });

  it("folders start untrusted and listFolders reports the flag", async () => {
    const id = await registerSnapshotFolder();
    expect(listFolders()).toEqual([
      expect.objectContaining({ id, trusted: false }),
    ]);
    expect(setFolderTrusted(id, true)?.trusted).toBe(true);
    expect(listFolders()[0].trusted).toBe(true);
    expect(setFolderTrusted("nope", true)).toBeNull();
  });

  it("refuses an unapproved import from an untrusted folder without touching the file", async () => {
    const id = await registerSnapshotFolder();
    const result = await runCommand({
      type: "importFile",
      folderId: id,
      path: "a.pdf",
      approved: false,
    });
    expect(result).toMatchObject({ ok: false, needsApproval: true });
    expect(importSpy).not.toHaveBeenCalled();
  });

  it("allows an unapproved import once the folder is trusted", async () => {
    const id = await registerSnapshotFolder();
    setFolderTrusted(id, true);
    const result = await runCommand({
      type: "importFile",
      folderId: id,
      path: "a.pdf",
      approved: false,
    });
    expect(result).toMatchObject({
      ok: true,
      assetId: "asset-1",
      verified: true,
    });
    expect(importSpy).toHaveBeenCalledWith(
      expect.anything(),
      id,
      "Inv",
      "a.pdf",
    );
  });

  it("allows an approved import from an untrusted folder", async () => {
    const id = await registerSnapshotFolder();
    const result = await runCommand({
      type: "importFile",
      folderId: id,
      path: "a.pdf",
      approved: true,
    });
    expect(result).toMatchObject({ ok: true, assetId: "asset-1" });
  });

  it("readFile reports binary files with an import hint rather than U+FFFD text", async () => {
    const id = await registerSnapshotFolder();
    const result = await runCommand({
      type: "readFile",
      folderId: id,
      path: "a.pdf",
      encoding: "utf-8",
      maxBytes: 1000,
    });
    expect(result).toMatchObject({ ok: false, binary: true, size: 2 });
    expect(String(result.detail)).toContain("folder_bridge_import_file");
  });
});
