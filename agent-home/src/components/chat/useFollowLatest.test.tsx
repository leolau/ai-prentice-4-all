// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useEffect, useRef, type MutableRefObject } from "react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { useFollowLatest } from "@/components/chat/useFollowLatest";

// Fake layout for the scroller (jsdom has none).
const m = { scrollHeight: 2000, clientHeight: 400, scrollTop: 1600 };
const isScroller = (el: Element) => (el as HTMLElement).dataset?.testid === "scroller";
const saved: Record<string, PropertyDescriptor | undefined> = {};

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
      if (isScroller(this)) m.scrollTop = v;
    },
  });
});

afterAll(() => {
  for (const [key, desc] of Object.entries(saved)) {
    if (desc) Object.defineProperty(Element.prototype, key, desc);
  }
});

let follow: MutableRefObject<boolean>;
function Harness() {
  const ref = useRef<HTMLDivElement>(null);
  const f = useFollowLatest(ref);
  useEffect(() => {
    follow = f;
  });
  return <div ref={ref} data-testid="scroller" />;
}

const scrollTo = (c: HTMLElement, top: number) =>
  act(() => {
    m.scrollTop = top;
    fireEvent.scroll(c.querySelector('[data-testid="scroller"]')!);
  });

beforeEach(() => {
  m.scrollHeight = 2000;
  m.scrollTop = 1600;
});
afterEach(cleanup);

describe("useFollowLatest", () => {
  it("follows the newest message until the reader scrolls up", () => {
    const { container } = render(<Harness />);
    expect(follow.current).toBe(true);
    scrollTo(container, 900);
    expect(follow.current).toBe(false);
  });

  it("keeps the reader's place while content keeps growing below", () => {
    const { container } = render(<Harness />);
    scrollTo(container, 900);
    m.scrollHeight = 2600; // streamed text; growth fires no scroll event
    expect(follow.current).toBe(false);
    scrollTo(container, 950); // reading on, still far from the bottom
    expect(follow.current).toBe(false);
  });

  it("resumes following once the reader is back near the bottom", () => {
    const { container } = render(<Harness />);
    scrollTo(container, 900);
    scrollTo(container, 1550);
    expect(follow.current).toBe(true);
  });
});
