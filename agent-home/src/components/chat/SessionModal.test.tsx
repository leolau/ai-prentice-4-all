import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SessionModal } from "@/components/chat/SessionModal";
import type { SessionSummary, SessionTag, TagSuggestion } from "@/types";

const SESSION: SessionSummary = {
  id: "home_1",
  source: "agent_home",
  title: "My Chat",
  preview: null,
  message_count: 12,
  started_at: 1700000000,
  last_active: 1700000100,
  ended_at: null,
  is_active: true,
  input_tokens: 1000,
  output_tokens: 500,
  cache_read_tokens: 200,
  cache_write_tokens: 100,
  reasoning_tokens: 50,
};

const TAGS: SessionTag[] = [
  { id: "t1", name: "bug", color: "red" },
  { id: "t2", name: "feature", color: "green" },
];

const ALL_TAGS: SessionTag[] = [
  ...TAGS,
  { id: "t3", name: "urgent", color: "amber" },
  { id: "t4", name: "docs", color: "blue" },
];

const SUGGESTIONS: TagSuggestion[] = [
  { tag_name: "debugging", is_new: true, reason: "conversation about debugging", confidence: 0.9 },
  { tag_name: "bug", is_new: false, reason: "bug-related discussion", confidence: 0.7 },
];

function render(props: Partial<Parameters<typeof SessionModal>[0]> = {}) {
  return renderToStaticMarkup(
    <SessionModal
      session={SESSION}
      onClose={() => {}}
      onRename={async () => {}}
      onArchive={async () => {}}
      {...props}
    />,
  );
}

describe("SessionModal", () => {
  it("renders the modal container with data-component", () => {
    const html = render();
    expect(html).toContain('data-component="SessionModal"');
  });

  it("renders the session title in the name input", () => {
    const html = render();
    expect(html).toContain("My Chat");
  });

  it("renders context window section with total tokens", () => {
    const html = render();
    expect(html).toContain("Context Window");
    // Total = 1000 + 500 + 200 + 100 + 50 = 1850
    expect(html).toContain("1.9K");
  });

  it("renders context window breakdown rows when tokens > 0", () => {
    const html = render();
    expect(html).toContain("Input");
    expect(html).toContain("Output");
    expect(html).toContain("Cache read");
    expect(html).toContain("Cache write");
    expect(html).toContain("Reasoning");
  });

  it("renders 'No token data' when all tokens are 0", () => {
    const html = render({
      session: { ...SESSION, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0 },
    });
    expect(html).toContain("No token data");
  });

  it("renders tag chips when tags provided", () => {
    const html = render({ tags: TAGS });
    expect(html).toContain("bug");
    expect(html).toContain("feature");
  });

  it("renders 'No tags yet' when tags array is empty", () => {
    const html = render({ tags: [] });
    expect(html).toContain("No tags yet");
  });

  it("renders association picker when onAddTag and allTags are provided", () => {
    const html = render({ tags: TAGS, allTags: ALL_TAGS, onAddTag: async () => {} });
    expect(html).toContain("Associate tag");
    // Unassociated tags appear as options
    expect(html).toContain("urgent");
    expect(html).toContain("docs");
    // Already-associated tags do not appear as options (only in the tag chips)
    // The select should not contain "bug" or "feature" as options
    // (they're already shown as chips above)
  });

  it("shows 'All tags associated' when no unassociated tags remain", () => {
    const html = render({ tags: ALL_TAGS, allTags: ALL_TAGS, onAddTag: async () => {} });
    expect(html).toContain("All tags associated");
    expect(html).not.toContain("Associate tag");
  });

  it("shows 'No tags defined yet' when allTags is empty", () => {
    const html = render({ tags: [], allTags: [], onAddTag: async () => {} });
    expect(html).toContain("No tags defined yet");
  });

  it("does not render association picker when onAddTag is not provided", () => {
    const html = render({ tags: TAGS, allTags: ALL_TAGS });
    expect(html).not.toContain("Associate tag");
  });

  it("renders tag remove button when onRemoveTag is provided", () => {
    const html = render({ tags: TAGS, onRemoveTag: async () => {} });
    expect(html).toContain('aria-label="Remove tag bug"');
    expect(html).toContain('aria-label="Remove tag feature"');
  });

  it("renders suggestions with Accept/Dismiss buttons", () => {
    const html = render({
      tags: TAGS,
      tagSuggestions: SUGGESTIONS,
      onAcceptSuggestion: async () => {},
      onDismissSuggestion: async () => {},
    });
    expect(html).toContain("debugging");
    expect(html).toContain("bug-related discussion");
    expect(html).toContain("Accept");
    expect(html).toContain("Dismiss");
  });

  it("renders 'new' badge for suggestions with is_new=true", () => {
    const html = render({
      tags: TAGS,
      tagSuggestions: SUGGESTIONS,
      onAcceptSuggestion: async () => {},
      onDismissSuggestion: async () => {},
    });
    expect(html).toContain("new");
  });

  it("renders statistics section", () => {
    const html = render();
    expect(html).toContain("Statistics");
    expect(html).toContain("Messages");
    expect(html).toContain("12");
    expect(html).toContain("agent_home");
    expect(html).toContain("home_1");
  });

  it("renders Archive and Save buttons", () => {
    const html = render();
    expect(html).toContain("Archive");
    expect(html).toContain("Save");
  });
});
