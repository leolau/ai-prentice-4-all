import { describe, expect, it } from "vitest";

import {
  artifactsFromOutputs,
  attachBody,
  decodeEscapes,
  extOf,
  fileBadge,
  groupVersions,
  isOpenable,
  latestShelfItems,
  newestFirst,
  outputStateLabel,
  provenanceLabel,
  shortTitle,
  suggestedOutput,
  unattachedByRun,
  unattachedFiles,
  unattachedSentence,
  versionLabel,
} from "@/components/projects/outputs/artifacts";
import { ARTIFACT, DELIVERY, NOW, OUTPUT } from "@/components/projects/outputs/fixtures";

describe("decodeEscapes", () => {
  it("turns literal \\u escapes back into CJK text", () => {
    expect(decodeEscapes("\\u9752\\u7530 MOU")).toBe("青田 MOU");
    expect(decodeEscapes("\\ud83d\\ude00")).toBe("😀");
  });
  it("leaves plain text, null and lone surrogates alone", () => {
    expect(decodeEscapes("青田")).toBe("青田");
    expect(decodeEscapes(null)).toBeNull();
    expect(decodeEscapes("\\ud83d alone")).toBe("\\ud83d alone");
  });
});

describe("extOf / fileBadge", () => {
  it("derives the extension from names, paths and URLs", () => {
    expect(extOf("/a/MOU.v2.DOCX")).toBe("docx");
    expect(extOf("https://x.test/report.pdf?dl=1")).toBe("pdf");
    expect(extOf("README")).toBeNull();
    expect(extOf(".env")).toBeNull();
    expect(extOf(null)).toBeNull();
  });
  it("badges by ext, alias, then mime", () => {
    expect(fileBadge({ ext: "docx" })).toBe("DOCX");
    expect(fileBadge({ ext: "markdown" })).toBe("MD");
    expect(fileBadge({ ext: "gdoc" })).toBe("DOC");
    expect(fileBadge({ ext: null, title: "notes.md" })).toBe("MD");
    expect(fileBadge({ ext: null, mime: "application/pdf", title: "x" })).toBe("PDF");
    expect(fileBadge({ ext: null, mime: null, title: "Shared doc" })).toBe("FILE");
  });
});

describe("ordering and the shelf", () => {
  const items = [
    ARTIFACT({ id: "a", created_at: NOW - 50 }),
    ARTIFACT({ id: "b", created_at: NOW - 10, kind: "deliverable", output_id: "out_1" }),
    ARTIFACT({ id: "c", created_at: NOW - 5, kind: "note" }),
    ARTIFACT({ id: "d", created_at: NOW - 30 }),
    ARTIFACT({ id: "e", created_at: NOW - 40 }),
    ARTIFACT({ id: "f", created_at: NOW - 60 }),
  ];
  it("sorts newest first with a stable tie-break", () => {
    expect(newestFirst(items).map((a) => a.id)).toEqual(["c", "b", "d", "e", "a", "f"]);
    expect(
      newestFirst([ARTIFACT({ id: "z", created_at: 1 }), ARTIFACT({ id: "y", created_at: 1 })]).map(
        (a) => a.id,
      ),
    ).toEqual(["y", "z"]);
  });
  it("keeps the 4 newest deliverables and drafts, never notes", () => {
    expect(latestShelfItems(items).map((a) => a.id)).toEqual(["b", "d", "e", "a"]);
  });
});

describe("unattached detection", () => {
  const items = [
    ARTIFACT({ id: "r1a", run_no: 1 }),
    ARTIFACT({ id: "r1b", run_no: 1, created_at: NOW }),
    ARTIFACT({ id: "r2", run_no: 2 }),
    ARTIFACT({ id: "loose", run_no: null }),
    ARTIFACT({ id: "note", kind: "note" }),
    ARTIFACT({ id: "owned", kind: "deliverable", output_id: "out_1" }),
  ];
  it("lists drafts no output owns, not notes or deliverables", () => {
    expect(unattachedFiles(items).map((a) => a.id).sort()).toEqual(["loose", "r1a", "r1b", "r2"]);
  });
  it("groups by run, newest run first, loose files last", () => {
    const groups = unattachedByRun(items);
    expect(groups.map((g) => g.runNo)).toEqual([2, 1, null]);
    expect(groups[1].files.map((f) => f.id)).toEqual(["r1b", "r1a"]);
  });
  it("says it in plain English", () => {
    const groups = unattachedByRun(items);
    expect(unattachedSentence(groups[1])).toBe(
      "Run 1 produced 2 files that aren't linked to any output.",
    );
    expect(unattachedSentence(groups[0])).toBe(
      "Run 2 produced 1 file that isn't linked to any output.",
    );
    expect(unattachedSentence(groups[2])).toMatch(/^A card produced/);
  });
});

describe("groupVersions", () => {
  const output = OUTPUT({
    deliveries: [
      DELIVERY({ id: "d3", run_id: "run_c", delivered_at: NOW - 100 }),
      DELIVERY({ id: "d1", run_id: "run_a", delivered_at: NOW - 900 }),
      DELIVERY({ id: "d2", run_id: "run_a", delivered_at: NOW - 800 }),
      DELIVERY({ id: "d4", run_id: null, delivered_at: NOW - 50 }),
    ],
  });
  it("makes one version per run, newest version first", () => {
    const versions = groupVersions(output, [
      ARTIFACT({ id: "d:d1", run_no: 1 }),
      ARTIFACT({ id: "d:d3", run_no: 3 }),
    ]);
    expect(versions.map((v) => [v.version, v.runNo, v.files.map((f) => f.delivery.id)])).toEqual([
      [3, null, ["d4"]],
      [2, 3, ["d3"]],
      [1, 1, ["d1", "d2"]],
    ]);
    expect(versions[2].deliveredAt).toBe(NOW - 800);
    expect(versions[2].files[0].artifact?.id).toBe("d:d1");
  });
  it("labels versions", () => {
    expect(versionLabel({ version: 2, runNo: 3, runId: "r" })).toBe("v2 · run 3");
    expect(versionLabel({ version: 1, runNo: null, runId: "r" })).toBe("v1 · from a run");
    expect(versionLabel({ version: 1, runNo: null, runId: null })).toBe("v1 · added by hand");
  });
  it("is empty for an undelivered output", () => {
    expect(groupVersions(OUTPUT())).toEqual([]);
  });
});

describe("fallback from project.outputs", () => {
  it("derives deliverables from deliveries with versions", () => {
    const list = artifactsFromOutputs([
      OUTPUT({
        deliveries: [
          DELIVERY({ id: "d1", delivered_at: NOW - 10 }),
          DELIVERY({
            id: "d2",
            run_id: "run_b",
            link_kind: "file",
            link_ref: "leo/p/123e4567-e89b-42d3-a456-426614174000-Deck.pdf",
            delivered_at: NOW,
          }),
        ],
      }),
    ]);
    expect(list.map((a) => [a.id, a.title, a.ext, a.version, a.kind])).toEqual([
      ["d:d2", "Deck.pdf", "pdf", 2, "deliverable"],
      ["d:d1", "mou-v1.docx", "docx", 1, "deliverable"],
    ]);
    expect(list[1].href).toBe("https://example.com/mou-v1.docx");
    expect(list[0].href).toBeNull();
    expect(isOpenable(list[0])).toBe(true);
  });
});

describe("small helpers", () => {
  it("suggests the first open required output with nothing delivered", () => {
    const outputs = [
      OUTPUT({ id: "acc", seq: 1, status: "accepted" }),
      OUTPUT({ id: "has", seq: 2, status: "delivered", deliveries: [DELIVERY()] }),
      OUTPUT({ id: "opt", seq: 3, required: 0 }),
      OUTPUT({ id: "req", seq: 4 }),
    ];
    expect(suggestedOutput(outputs)?.id).toBe("req");
    expect(suggestedOutput([OUTPUT({ status: "dropped" })])).toBeNull();
  });
  it("builds the deliver body from the file's provenance", () => {
    expect(attachBody(ARTIFACT())).toEqual({
      label: "mou-qingtian.docx",
      run_id: "run_a",
      task_id: "t_1",
      link_kind: "workspace",
      link_ref: "/ws/t_1/mou-qingtian.docx",
      profile: "default",
    });
  });
  it("labels states, titles and provenance", () => {
    expect(outputStateLabel("dropped")).toBe("superseded");
    expect(outputStateLabel("in_progress")).toBe("in progress");
    expect(shortTitle("2 MOUs in docx, bilingual")).toBe("2 MOUs in docx, bi…");
    expect(shortTitle("short")).toBe("short");
    expect(provenanceLabel(ARTIFACT())).toBe("run 1 · Draft both MOUs");
    expect(provenanceLabel(ARTIFACT({ run_no: null, card_title: null, kind: "deliverable" }))).toBe(
      "added by hand",
    );
  });
});
