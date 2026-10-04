// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useStickToBottom,
  type StickToBottom,
} from "@/components/chat/thread/useStickToBottom";

// Fake layout for the scroller (jsdom has none).
const m = { scrollHeight: 1000, clientHeight: 400, scrollTop: 0 };
const isScroller = (el: Element) => (el as HTMLElement).dataset?.testid === "scroller";
const saved: Record<string, PropertyDescriptor | undefined> = {};

let roCallbacks: Array<() => void> = [];
class FakeResizeObserver {
  private cb: () => void;
  constructor(cb: () => void) {
    this.cb = cb;
    roCallbacks.push(cb);
  }
  observe() {}
  disconnect() {
    roCallbacks = roCallbacks.filter((c) => c !== this.cb);
  }
}
const resize = () => act(() => roCallbacks.forEach((cb) => cb()));

beforeAll(() => {
  for (const key of ["scrollHeight", "clientHeight", "scrollTop"]) {
    saved[key] = Object.getOwnPropertyDescriptor(Element.prototype, key);
  }
  Object.defineProperty(Element.prototype, "scrollHeight", {
    configurable: true,
    get() {
      return isScroller(this) ? m.scrollHeight : 0;
    },
  });
  Object.defineProperty(Element.prototype, "clientHeight", {
    configurable: true,
    get() {
      return isScroller(this) ? m.clientHeight : 0;
    },
  });
  Object.defineProperty(Element.prototype, "scrollTop", {
    configurable: true,
    get() {
      return isScroller(this) ? m.scrollTop : 0;
    },
    set(v: number) {
      if (isScroller(this)) {
        m.scrollTop = Math.max(0, Math.min(v, m.scrollHeight - m.clientHeight));
      }
    },
  });
});

afterAll(() => {
  for (const [key, desc] of Object.entries(saved)) {
    if (desc) Object.defineProperty(Element.prototype, key, desc);
  }
});

let api: StickToBottom;
let harnessRenders = 0;
function Harness({ threshold }: { threshold?: number }) {
  harnessRenders += 1;
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  api = useStickToBottom(scrollRef, contentRef, { threshold });
  return (
    <div ref={scrollRef} data-testid="scroller">
      <div ref={contentRef} />
      <span data-testid="state">
        {api.atBottom ? "bottom" : "up"}/{api.hasNew ? "new" : "none"}
      </span>
    </div>
  );
}

const scroller = (c: HTMLElement) => c.querySelector('[data-testid="scroller"]')!;
const userScroll = (c: HTMLElement, top: number) =>
  act(() => {
    m.scrollTop = top;
    fireEvent.scroll(scroller(c));
  });
const grow = (by: number) => {
  m.scrollHeight += by;
  resize();
};

describe("useStickToBottom", () => {
  beforeEach(() => {
    m.scrollHeight = 1000;
    m.clientHeight = 400;
    m.scrollTop = 0;
    roCallbacks = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("pins to the bottom on mount", () => {
    render(<Harness />);
    expect(m.scrollTop).toBe(600);
    expect(api.atBottom).toBe(true);
  });

  it("follows content growth while at the bottom", () => {
    render(<Harness />);
    grow(300);
    expect(m.scrollTop).toBe(900);
    expect(api.hasNew).toBe(false);
  });

  it("stays stuck when a scroll event lands after growth (no upward move)", () => {
    const { container } = render(<Harness />);
    m.scrollHeight += 500; // grown, not yet observed
    act(() => {
      fireEvent.scroll(scroller(container));
    });
    expect(api.atBottom).toBe(true);
    resize();
    expect(m.scrollTop).toBe(1100);
  });

  it("stops following once the reader scrolls up and flags new content", () => {
    const { container } = render(<Harness />);
    userScroll(container, 200);
    expect(api.atBottom).toBe(false);
    grow(300);
    expect(m.scrollTop).toBe(200);
    expect(api.hasNew).toBe(true);
    expect(container.textContent).toContain("up/new");
  });

  it("flags growth even when a scroll event is measured before the resize", () => {
    const { container } = render(<Harness />);
    userScroll(container, 200);
    m.scrollHeight += 300;
    userScroll(container, 190);
    resize();
    expect(api.hasNew).toBe(true);
  });

  it("does not flag new content when nothing was added below", () => {
    const { container } = render(<Harness />);
    userScroll(container, 200);
    resize();
    expect(api.hasNew).toBe(false);
  });

  it("treats within-threshold as at the bottom and clears hasNew there", () => {
    const { container } = render(<Harness threshold={80} />);
    userScroll(container, 100);
    grow(200);
    expect(api.hasNew).toBe(true);
    // 1200 - 750 - 400 = 50 ≤ 80
    userScroll(container, 750);
    expect(api.atBottom).toBe(true);
    expect(api.hasNew).toBe(false);
  });

  it("scrollToBottom re-sticks and clears hasNew", () => {
    const { container } = render(<Harness />);
    userScroll(container, 0);
    grow(400);
    expect(api.hasNew).toBe(true);
    act(() => api.scrollToBottom());
    expect(m.scrollTop).toBe(1000);
    expect(api.atBottom).toBe(true);
    expect(api.hasNew).toBe(false);
    grow(100);
    expect(m.scrollTop).toBe(1100);
  });

  it("preservePrepend keeps the viewport in place when older rows load", () => {
    const { container } = render(<Harness />);
    userScroll(container, 50);
    act(() => api.preservePrepend());
    grow(700); // older page prepended above
    expect(m.scrollTop).toBe(750);
    expect(api.hasNew).toBe(false);
    // Subsequent growth behaves normally again.
    grow(100);
    expect(m.scrollTop).toBe(750);
    expect(api.hasNew).toBe(true);
  });

  it("does not re-render on scroll events that keep the same state", () => {
    const { container } = render(<Harness />);
    const before = harnessRenders;
    userScroll(container, 590);
    userScroll(container, 595);
    expect(harnessRenders).toBe(before);
    expect(container.textContent).toContain("bottom/none");
  });

  it("is a no-op without ResizeObserver", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    render(<Harness />);
    expect(m.scrollTop).toBe(600);
    expect(api.atBottom).toBe(true);
  });
});
