// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import { snapshotElements, accessibleName, selectorFor, inShellChrome } from "@/lib/app-mcp/snapshot";

function fixture(html: string): void {
  document.body.innerHTML = html;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("snapshotElements", () => {
  it("lists visible interactive elements with role and name", () => {
    fixture(`
      <a href="/chat">Chats</a>
      <button type="button">Send</button>
      <input type="text" aria-label="Search" placeholder="Find…" />
      <input type="checkbox" aria-label="Done" checked />
      <div role="button" aria-label="Extra action">go</div>
    `);
    const snap = snapshotElements(document);
    expect(snap).toHaveLength(5);
    const [link, button, textbox, checkbox, roleButton] = snap;
    expect(link.role).toBe("link");
    expect(link.name).toBe("Chats");
    expect(button.role).toBe("button");
    expect(textbox.role).toBe("textbox");
    expect(textbox.name).toBe("Search");
    expect(checkbox.role).toBe("checkbox");
    expect(checkbox.checked).toBe(true);
    expect(roleButton.role).toBe("button");
  });

  it("assigns stable ids that survive re-snapshots", () => {
    fixture(`<button>One</button><button>Two</button>`);
    const first = snapshotElements(document);
    const second = snapshotElements(document);
    expect(second.map((e) => e.id)).toEqual(first.map((e) => e.id));
    expect(new Set(first.map((e) => e.id)).size).toBe(2);
  });

  it("skips hidden elements and hidden inputs", () => {
    fixture(`
      <button>Visible</button>
      <button hidden>Sneaky</button>
      <div aria-hidden="true"><button>Ghost</button></div>
      <input type="hidden" name="csrf" value="x" />
    `);
    const snap = snapshotElements(document);
    expect(snap).toHaveLength(1);
    expect(snap[0].name).toBe("Visible");
  });

  it("reads values and disabled state from form controls", () => {
    fixture(`<input type="text" value="hello" disabled /><select disabled></select>`);
    const [input, select] = snapshotElements(document);
    expect(input.value).toBe("hello");
    expect(input.disabled).toBe(true);
    expect(select.disabled).toBe(true);
  });

  it("produces a usable CSS selector per element", () => {
    fixture(`<section id="panel"><button>Inside</button></section>`);
    const [entry] = snapshotElements(document);
    expect(entry.selector).toContain("#panel");
    expect(document.querySelector(entry.selector)).toBeTruthy();
  });
});

describe("accessibleName", () => {
  it("prefers aria-label, then associated label, then text", () => {
    fixture(`
      <button aria-label="Aria wins">Text</button>
      <label for="f1">Field label</label><input id="f1" type="text" />
      <button>Plain text</button>
    `);
    const [aria, labelled, plain] = Array.from(
      document.querySelectorAll("button, input"),
    ).map((el) => accessibleName(el));
    expect(aria).toBe("Aria wins");
    expect(labelled).toBe("Field label");
    expect(plain).toBe("Plain text");
  });

  it("falls back to placeholder", () => {
    fixture(`<input type="text" placeholder="Search everything" />`);
    expect(accessibleName(document.querySelector("input")!)).toBe("Search everything");
  });
});

describe("selectorFor", () => {
  it("uses the element id when present", () => {
    fixture(`<button id="send-btn">Send</button>`);
    expect(selectorFor(document.querySelector("button")!)).toBe("#send-btn");
  });
});

describe("shell chrome exclusion", () => {
  it("keeps the lead-chat panel and Coral launcher out of page snapshots", () => {
    fixture(`
      <main><button>Page action</button><a href="/todos">Tasks</a></main>
      <div data-component="LeadChatHost">
        <textarea placeholder="Message your agent"></textarea>
        <button>Send</button>
        <a href="/chat">Chats</a>
      </div>
      <div data-component="CoralHost"><button aria-label="Open apps">✦</button></div>
    `);
    const snap = snapshotElements(document);
    expect(snap.map((e) => e.name).sort()).toEqual(["Page action", "Tasks"]);
  });

  it("identifies elements inside the shell chrome", () => {
    fixture(`
      <button id="bg">Background</button>
      <div data-component="LeadChatHost"><button id="panel">Panel</button></div>
    `);
    expect(inShellChrome(document.querySelector("#panel")!)).toBe(true);
    expect(inShellChrome(document.querySelector("#bg")!)).toBe(false);
  });
});
