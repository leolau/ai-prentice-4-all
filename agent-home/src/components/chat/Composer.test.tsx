// @vitest-environment jsdom
/**
 * Oversized-file upload confirmation: files over the advisory 100 MB
 * threshold pause at an "Upload anyway" prompt rather than being refused.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/Composer";

/** A File whose declared size is faked — no 100 MB allocation needed. */
function fakeFile(name: string, size: number): File {
  const f = new File(["x"], name);
  Object.defineProperty(f, "size", { value: size });
  return f;
}

function pickFile(getByLabelText: (t: string) => HTMLElement, file: File) {
  const input = document.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Composer oversized uploads", () => {
  it("uploads a small file without confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ path: "p", name: "a.txt" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <Composer
        sending={false}
        storageEnabled
        sessionId="s1"
        onSend={() => {}}
      />,
    );
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [fakeFile("a.txt", 8)] } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(
      document.querySelector('[data-component="OversizeConfirm"]'),
    ).toBeNull();
  });

  it("asks before uploading a file over 100 MB, then proceeds", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ path: "p", name: "big.zip" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { getByText, queryByText } = render(
      <Composer
        sending={false}
        storageEnabled
        sessionId="s1"
        onSend={() => {}}
      />,
    );
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [fakeFile("big.zip", 101 * 1024 * 1024)] },
    });
    // Pauses for confirmation — no upload yet.
    expect(queryByText("Upload anyway")).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(getByText("Upload anyway"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
  });

  it("cancel leaves the file unuploaded", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { getByText, queryByText } = render(
      <Composer
        sending={false}
        storageEnabled
        sessionId="s1"
        onSend={() => {}}
      />,
    );
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [fakeFile("big.zip", 101 * 1024 * 1024)] },
    });
    fireEvent.click(getByText("Cancel"));
    expect(queryByText("Upload anyway")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
