import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// MemoryView is "use client" — its useEffect hooks won't fire in SSR, but the
// initial state (rows from props) is rendered. That's what we assert on.
vi.mock("@/components/memory/MemoryMap", () => ({
  MemoryMap: () => null,
}));

import { MemoryView, describeFailure } from "@/components/memory/MemoryView";
import type { MemoryRowsResponse, MemorySummary } from "@/types";

/** Decode HTML entities so `toContain` can match apostrophes etc. */
function decodeHtml(s: string): string {
  return s
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

const SUMMARY: MemorySummary = {
  space: {
    column_dim: 1024,
    rows_by_model: { "BAAI/bge-m3": 37 },
    configured_model: "BAAI/bge-m3",
    healthy: true,
  },
  totals: { memories: 37, documents: 0, chunks: 0 },
  by_owner: { leo_owner: 37 },
  by_topic: { work: 20, personal: 17 },
  by_kind: { fact: 30, preference: 7 },
  growth: [],
  recall_use: {
    never_used: 20,
    used_7d: 10,
    top: [],
  },
};

const ROWS: MemoryRowsResponse = {
  rows: [
    {
      id: "m1",
      owner_user_id: "leo_owner",
      visibility: "shared",
      kind: "fact",
      topic: "work",
      text: "Hermes runs on port 9119",
      truncated: false,
      created_at: "2026-08-01T00:00:00Z",
      uses: 3,
      last_used: "2026-08-05T00:00:00Z",
      elevated: false,
      provenance: "",
      score: null,
      source_session: "sess-42",
    },
    {
      id: "m2",
      owner_user_id: "alice",
      visibility: "shared",
      kind: "fact",
      topic: "personal",
      text: "Alice likes tea",
      truncated: false,
      created_at: "2026-08-01T00:00:00Z",
      uses: 0,
      last_used: null,
      elevated: true,
      provenance: "from alice's memory",
      score: null,
    },
  ],
  total: 2,
  limit: 25,
  offset: 0,
};

describe("MemoryView", () => {
  it("renders rows with text and topic", () => {
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain("Hermes runs on port 9119");
    expect(html).toContain("Alice likes tea");
    expect(html).toContain("work");
    expect(html).toContain("personal");
  });

  it("renders provenance for an elevated row", () => {
    const html = decodeHtml(
      renderToStaticMarkup(
        <MemoryView summary={SUMMARY} initialRows={ROWS} />,
      ),
    );
    expect(html).toContain("from alice's memory");
  });

  it("renders pills: memories, never recalled", () => {
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain("37");
    expect(html).toContain("memories");
    expect(html).toContain("20");
    expect(html).toContain("never recalled");
  });

  it("renders totals.memories === 0 without crashing", () => {
    const emptySummary = {
      ...SUMMARY,
      totals: { memories: 0, documents: 0, chunks: 0 },
      recall_use: { never_used: 0, used_7d: 0, top: [] },
    };
    const emptyRows: MemoryRowsResponse = {
      rows: [],
      total: 0,
      limit: 25,
      offset: 0,
    };
    const html = renderToStaticMarkup(
      <MemoryView summary={emptySummary} initialRows={emptyRows} />,
    );
    expect(html).toContain("No memories visible in your scope yet");
  });

  it("renders the query placement input", () => {
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain("Place a query on the map");
    // The "never stored" notice is conditional on queryText being non-empty;
    // in SSR that state starts empty, so we assert the button instead.
    expect(html).toContain("Place");
  });

  it("renders search input", () => {
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain("Search memories");
  });

  it("puts the search box and the list in their own panel beside the map", () => {
    // On a wide screen the stacked layout forced a scroll between the map and
    // the list, which are two halves of the same question.
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain('data-component="MemoryListPanel"');
    expect(html).toContain("xl:grid-cols-");
  });

  it("cites the chat a memory was written in", () => {
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(html).toContain("From a chat");
    expect(html).toContain("sess-42");
  });

  it("cites the document a chunk row came from", () => {
    const chunkRows: MemoryRowsResponse = {
      ...ROWS,
      rows: [
        {
          ...ROWS.rows[0],
          id: "c1",
          kind: "chunk",
          source_session: null,
          document_title: "Joyaether 2026 Support Policy",
          section: "Response targets",
          source_kind: "local",
          source_ref: "/opt/data/uploads/policy.md",
        },
      ],
    };
    const html = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={chunkRows} />,
    );
    expect(html).toContain("Joyaether 2026 Support Policy");
    expect(html).toContain("Response targets");
  });

  it("offers the documents tab only when documents exist", () => {
    const withoutDocs = renderToStaticMarkup(
      <MemoryView summary={SUMMARY} initialRows={ROWS} />,
    );
    expect(withoutDocs).not.toContain(">Documents<");

    const withDocs = renderToStaticMarkup(
      <MemoryView
        summary={{ ...SUMMARY, totals: { memories: 37, documents: 2, chunks: 9 } }}
        initialRows={ROWS}
      />,
    );
    expect(withDocs).toContain(">Documents<");
  });
});

describe("describeFailure", () => {
  it("says something actionable for each status a phone user can hit", () => {
    // A failed refetch used to `return` silently: the button stopped working
    // and the list stayed put, which reads as "no results" rather than
    // "your session expired".
    expect(describeFailure(401)).toMatch(/sign in/i);
    expect(describeFailure(403)).toMatch(/access/i);
    expect(describeFailure(409)).toMatch(/principal/i);
    expect(describeFailure(502)).toMatch(/load/i);
  });
});
