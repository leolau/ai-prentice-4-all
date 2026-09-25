import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetRegistryForTest,
  addFolder,
  getFolderHandle,
  restoreFolders,
  setFolderTrusted,
} from "@/lib/folder-bridge/handles";

vi.mock("@/lib/folder-bridge/snapshotHandle", async (importOriginal) => {
  const mod =
    await importOriginal<typeof import("@/lib/folder-bridge/snapshotHandle")>();
  return {
    ...mod,
    pickDirectoryViaInput: async () => {
      const file = new File(["hi"], "a.txt");
      Object.defineProperty(file, "webkitRelativePath", { value: "Inv/a.txt" });
      return mod.snapshotFromFileList([file]);
    },
  };
});

describe("restoreFolders after in-app navigation", () => {
  beforeEach(() => {
    __resetRegistryForTest();
    // Safari-like: no showDirectoryPicker, but webkitdirectory inputs exist.
    vi.stubGlobal("window", {});
    vi.stubGlobal("document", {
      createElement: () => ({ webkitdirectory: false }),
    });
  });

  it("keeps live snapshot folders and their trust flag when the page remounts", async () => {
    const record = (await addFolder())!;
    setFolderTrusted(record.id, true);
    const handle = getFolderHandle(record.id);

    const shown = await restoreFolders();

    expect(shown).toEqual([{ ...record, trusted: true }]);
    expect(getFolderHandle(record.id)).toBe(handle);
  });

  it("returns nothing when no folder is live and nothing is persisted", async () => {
    expect(await restoreFolders()).toEqual([]);
  });
});
