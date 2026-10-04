// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/chat/MessageBubble";

describe("MessageBubble copy", () => {
  const writeText = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers();
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("copies the displayed reply and shows Copied for 1.5s", async () => {
    render(
      <MessageBubble message={{ role: "assistant", content: "**Bold** answer" }} />,
    );
    const button = screen.getByRole("button", { name: "Copy reply" });
    expect(button.textContent).toBe("Copy");
    await act(async () => {
      fireEvent.click(button);
    });
    expect(writeText).toHaveBeenCalledWith("**Bold** answer");
    expect(button.textContent).toBe("Copied");
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(button.textContent).toBe("Copy");
  });

  it("stays on Copy when the clipboard write is refused", async () => {
    writeText.mockRejectedValue(new Error("denied"));
    render(<MessageBubble message={{ role: "assistant", content: "x" }} />);
    const button = screen.getByRole("button", { name: "Copy reply" });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(button.textContent).toBe("Copy");
  });
});
