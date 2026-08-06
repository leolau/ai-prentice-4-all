/**
 * Behaviour of the manual session-tab ordering (drag-to-reorder). The stored
 * order is a device-local id sequence; it must be re-applied on top of whatever
 * the server returns, place unknown/new conversations first, never invent or
 * drop sessions, and survive malformed storage.
 */
import { describe, expect, it } from "vitest";

import { orderSessions, parseOrder } from "@/lib/chat/session-order";

const s = (id: string) => ({ id });

describe("parseOrder", () => {
  it("parses a JSON id array and rejects non-strings", () => {
    expect(parseOrder('["b","a","c"]')).toEqual(["b", "a", "c"]);
    expect(parseOrder('["a",1,null,"b"]')).toEqual(["a", "b"]);
  });

  it("returns an empty list for empty or malformed input", () => {
    expect(parseOrder("")).toEqual([]);
    expect(parseOrder("not json")).toEqual([]);
    expect(parseOrder('{"a":1}')).toEqual([]);
  });
});

describe("orderSessions", () => {
  it("orders known sessions by the saved sequence", () => {
    const sessions = [s("a"), s("b"), s("c")];
    expect(orderSessions(sessions, ["c", "a", "b"]).map((x) => x.id)).toEqual([
      "c",
      "a",
      "b",
    ]);
  });

  it("places sessions not in the order first, keeping their relative order", () => {
    // `d` and `e` are new (server order preserved); `a`/`b` follow the saved seq.
    const sessions = [s("d"), s("a"), s("e"), s("b")];
    expect(orderSessions(sessions, ["b", "a"]).map((x) => x.id)).toEqual([
      "d",
      "e",
      "b",
      "a",
    ]);
  });

  it("ignores ids in the order that no longer exist and never drops a session", () => {
    const sessions = [s("a"), s("b")];
    const out = orderSessions(sessions, ["gone", "b", "a", "also-gone"]);
    expect(out.map((x) => x.id).sort()).toEqual(["a", "b"]);
    expect(out.map((x) => x.id)).toEqual(["b", "a"]);
  });

  it("is a no-op ordering when no order is stored", () => {
    const sessions = [s("a"), s("b"), s("c")];
    expect(orderSessions(sessions, []).map((x) => x.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });
});
