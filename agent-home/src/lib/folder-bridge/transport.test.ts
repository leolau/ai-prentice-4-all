import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { searchFolder } from "@/lib/folder-bridge/fsOps";
import type { SearchMatch } from "@/lib/folder-bridge/types";
import {
  __resetTransportForTest,
  connectFolderBridge,
  disconnectFolderBridge,
} from "@/lib/folder-bridge/transport";

// transport.ts dials `window.location` + `WebSocket` directly; the node test
// env has neither, so both are stubbed. Handles/fsOps are mocked so a command
// can be held open as long as the test needs.
vi.mock("@/lib/folder-bridge/fsOps", () => ({
  fileMetadata: vi.fn(),
  importFile: vi.fn(),
  listDirectoryEntries: vi.fn(),
  readFileContent: vi.fn(),
  searchFolder: vi.fn(),
}));
vi.mock("@/lib/folder-bridge/handles", () => ({
  getFolderHandle: vi.fn(() => ({})),
  getFolderLabel: vi.fn(() => "f"),
  isFolderTrusted: vi.fn(() => true),
  listFolders: vi.fn(() => [
    { id: "f1", label: "f", permission: "granted", trusted: true },
  ]),
}));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.onclose?.();
  }
}

function frames(sock: FakeWebSocket): Record<string, unknown>[] {
  return sock.sent.map((f) => JSON.parse(f) as Record<string, unknown>);
}

async function connectedSocket(): Promise<FakeWebSocket> {
  connectFolderBridge();
  await vi.advanceTimersByTimeAsync(0);
  const sock = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  sock.onopen?.();
  return sock;
}

describe("folder-bridge transport keepalive", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    __resetTransportForTest();
    vi.stubGlobal("window", { location: { protocol: "https:", host: "home.test" } });
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ ticket: "t" }) })),
    );
  });

  afterEach(() => {
    disconnectFolderBridge();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("pings progress while a command is in flight, then stops", async () => {
    const gate: { resolve?: (v: SearchMatch[]) => void } = {};
    vi.mocked(searchFolder).mockImplementation(
      () =>
        new Promise<SearchMatch[]>((resolve) => {
          gate.resolve = resolve;
        }),
    );
    const sock = await connectedSocket();
    sock.onmessage?.({
      data: JSON.stringify({
        type: "cmd",
        id: "c1",
        command: {
          type: "searchFiles",
          folderIds: ["f1"],
          query: "x",
          extensions: [],
          limit: 5,
        },
      }),
    });

    await vi.advanceTimersByTimeAsync(25_000);
    const pings = frames(sock).filter((m) => m.type === "progress");
    expect(pings.length).toBeGreaterThanOrEqual(2);
    expect(pings[0].id).toBe("c1");
    expect(frames(sock).some((m) => m.type === "result")).toBe(false);

    gate.resolve?.([]);
    await vi.advanceTimersByTimeAsync(0);
    const results = frames(sock).filter((m) => m.type === "result");
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ id: "c1", ok: true });

    const pingsAfter = pings.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(frames(sock).filter((m) => m.type === "progress")).toHaveLength(pingsAfter);
  });

  it("fast commands send a result with no progress pings", async () => {
    const sock = await connectedSocket();
    sock.onmessage?.({
      data: JSON.stringify({ type: "cmd", id: "c2", command: { type: "listFolders" } }),
    });
    await vi.advanceTimersByTimeAsync(0);
    const all = frames(sock);
    expect(all.filter((m) => m.type === "result")).toHaveLength(1);
    expect(all.some((m) => m.type === "progress")).toBe(false);
  });
});
