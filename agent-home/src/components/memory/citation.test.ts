import { describe, expect, it } from "vitest";

import { describeSource, sourceLink } from "@/components/memory/citation";

describe("sourceLink", () => {
  it("links a file_asset_id to the /api/files content endpoint", () => {
    const link = sourceLink({
      file_asset_id: "asset-uuid-1",
      document_id: "doc-uuid-1",
    });
    expect(link).toEqual({
      href: "/api/files/asset-uuid-1/content",
      text: "Open the file",
      external: false,
    });
  });

  it("URL-encodes the file_asset_id", () => {
    const link = sourceLink({
      file_asset_id: "asset/with spaces",
    });
    expect(link?.href).toBe("/api/files/asset%2Fwith%20spaces/content");
  });

  it("falls back to document_id when file_asset_id is absent", () => {
    const link = sourceLink({
      document_id: "doc-uuid-1",
    });
    expect(link).toEqual({
      href: "/memory?document=doc-uuid-1",
      text: "See this document's passages",
      external: false,
    });
  });

  it("falls back to document_id when file_asset_id is null", () => {
    const link = sourceLink({
      file_asset_id: null,
      document_id: "doc-uuid-1",
    });
    expect(link?.href).toBe("/memory?document=doc-uuid-1");
  });

  it("falls back to source_session when neither file nor document", () => {
    const link = sourceLink({
      source_session: "sess-123",
    });
    expect(link).toEqual({
      href: "/chat?session=sess-123",
      text: "Open the conversation",
      external: false,
    });
  });

  it("returns null when nothing is set", () => {
    const link = sourceLink({});
    expect(link).toBeNull();
  });

  it("an external URL in source_ref takes priority over file_asset_id", () => {
    const link = sourceLink({
      source_ref: "https://drive.google.com/file/d/abc",
      file_asset_id: "asset-1",
      document_id: "doc-1",
    });
    expect(link).toEqual({
      href: "https://drive.google.com/file/d/abc",
      text: "Open the document",
      external: true,
    });
  });

  it("file_asset_id takes priority over document_id", () => {
    // The file content endpoint is more direct than re-filtering the
    // document's passages on the /memory page.
    const link = sourceLink({
      file_asset_id: "asset-1",
      document_id: "doc-1",
      source_session: "sess-1",
    });
    expect(link?.href).toBe("/api/files/asset-1/content");
  });
});

describe("describeSource — file source_kind", () => {
  it("renders 'File' for source_kind='file'", () => {
    const citation = describeSource({
      source_kind: "file",
      document_title: "pricing.pdf",
      source_ref: "asset-uuid-1",
    });
    expect(citation).not.toBeNull();
    expect(citation!.label).toBe("File: pricing.pdf");
    expect(citation!.detail).toBe("asset-uuid-1");
  });
});
