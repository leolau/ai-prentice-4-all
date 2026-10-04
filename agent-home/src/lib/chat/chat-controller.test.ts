import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers, ChatStreamParams } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
  attachChatStream: vi.fn(),
  cancelChatTurn: vi.fn(),
}));

import { ChatController, NEW_KEY } from "@/lib/chat/chat-controller";
import { attachChatStream, cancelChatTurn, streamChatTurn } from "@/lib/chat/stream";
import { STOPPED_NOTE } from "@/lib/chat/turn-activity";
import type { ChatApprovalRequest, ChatMessage } from "@/types";

// ── Harness ─────────────────────────────────────────────────────────

let frames: FrameRequestCallback[] = [];
/** Run one animation frame (the controller's notification point). */
function frame() {
  const run = frames;
  frames = [];
  for (const cb of run) cb(0);
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() {
  for (let i = 0; i < 5; i++) await tick();
  frame();
}

type Responder = (url: string, init?: RequestInit) => unknown;
let routes: [string, Responder][] = [];
let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function route(prefix: string, respond: Responder) {
  routes.unshift([prefix, respond]);
}

function page(messages: ChatMessage[], hasMore = false) {
  return { session_id: "s", messages, has_more: hasMore };
}

const u = (id: number, content = `u${id}`): ChatMessage => ({ id, role: "user", content });
const a = (id: number, content = `a${id}`): ChatMessage => ({ id, role: "assistant", content });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface Turn {
  handlers: ChatStreamHandlers;
  params: ChatStreamParams;
  complete(content?: string, sessionId?: string): void;
  fail(err: unknown): void;
}

/** Arm `streamChatTurn` for the next send; the returned turn drives it. */
function armTurn(): Turn {
  const d = deferred<{ sessionId: string }>();
  const turn = {} as Turn;
  vi.mocked(streamChatTurn).mockImplementationOnce(async (params, handlers) => {
    turn.handlers = handlers;
    turn.params = params;
    params.signal?.addEventListener("abort", () =>
      d.reject(new DOMException("aborted", "AbortError")),
    );
    return d.promise;
  });
  turn.complete = (content = "", sessionId) => {
    const sid = sessionId ?? turn.params.sessionId ?? "";
    turn.handlers.onCompleted?.(content, sid);
    d.resolve({ sessionId: sid });
  };
  turn.fail = (err) => d.reject(err);
  return turn;
}

const urls = (prefix: string) =>
  fetchMock.mock.calls.map((c) => String(c[0])).filter((x) => x.startsWith(prefix));

const shape = (ms: ChatMessage[]) => ms.map((m) => `${m.role}:${m.content}`);

beforeEach(() => {
  frames = [];
  routes = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frames.push(cb);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const hit = routes.find(([p]) => url.startsWith(p));
    const body = hit ? await hit[1](url, init) : {};
    return body instanceof Response ? body : json(body);
  });
  vi.stubGlobal("fetch", fetchMock);
  route("/api/chat/messages", () => page([]));
  route("/api/chat/active", () => ({ runId: null }));
  vi.mocked(streamChatTurn).mockReset();
  vi.mocked(attachChatStream).mockReset();
  vi.mocked(cancelChatTurn).mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ── Paging ──────────────────────────────────────────────────────────

describe("ChatController transcript paging", () => {
  it("returns a stable empty snapshot for an unknown id", () => {
    const c = new ChatController();
    const s = c.getThread("nope");
    expect(s).toBe(c.getThread("nope"));
    expect(s).toMatchObject({ sessionId: "nope", messages: [], status: "idle" });
    expect(c.getThread(null).sessionId).toBeNull();
  });

  it("seeds, then open revalidates the first page keeping row identity", async () => {
    const c = new ChatController({ profile: "work" });
    c.seed("s1", [u(1), { id: 2, role: "tool", content: "{}" }, a(3)], true);
    const seeded = c.getThread("s1");
    expect(seeded.status).toBe("ready");
    expect(seeded.hasMore).toBe(true);
    expect(seeded.messages.map((m) => m.id)).toEqual([1, 3]);

    route("/api/chat/messages", () => page([u(1), a(3), u(4)], true));
    await c.open("s1");
    const s = c.getThread("s1");
    expect(s.messages.map((m) => m.id)).toEqual([1, 3, 4]);
    expect(s.messages[0]).toBe(seeded.messages[0]);
    expect(s.hasMore).toBe(true);
    expect(urls("/api/chat/messages")[0]).toBe(
      "/api/chat/messages?sessionId=s1&visible=1&limit=40&profile=work",
    );
  });

  it("open shows loading for an uncached thread and an error on failure", async () => {
    const c = new ChatController();
    route("/api/chat/messages", () => json({ detail: "nope" }, 500));
    const p = c.open("s1");
    expect(c.getThread("s1").status).toBe("loading");
    await p;
    expect(c.getThread("s1")).toMatchObject({ status: "error", error: "nope" });
  });

  it("loadOlder pages back with before=<oldest id> until has_more is false", async () => {
    const c = new ChatController({ pageSize: 2 });
    route("/api/chat/messages", (url) => {
      if (url.includes("before=5")) return page([u(3), a(4)], true);
      if (url.includes("before=3")) return page([u(1), a(2)], false);
      return page([u(5), a(6)], true);
    });
    await c.open("s1");
    expect(c.getThread("s1").hasMore).toBe(true);
    const p = c.loadOlder("s1");
    expect(c.getThread("s1").loadingOlder).toBe(true);
    // A second call while one is in flight joins it.
    expect(c.loadOlder("s1")).toBe(p);
    await p;
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([3, 4, 5, 6]);
    expect(c.getThread("s1").loadingOlder).toBe(false);
    await c.loadOlder("s1");
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(c.getThread("s1").hasMore).toBe(false);
    const calls = urls("/api/chat/messages").length;
    await c.loadOlder("s1"); // no-op once the history is complete
    expect(urls("/api/chat/messages").length).toBe(calls);
  });

  it("loadAll walks every page", async () => {
    const c = new ChatController({ pageSize: 2 });
    route("/api/chat/messages", (url) => {
      if (url.includes("before=5")) return page([u(3), a(4)], true);
      if (url.includes("before=3")) return page([u(1), a(2)], false);
      return page([u(5), a(6)], true);
    });
    await c.loadAll("s1");
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(urls("/api/chat/messages").filter((x) => x.includes("before")).every((x) =>
      x.includes("limit=500"),
    )).toBe(true);
  });

  it("reload replaces the tail, keeps older pages and keeps hasMore of the older chain", async () => {
    const c = new ChatController({ pageSize: 2 });
    route("/api/chat/messages", (url) =>
      url.includes("before=3") ? page([u(1), a(2)], false) : page([u(3), a(4)], true),
    );
    await c.open("s1");
    await c.loadOlder("s1");
    expect(c.getThread("s1").hasMore).toBe(false);
    route("/api/chat/messages", () => page([a(4), u(5)], true));
    await c.reload("s1");
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3, 4, 5]);
    expect(c.getThread("s1").hasMore).toBe(false);
  });

  it("evict forgets an idle thread", async () => {
    const c = new ChatController();
    c.seed("s1", [u(1)], false);
    c.evict("s1");
    expect(c.getThread("s1").messages).toEqual([]);
  });
});

// ── Turns ───────────────────────────────────────────────────────────

describe("ChatController turns", () => {
  it("send: optimistic row, frame-batched deltas, final row replaces live turn", async () => {
    const settled = vi.fn();
    const c = new ChatController({ profile: "work", onTurnSettled: settled });
    c.seed("s1", [u(1), a(2)], false);
    const threadCb = vi.fn();
    const liveCb = vi.fn();
    const busyCb = vi.fn();
    c.subscribeThread("s1", threadCb);
    c.subscribeLive("s1", liveCb);
    c.subscribeBusy(busyCb);

    const turn = armTurn();
    const sent = c.send("s1", "hello", []);
    const optimistic = c.getThread("s1").messages.at(-1)!;
    expect(optimistic).toMatchObject({ role: "user", content: "hello" });
    expect(optimistic.clientKey).toBeTruthy();
    expect(c.getBusyKeys()).toEqual(["s1"]);
    expect(c.getLive("s1")).toMatchObject({ assistantText: "", phase: "sending" });
    await tick();
    expect(turn.params).toMatchObject({ sessionId: "s1", message: "hello", profile: "work" });
    frame();
    threadCb.mockClear();
    liveCb.mockClear();

    turn.handlers.onAccepted?.("run1", "s1");
    const before = c.getLive("s1");
    for (const d of ["He", "ll", "o ", "th", "ere"]) turn.handlers.onDelta?.(d);
    // Nothing is published until the frame.
    expect(c.getLive("s1")).toBe(before);
    expect(liveCb).not.toHaveBeenCalled();
    frame();
    expect(liveCb).toHaveBeenCalledTimes(1);
    expect(threadCb).not.toHaveBeenCalled();
    expect(c.getLive("s1")).toMatchObject({ assistantText: "Hello there", phase: "streaming" });
    expect(c.getLive("s1")!.activity.runId).toBe("run1");

    liveCb.mockClear();
    route("/api/chat/messages", () => page([u(1), a(2), u(3, "hello"), a(4, "Hello there!")]));
    turn.complete("Hello there!");
    const result = await sent;
    expect(result).toEqual({ ok: true, sessionId: "s1" });
    // Final row appended and live turn gone before the frame publishes both.
    expect(c.getLive("s1")).toBeNull();
    expect(shape(c.getThread("s1").messages).slice(-2)).toEqual([
      "user:hello",
      "assistant:Hello there!",
    ]);
    frame();
    expect(liveCb).toHaveBeenCalledTimes(1);
    expect(threadCb).toHaveBeenCalled();
    expect(c.getBusyKeys()).toEqual([]);
    expect(settled).toHaveBeenCalledWith("s1", "s1");
    // The quiet reload reconciles DB ids and drops the optimistic rows.
    await settle();
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("keeps the settled turn on screen while the server write lags", async () => {
    const c = new ChatController({});
    c.seed("s1", [u(1), a(2)], false);
    const turn = armTurn();
    const sent = c.send("s1", "hello", []);
    await tick();
    route("/api/chat/messages", () => page([u(1), a(2)]));
    turn.complete("Hi!");
    await sent;
    await settle();
    expect(shape(c.getThread("s1").messages)).toEqual([
      "user:u1",
      "assistant:a2",
      "user:hello",
      "assistant:Hi!",
    ]);
    route("/api/chat/messages", () => page([u(1), a(2), u(3, "hello"), a(4, "Hi!")]));
    await c.reload("s1");
    await settle();
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("refuses a second send on a busy key", async () => {
    const c = new ChatController();
    armTurn();
    void c.send("s1", "one", []);
    expect(await c.send("s1", "two", [])).toEqual({ ok: false, sessionId: "s1" });
    expect(shape(c.getThread("s1").messages)).toEqual(["user:one"]);
  });

  it("stop before the ack aborts the request and keeps the user row", async () => {
    const c = new ChatController();
    const turn = armTurn();
    const sent = c.send("s1", "hi", []);
    await tick();
    await c.stop("s1");
    expect(turn.params.signal?.aborted).toBe(true);
    expect(cancelChatTurn).not.toHaveBeenCalled();
    expect(await sent).toEqual({ ok: true, sessionId: "s1" });
    // No empty reply bubble is left behind.
    expect(shape(c.getThread("s1").messages)).toEqual(["user:hi"]);
    expect(c.getLive("s1")).toBeNull();
  });

  it("stop after the ack cancels the run and keeps the partial reply", async () => {
    const c = new ChatController({ profile: "work" });
    const turn = armTurn();
    const sent = c.send("s1", "hi", []);
    await tick();
    turn.handlers.onAccepted?.("run1", "s1");
    turn.handlers.onDelta?.("partial");
    await c.stop("s1");
    expect(cancelChatTurn).toHaveBeenCalledWith({
      sessionId: "s1",
      runId: "run1",
      profile: "work",
    });
    expect(turn.params.signal?.aborted).toBe(false);
    frame();
    expect(c.getLive("s1")).toMatchObject({ phase: "stopping" });
    turn.handlers.onCancelled?.();
    expect(c.getThread("s1").note).toBe(STOPPED_NOTE);
    turn.complete("");
    expect(await sent).toEqual({ ok: true, sessionId: "s1" });
    expect(shape(c.getThread("s1").messages)).toEqual(["user:hi", "assistant:partial"]);
  });

  it("a failed cancel aborts the stream and reports the error", async () => {
    const c = new ChatController();
    const turn = armTurn();
    const sent = c.send("s1", "hi", []);
    await tick();
    turn.handlers.onAccepted?.("run1", "s1");
    vi.mocked(cancelChatTurn).mockRejectedValueOnce(new Error("cannot stop"));
    await c.stop("s1");
    expect(turn.params.signal?.aborted).toBe(true);
    expect(await sent).toMatchObject({ ok: true });
    expect(c.getThread("s1").error).toBe("cannot stop");
  });

  it("resolves an approval and leaves the decision note", async () => {
    const c = new ChatController({ profile: "work" });
    route("/api/chat/approval", () => ({ ok: true }));
    const turn = armTurn();
    void c.send("s1", "book it", []);
    await tick();
    const req: ChatApprovalRequest = {
      runId: "run1",
      toolName: "calendar",
      choices: ["once", "deny"],
    };
    turn.handlers.onApproval?.(req);
    frame();
    expect(c.getLive("s1")).toMatchObject({ approval: req, phase: "waiting_approval" });
    const p = c.resolveApproval("s1", "once");
    frame();
    expect(c.getLive("s1")!.resolvingApproval).toBe(true);
    await p;
    frame();
    const call = fetchMock.mock.calls.find((x) => x[0] === "/api/chat/approval")!;
    expect(JSON.parse(String(call[1].body))).toEqual({
      runId: "run1",
      choice: "once",
      profile: "work",
    });
    expect(c.getLive("s1")).toMatchObject({ approval: null, resolvingApproval: false });
    expect(c.getThread("s1").note).toBe("Approved — running calendar…");
  });

  it("reports a failed approval without clearing the card", async () => {
    const c = new ChatController();
    route("/api/chat/approval", () => json({ detail: "expired" }, 409));
    const turn = armTurn();
    void c.send("s1", "x", []);
    await tick();
    turn.handlers.onApproval?.({ runId: "r", choices: ["once", "deny"] });
    await c.resolveApproval("s1", "deny");
    frame();
    expect(c.getThread("s1").error).toBe("expired");
    expect(c.getLive("s1")!.approval).not.toBeNull();
  });

  it("transport failure: ok=false, optimistic row removed, error set", async () => {
    const settled = vi.fn();
    const c = new ChatController({ onTurnSettled: settled });
    c.seed("s1", [u(1)], false);
    const turn = armTurn();
    const sent = c.send("s1", "lost", []);
    await tick();
    turn.handlers.onDelta?.("half");
    turn.fail(new Error("network down"));
    expect(await sent).toEqual({ ok: false, sessionId: "s1" });
    expect(shape(c.getThread("s1").messages)).toEqual(["user:u1"]);
    expect(c.getThread("s1").error).toBe("network down");
    expect(c.getLive("s1")).toBeNull();
    expect(c.getBusyKeys()).toEqual([]);
    expect(settled).not.toHaveBeenCalled();
  });

  it("a stream error is shown and cleared by the next send", async () => {
    const c = new ChatController();
    const t1 = armTurn();
    const sent = c.send("s1", "a", []);
    await tick();
    t1.handlers.onError?.("The turn failed.");
    t1.complete("");
    await sent;
    expect(c.getThread("s1").error).toBe("The turn failed.");
    armTurn();
    void c.send("s1", "b", []);
    expect(c.getThread("s1").error).toBeNull();
  });

  it("a NEW_KEY turn lands on its new session id when adopting", async () => {
    const settled = vi.fn();
    const c = new ChatController({ adoptLandedSession: true, onTurnSettled: settled });
    const turn = armTurn();
    const sent = c.send(null, "start", []);
    expect(c.getBusyKeys()).toEqual([NEW_KEY]);
    expect(shape(c.getThread(null).messages)).toEqual(["user:start"]);
    await tick();
    turn.handlers.onDelta?.("welcome");
    route("/api/chat/messages", () => page([u(1, "start"), a(2, "welcome")]));
    turn.complete("welcome", "s9");
    expect(await sent).toEqual({ ok: true, sessionId: "s9" });
    expect(c.getThread(null).messages).toEqual([]);
    expect(shape(c.getThread("s9").messages)).toEqual(["user:start", "assistant:welcome"]);
    expect(settled).toHaveBeenCalledWith(NEW_KEY, "s9");
    await settle();
    expect(urls("/api/chat/messages")[0]).toContain("sessionId=s9");
    expect(c.getThread("s9").messages.map((m) => m.id)).toEqual([1, 2]);
  });

  it("a continuation id is adopted only with adoptLandedSession", async () => {
    const main = new ChatController({ adoptLandedSession: true });
    const t1 = armTurn();
    const s1 = main.send("root", "q", []);
    await tick();
    t1.complete("r", "cont");
    expect(await s1).toEqual({ ok: true, sessionId: "cont" });
    expect(shape(main.getThread("cont").messages)).toEqual(["user:q", "assistant:r"]);

    const lead = new ChatController({ adoptLandedSession: false });
    const t2 = armTurn();
    const s2 = lead.send("root", "q", []);
    await tick();
    t2.complete("r", "cont");
    expect(await s2).toEqual({ ok: true, sessionId: "root" });
    expect(shape(lead.getThread("root").messages)).toEqual(["user:q", "assistant:r"]);
    expect(lead.getThread("cont").messages).toEqual([]);
    await settle();
    expect(urls("/api/chat/messages").some((x) => x.includes("sessionId=root"))).toBe(true);
  });

  it("two concurrent sessions do not cross-contaminate", async () => {
    const c = new ChatController();
    const ta = armTurn();
    const sa = c.send("A", "to A", []);
    const tb = armTurn();
    const sb = c.send("B", "to B", []);
    await tick();
    expect(c.getBusyKeys()).toEqual(["A", "B"]);
    ta.handlers.onDelta?.("alpha");
    tb.handlers.onDelta?.("beta");
    frame();
    expect(c.getLive("A")!.assistantText).toBe("alpha");
    expect(c.getLive("B")!.assistantText).toBe("beta");
    ta.complete("alpha!");
    await sa;
    expect(shape(c.getThread("A").messages)).toEqual(["user:to A", "assistant:alpha!"]);
    expect(shape(c.getThread("B").messages)).toEqual(["user:to B"]);
    expect(c.getLive("B")!.assistantText).toBe("beta");
    tb.complete("beta!");
    await sb;
    expect(shape(c.getThread("B").messages)).toEqual(["user:to B", "assistant:beta!"]);
  });

  it("a slow open() answering after a send keeps the optimistic row", async () => {
    const c = new ChatController();
    const slow = deferred<unknown>();
    route("/api/chat/messages", () => slow.promise);
    const opening = c.open("s1");
    const turn = armTurn();
    void c.send("s1", "fresh", []);
    slow.resolve(page([u(1), a(2)]));
    await opening;
    expect(shape(c.getThread("s1").messages)).toEqual(["user:u1", "assistant:a2", "user:fresh"]);
    // It also survives a reload while the turn is still in flight.
    route("/api/chat/messages", () => page([u(1), a(2)]));
    await c.reload("s1");
    expect(shape(c.getThread("s1").messages).at(-1)).toBe("user:fresh");
    turn.complete("ok");
  });

  it("a stale load that resolves after a newer one is dropped", async () => {
    const c = new ChatController();
    const slow = deferred<unknown>();
    route("/api/chat/messages", () => slow.promise);
    const first = c.open("s1");
    route("/api/chat/messages", () => page([u(1), a(2), u(3)]));
    await c.reload("s1");
    slow.resolve(page([u(1)]));
    await first;
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2, 3]);
  });
});

// ── Attach / watch ──────────────────────────────────────────────────

describe("ChatController attach", () => {
  it("attachIfActive streams a run started elsewhere, then reconciles", async () => {
    const settled = vi.fn();
    const c = new ChatController({ profile: "work", onTurnSettled: settled });
    c.seed("s1", [u(1, "from phone")], false);
    route("/api/chat/active", () => ({ runId: "run7" }));
    const d = deferred<void>();
    let handlers!: ChatStreamHandlers;
    vi.mocked(attachChatStream).mockImplementationOnce(async (_p, h) => {
      handlers = h;
      return d.promise;
    });
    const attached = c.attachIfActive("s1");
    await tick();
    expect(urls("/api/chat/active")[0]).toBe("/api/chat/active?sessionId=s1&profile=work");
    expect(vi.mocked(attachChatStream).mock.calls[0][0]).toMatchObject({
      sessionId: "s1",
      runId: "run7",
      profile: "work",
    });
    expect(c.getBusyKeys()).toEqual(["s1"]);
    expect(c.getLive("s1")!.activity).toMatchObject({ runId: "run7", accepted: true });
    // Attaching again while attached is a no-op.
    expect(await c.attachIfActive("s1")).toBe(false);
    handlers.onDelta?.("reply");
    handlers.onCompleted?.("reply", "s1");
    route("/api/chat/messages", () => page([u(1, "from phone"), a(2, "reply")]));
    d.resolve();
    expect(await attached).toBe(true);
    expect(shape(c.getThread("s1").messages)).toEqual(["user:from phone", "assistant:reply"]);
    expect(settled).toHaveBeenCalledWith("s1", "s1");
    await settle();
    expect(c.getThread("s1").messages.map((m) => m.id)).toEqual([1, 2]);
    // The same run is never re-attached (replay would duplicate the reply).
    expect(await c.attachIfActive("s1")).toBe(false);
  });

  it("attachIfActive resolves false when nothing is running", async () => {
    const c = new ChatController();
    expect(await c.attachIfActive("s1")).toBe(false);
    expect(attachChatStream).not.toHaveBeenCalled();
  });

  it("watchActive probes now, on an interval, and stops cleanly", async () => {
    vi.useFakeTimers();
    try {
      const c = new ChatController();
      const stop = c.watchActive("s1", 1000);
      await vi.advanceTimersByTimeAsync(0);
      expect(urls("/api/chat/active").length).toBe(1);
      await vi.advanceTimersByTimeAsync(1000);
      expect(urls("/api/chat/active").length).toBe(2);
      stop();
      await vi.advanceTimersByTimeAsync(5000);
      expect(urls("/api/chat/active").length).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("watchActive skips a busy key", async () => {
    vi.useFakeTimers();
    try {
      const c = new ChatController();
      armTurn();
      void c.send("s1", "x", []);
      const stop = c.watchActive("s1", 1000);
      await vi.advanceTimersByTimeAsync(3000);
      expect(urls("/api/chat/active").length).toBe(0);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("dispose aborts the streams it owns", async () => {
    const c = new ChatController();
    const turn = armTurn();
    const sent = c.send("s1", "x", []);
    await tick();
    c.dispose();
    expect(turn.params.signal?.aborted).toBe(true);
    expect(await sent).toMatchObject({ ok: true });
  });
});
