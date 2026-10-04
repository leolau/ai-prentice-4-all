// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadProjectFile } from "@/components/projects/inputs/uploadProjectFile";

afterEach(() => {
  vi.unstubAllGlobals();
});

const file = () => new File(["hello"], "MOU.docx");

describe("uploadProjectFile", () => {
  it("posts the file, its link kind and label with the idempotency key (fetch path)", async () => {
    vi.stubGlobal("XMLHttpRequest", undefined);
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ kind: "sample", ref: "p/MOU.docx", profile: "default" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await uploadProjectFile({ slug: "my proj", file: file(), kind: "sample", idempotencyKey: "k1" });
    expect(res.ok).toBe(true);
    expect(res.data?.ref).toBe("p/MOU.docx");
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/projects/my%20proj/files/upload");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("k1");
    const form = init.body as FormData;
    expect(form.get("kind")).toBe("sample");
    expect(form.get("label")).toBe("MOU.docx");
    expect((form.get("file") as File).name).toBe("MOU.docx");
  });

  it("turns a refusal into a sentence", async () => {
    vi.stubGlobal("XMLHttpRequest", undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Storage is not configured." }), { status: 501 })),
    );
    const res = await uploadProjectFile({ slug: "p", file: file(), kind: "reference", idempotencyKey: "k" });
    expect(res.ok).toBe(false);
    expect(res.error).toBeTruthy();
  });

  it("an unreachable server is a failed result, not a throw", async () => {
    vi.stubGlobal("XMLHttpRequest", undefined);
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    const res = await uploadProjectFile({ slug: "p", file: file(), kind: "file", idempotencyKey: "k" });
    expect(res).toMatchObject({ ok: false, status: 0, error: "Could not reach the server." });
  });

  it("reports progress through XHR when available", async () => {
    const sent: { headers: Record<string, string>; body: unknown }[] = [];
    class FakeXHR {
      status = 0;
      responseText = "";
      upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      private headers: Record<string, string> = {};
      open() {}
      setRequestHeader(k: string, v: string) {
        this.headers[k] = v;
      }
      send(body: unknown) {
        sent.push({ headers: this.headers, body });
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 } as ProgressEvent);
        this.status = 200;
        this.responseText = JSON.stringify({ kind: "reference", ref: "p/MOU.docx", profile: "default" });
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const progress: number[] = [];
    const res = await uploadProjectFile({
      slug: "p",
      file: file(),
      kind: "reference",
      idempotencyKey: "k9",
      onProgress: (f) => progress.push(f),
    });
    expect(res.ok).toBe(true);
    expect(progress).toEqual([0.5]);
    expect(sent[0].headers["Idempotency-Key"]).toBe("k9");
  });
});
