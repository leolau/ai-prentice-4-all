import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

// FilesView is "use client" — its effects don't fire under SSR, but the state
// derived from props does render, which is what these assert on.
import {
  FileDetail,
  FilesView,
  formatSize,
  formatWhen,
  provenanceLine,
  surfaceLabel,
} from "@/components/files/FilesView";
import type { FileAsset, FileAssetsResponse } from "@/types";

function asset(over: Partial<FileAsset> = {}): FileAsset {
  return {
    id: "f-1",
    owner_user_id: "leo",
    visibility: "private:leo",
    surface: "telegram",
    account_id: "bot",
    conversation: "chat-1",
    sender_id: "tg-1",
    sender_name: "Ada Wong",
    message_id: "42",
    received_at: "2026-08-06T14:08:00.000Z",
    filename: "grant.pdf",
    content_type: "application/pdf",
    byte_size: 2_700_000,
    sha256: "abc",
    storage_path: "leo/telegram/2026-08/abc-grant.pdf",
    document_id: null,
    remembered_at: null,
    remembered_by: null,
    remembered: false,
    ...over,
  };
}

function page(files: FileAsset[], total = files.length): FileAssetsResponse {
  return { files, total, limit: 50, offset: 0 };
}

describe("FilesView", () => {
  it("lists a file with the provenance line a person would recognise", () => {
    const html = renderToStaticMarkup(
      <FilesView initial={page([asset()])} surfaces={[]} />,
    );
    expect(html).toContain("grant.pdf");
    expect(html).toContain("Telegram");
    expect(html).toContain("Ada Wong");
    expect(html).toContain("2.6 MB");
  });

  it("marks the exception, not the rule: only remembered files get the badge", () => {
    const html = renderToStaticMarkup(
      <FilesView
        initial={page([
          asset({ id: "a", filename: "stored.png" }),
          asset({
            id: "b",
            filename: "minutes.txt",
            remembered: true,
            document_id: "doc-9",
            remembered_by: "email-triage",
          }),
        ])}
        surfaces={[]}
      />,
    );
    // Count the badge, not the word: "Remembered" is also a filter chip.
    expect(html.match(/data-component="RememberedBadge"/g) ?? []).toHaveLength(
      1,
    );
  });

  it("says what the page is for when nothing has arrived", () => {
    const html = renderToStaticMarkup(
      <FilesView initial={page([])} surfaces={[]} />,
    );
    expect(html).toContain("Nothing has arrived yet");
    expect(html).toContain("Telegram");
  });

  it("offers a chip only for surfaces the reader actually has files from", () => {
    const html = renderToStaticMarkup(
      <FilesView
        initial={page([asset()])}
        surfaces={[
          { surface: "email", count: 3 },
          { surface: "agent_home", count: 9 },
        ]}
      />,
    );
    expect(html).toContain("Email · 3");
    expect(html).toContain("Chat · 9");
    expect(html).not.toContain("WhatsApp ·");
  });
});

describe("FileDetail", () => {
  it("reaches the bytes only through the checked route, never the bucket", () => {
    const html = renderToStaticMarkup(
      <FileDetail file={asset()} onClose={() => {}} />,
    );
    expect(html).toContain('href="/api/files/f-1/content"');
    expect(html).toContain('href="/api/files/f-1/content?download=1"');
    // No object key, no signed URL, no path on the box.
    expect(html).not.toContain("leo/telegram/2026-08");
    expect(html).not.toContain("supabase");
  });

  it("says plainly that a stored file is not in memory", () => {
    const html = renderToStaticMarkup(
      <FileDetail file={asset()} onClose={() => {}} />,
    );
    expect(html).toContain("Stored only");
    expect(html).not.toContain("/memory?document=");
  });

  it("links a remembered file to its passages, naming who decided", () => {
    const html = renderToStaticMarkup(
      <FileDetail
        file={asset({
          remembered: true,
          document_id: "doc-9",
          remembered_by: "email-triage",
        })}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("Remembered by email-triage");
    expect(html).toContain('href="/memory?document=doc-9"');
  });
});

describe("formatting helpers", () => {
  it("names the surfaces a person recognises and passes through new ones", () => {
    expect(surfaceLabel("agent_home")).toBe("Chat");
    expect(surfaceLabel("whatsapp")).toBe("WhatsApp");
    expect(surfaceLabel("carrier-pigeon")).toBe("carrier-pigeon");
  });

  it("formats sizes and missing timestamps without lying", () => {
    expect(formatSize(0)).toBe("0 B");
    expect(formatSize(900)).toBe("900 B");
    expect(formatSize(2048)).toBe("2 KB");
    expect(formatSize(2_700_000)).toBe("2.6 MB");
    expect(formatWhen(null)).toBe("unknown time");
    expect(formatWhen("not-a-date")).toBe("unknown time");
  });

  it("falls back to the sender id when no display name arrived", () => {
    expect(provenanceLine(asset({ sender_name: null }))).toContain("tg-1");
  });

  // /files back-link (Part 1.3): the link appears only when the asset's
  // arrival raised a to-do — i.e. when inbound_item_id is set.
  it("renders 'To-dos from this' link when inbound_item_id is set", () => {
    const html = renderToStaticMarkup(
      <FileDetail file={asset({ inbound_item_id: "arr-123" })} onClose={() => {}} />,
    );
    expect(html).toContain("To-dos from this");
    expect(html).toContain("source_ref=arr-123");
  });

  it("omits the back-link when inbound_item_id is absent", () => {
    const html = renderToStaticMarkup(
      <FileDetail file={asset({ inbound_item_id: null })} onClose={() => {}} />,
    );
    expect(html).not.toContain("To-dos from this");
  });
});
