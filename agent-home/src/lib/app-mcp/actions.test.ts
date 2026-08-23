// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { executeCommand, resolveElement } from "@/lib/app-mcp/actions";
import { setUiContext } from "@/lib/app-mcp/state";
import { snapshotElements } from "@/lib/app-mcp/snapshot";

beforeEach(() => {
  document.body.innerHTML = "";
  setUiContext({ path: "/todos", element: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("executeCommand snapshot", () => {
  it("returns the interactive elements with fresh ids", () => {
    document.body.innerHTML = `<button>Filter</button><a href="/chat">Chats</a>`;
    const res = executeCommand({ type: "snapshot" });
    expect(res.ok).toBe(true);
    const elements = res.elements as Array<{ name: string }>;
    expect(elements.map((e) => e.name).sort()).toEqual(["Chats", "Filter"]);
    expect(res.state?.path).toBe("/todos");
  });
});

describe("executeCommand click", () => {
  it("clicks by snapshot id and reports the element", () => {
    document.body.innerHTML = `<button type="button">Filter</button>`;
    const [entry] = snapshotElements(document);
    const onClick = vi.fn();
    document.querySelector("button")!.addEventListener("click", onClick);

    const res = executeCommand({ type: "click", elementId: entry.id });
    expect(res.ok).toBe(true);
    expect(res.detail).toContain('"Filter"');
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("clicks by accessible name when no id is known", () => {
    document.body.innerHTML = `<button type="button">Archive</button>`;
    const onClick = vi.fn();
    document.querySelector("button")!.addEventListener("click", onClick);

    const res = executeCommand({ type: "click", name: "archive" });
    expect(res.ok).toBe(true);
    expect(onClick).toHaveBeenCalled();
  });

  it("fails cleanly when nothing matches", () => {
    document.body.innerHTML = `<button>Only</button>`;
    const res = executeCommand({ type: "click", name: "Missing" });
    expect(res.ok).toBe(false);
    expect(res.detail).toContain("No element matched");
  });
});

describe("executeCommand type", () => {
  it("sets the value through the native setter and fires input", () => {
    document.body.innerHTML = `<input type="text" aria-label="Title" />`;
    const input = document.querySelector("input")!;
    const onInput = vi.fn();
    input.addEventListener("input", onInput);

    const res = executeCommand({ type: "type", name: "Title", value: "Ship it" });
    expect(res.ok).toBe(true);
    expect(input.value).toBe("Ship it");
    expect(onInput).toHaveBeenCalled();
  });

  it("refuses non-text targets", () => {
    document.body.innerHTML = `<button>Send</button>`;
    const res = executeCommand({ type: "type", name: "Send", value: "x" });
    expect(res.ok).toBe(false);
  });
});

describe("executeCommand select/read/focus", () => {
  it("selects an option and fires change", () => {
    document.body.innerHTML = `
      <select aria-label="Stage">
        <option value="open">Open</option>
        <option value="done">Done</option>
      </select>`;
    const select = document.querySelector("select")!;
    const onChange = vi.fn();
    select.addEventListener("change", onChange);

    const res = executeCommand({ type: "select", name: "Stage", value: "done" });
    expect(res.ok).toBe(true);
    expect(select.value).toBe("done");
    expect(onChange).toHaveBeenCalled();
  });

  it("reads the current value of an input", () => {
    document.body.innerHTML = `<input type="text" aria-label="Query" value="hello" />`;
    const res = executeCommand({ type: "read", name: "Query" });
    expect(res.ok).toBe(true);
    expect(res.detail).toBe("hello");
  });

  it("focuses an element", () => {
    document.body.innerHTML = `<button>Target</button>`;
    const res = executeCommand({ type: "focus", name: "Target" });
    expect(res.ok).toBe(true);
    expect(document.activeElement).toBe(document.querySelector("button"));
  });
});

describe("executeCommand navigate", () => {
  it("refuses external or protocol-relative paths", () => {
    const res = executeCommand({ type: "navigate", path: "https://evil.example" });
    expect(res.ok).toBe(false);
    const res2 = executeCommand({ type: "navigate", path: "//evil.example" });
    expect(res2.ok).toBe(false);
  });

  it("assigns internal paths to the window location", () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign, pathname: "/todos", protocol: "http:", host: "localhost" },
      configurable: true,
    });
    const res = executeCommand({ type: "navigate", path: "/chat" });
    expect(res.ok).toBe(true);
    expect(assign).toHaveBeenCalledWith("/chat");
  });
});

describe("resolveElement", () => {
  it("prefers id over selector and name", () => {
    document.body.innerHTML = `<button id="a">One</button><button>Two</button>`;
    const snap = snapshotElements(document);
    const two = snap.find((e) => e.name === "Two")!;
    const el = resolveElement({ elementId: two.id, selector: "#a", name: "One" });
    expect(el?.textContent).toBe("Two");
  });
});
