import { describe, expect, it } from "vitest";

import {
  toProjectLink,
  upsertLink,
  withoutLink,
  addedByLabel,
  fileBadge,
  fileIdentity,
  formatBytes,
  groupInputLinks,
  inputCount,
  inputRoleLabel,
  linkKindToRole,
  memoryLink,
  memoryTitle,
  newFilesOnly,
  noteText,
  parseLinkOrNote,
  roleToLinkKind,
  sameLink,
  suggestionQuery,
} from "@/components/projects/inputs/inputKinds";
import type { ProjectLink } from "@/types";

const LINK = (over: Partial<ProjectLink>): ProjectLink => ({
  project_id: "prj_1",
  kind: "file",
  profile: "default",
  ref: "u/p/x.pdf",
  label: null,
  added_by: "leo",
  added_at: 100,
  ...over,
});

describe("role → link kind", () => {
  it("maps template to sample and reference to reference", () => {
    expect(roleToLinkKind("template")).toBe("sample");
    expect(roleToLinkKind("reference")).toBe("reference");
  });
  it("round-trips through linkKindToRole", () => {
    expect(linkKindToRole(roleToLinkKind("template"))).toBe("template");
    expect(linkKindToRole(roleToLinkKind("reference"))).toBe("reference");
    expect(linkKindToRole("file")).toBeNull();
    expect(linkKindToRole("memory")).toBeNull();
  });
});

describe("parseLinkOrNote", () => {
  it("returns null for blank text", () => {
    expect(parseLinkOrNote("   ")).toBeNull();
  });
  it("keeps a full URL as a url link", () => {
    expect(parseLinkOrNote(" https://drive.google.com/x ")).toEqual({
      kind: "url",
      ref: "https://drive.google.com/x",
      label: "https://drive.google.com/x",
    });
  });
  it("adds https to a bare domain", () => {
    expect(parseLinkOrNote("example.com/a?b=1")).toEqual({
      kind: "url",
      ref: "https://example.com/a?b=1",
      label: "example.com/a?b=1",
    });
  });
  it("turns anything else into a note reference", () => {
    const entry = parseLinkOrNote("Revenue split is 60/40,\n  ask Leo first");
    expect(entry?.kind).toBe("reference");
    expect(entry?.ref).toBe("note:Revenue split is 60/40,\n  ask Leo first");
    expect(entry?.label).toBe("Revenue split is 60/40, ask Leo first");
    expect(noteText(entry!.ref)).toBe("Revenue split is 60/40,\n  ask Leo first");
  });
  it("does not treat a sentence with a dot as a URL", () => {
    expect(parseLinkOrNote("Use v2. Not v1")?.kind).toBe("reference");
  });
  it("clips long labels", () => {
    const entry = parseLinkOrNote("x ".repeat(200));
    expect(entry!.label.length).toBeLessThanOrEqual(120);
    expect(entry!.label.endsWith("…")).toBe(true);
  });
});

describe("memory helpers", () => {
  it("titles by document, topic, then text", () => {
    expect(memoryTitle({ text: "t", topic: "topic", document_title: "Doc" })).toBe("Doc");
    expect(memoryTitle({ text: "t", topic: "topic", document_title: null })).toBe("topic");
    expect(memoryTitle({ text: "  some\n text ", topic: null })).toBe("some text");
  });
  it("builds a memory link from a row", () => {
    expect(memoryLink({ id: "mem_1", text: "ConnectAR details", topic: null })).toEqual({
      kind: "memory",
      ref: "mem_1",
      label: "ConnectAR details",
    });
  });
  it("suggests from the goal, else the description's first sentence", () => {
    expect(suggestionQuery("  Draft the MOUs ", "Ignore")).toBe("Draft the MOUs");
    expect(suggestionQuery("", "Five parties sign. Then more.")).toBe("Five parties sign.");
    expect(suggestionQuery("ab")).toBe("");
    expect(suggestionQuery("x".repeat(300)).length).toBe(200);
  });
});

describe("groupInputLinks", () => {
  it("puts uploads of any role in files and notes/urls/todos in links", () => {
    const groups = groupInputLinks({
      file: [LINK({ ref: "a.pdf", added_at: 1 })],
      sample: [LINK({ kind: "sample", ref: "tmpl.docx", added_at: 3 })],
      reference: [
        LINK({ kind: "reference", ref: "ref.pdf", added_at: 2 }),
        LINK({ kind: "reference", ref: "note:hello", added_at: 5 }),
      ],
      url: [LINK({ kind: "url", ref: "https://x.test", added_at: 4 })],
      todo: [LINK({ kind: "todo", ref: "t1", added_at: 6 })],
      memory: [LINK({ kind: "memory", ref: "m1" })],
      goal: [LINK({ kind: "goal", ref: "g1" })],
    });
    expect(groups.files.map((l) => l.ref)).toEqual(["tmpl.docx", "ref.pdf", "a.pdf"]);
    expect(groups.memories.map((l) => l.ref)).toEqual(["m1"]);
    expect(groups.links.map((l) => l.ref)).toEqual(["t1", "note:hello", "https://x.test"]);
    expect(inputCount(groups)).toBe(7);
  });
  it("is empty for a project with no links", () => {
    expect(inputCount(groupInputLinks({}))).toBe(0);
  });
});

describe("labels", () => {
  it("names every role in plain words", () => {
    expect(inputRoleLabel({ kind: "sample", ref: "a.docx" })).toBe("Template to match");
    expect(inputRoleLabel({ kind: "reference", ref: "a.pdf" })).toBe("Reference to read");
    expect(inputRoleLabel({ kind: "reference", ref: "note:x" })).toBe("Note");
    expect(inputRoleLabel({ kind: "url", ref: "https://x" })).toBe("Link");
    expect(inputRoleLabel({ kind: "memory", ref: "m" })).toBe("Memory");
    expect(inputRoleLabel({ kind: "file", ref: "f" })).toBe("File");
  });
  it("says you for the caller", () => {
    expect(addedByLabel("leo", "leo")).toBe("you");
    expect(addedByLabel("mia", "leo")).toBe("mia");
    expect(addedByLabel(null, "leo")).toBeNull();
  });
  it("formats sizes and badges", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(84 * 1024)).toBe("84 KB");
    expect(formatBytes(3.5 * 1024 * 1024)).toBe("3.5 MB");
    expect(fileBadge("MOU.template.docx")).toBe("DOCX");
    expect(fileBadge("README")).toBe("FILE");
  });
  it("compares links by kind, profile and ref", () => {
    expect(sameLink(LINK({}), LINK({ label: "other" }))).toBe(true);
    expect(sameLink(LINK({}), LINK({ kind: "sample" }))).toBe(false);
  });
});

describe("file identity", () => {
  const f = (name: string, size = 1, lastModified = 1) => ({ name, size, lastModified });
  it("dedupes the same file dropped twice", () => {
    const known = [fileIdentity(f("a.pdf"))];
    expect(newFilesOnly(known, [f("a.pdf"), f("b.pdf"), f("b.pdf")]).map((x) => x.name)).toEqual([
      "b.pdf",
    ]);
  });
  it("treats a changed file with the same name as new", () => {
    expect(newFilesOnly([fileIdentity(f("a.pdf"))], [f("a.pdf", 2)])).toHaveLength(1);
  });
});

describe("toProjectLink / upsertLink / withoutLink", () => {
  const base = { projectId: "prj_1", profile: "default", addedBy: "leo", now: 100 };

  it("fills what a write's answer left out", () => {
    expect(toProjectLink({ kind: "memory", ref: "mem_1", label: "Co" }, base)).toEqual({
      project_id: "prj_1",
      kind: "memory",
      profile: "default",
      ref: "mem_1",
      label: "Co",
      added_by: "leo",
      added_at: 100,
      resolved: true,
    });
    expect(
      toProjectLink({ kind: "url", ref: "https://x", profile: "work", added_by: "yan", added_at: 5 }, base),
    ).toMatchObject({ profile: "work", added_by: "yan", added_at: 5, label: null });
  });

  it("upsert puts the link first without duplicating; without removes it", () => {
    const a = toProjectLink({ kind: "url", ref: "https://a" }, base);
    const b = toProjectLink({ kind: "url", ref: "https://b" }, base);
    const a2 = { ...a, label: "A" };
    expect(upsertLink([a, b], a2)).toEqual([a2, b]);
    expect(withoutLink([a, b], a)).toEqual([b]);
    // Same ref, other kind: a different pointer.
    expect(withoutLink([a], { ...a, kind: "reference" })).toEqual([a]);
  });
});

