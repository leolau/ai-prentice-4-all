// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const parsed: string[] = [];
vi.mock("@/components/chat/RichText", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/chat/RichText")>();
  return {
    RichText: (props: { content: string }) => {
      parsed.push(props.content);
      return actual.RichText(props);
    },
  };
});

import {
  StreamingMarkdown,
  splitMarkdownBlocks,
} from "@/components/chat/thread/StreamingMarkdown";

describe("splitMarkdownBlocks", () => {
  it("splits paragraphs on blank lines", () => {
    expect(splitMarkdownBlocks("one\ntwo\n\nthree\n\n\nfour")).toEqual([
      "one\ntwo",
      "three",
      "four",
    ]);
  });

  it("drops leading and trailing blank lines", () => {
    expect(splitMarkdownBlocks("\n\n  \nhello\n\n\n")).toEqual(["hello"]);
    expect(splitMarkdownBlocks("")).toEqual([]);
    expect(splitMarkdownBlocks("\n\n")).toEqual([]);
  });

  it("normalises CRLF line endings", () => {
    expect(splitMarkdownBlocks("a\r\nb\r\n\r\nc")).toEqual(["a\nb", "c"]);
  });

  it("keeps blank lines inside a ``` fence", () => {
    const md = "intro\n\n```js\nconst a = 1;\n\nconst b = 2;\n```\n\nafter";
    expect(splitMarkdownBlocks(md)).toEqual([
      "intro",
      "```js\nconst a = 1;\n\nconst b = 2;\n```",
      "after",
    ]);
  });

  it("keeps blank lines inside a ~~~ fence and ignores ``` inside it", () => {
    const md = "~~~\n```\n\nstill code\n~~~\n\nnext";
    expect(splitMarkdownBlocks(md)).toEqual(["~~~\n```\n\nstill code\n~~~", "next"]);
  });

  it("needs a closing fence at least as long as the opener", () => {
    const md = "````\n```\n\nx\n````\n\ny";
    expect(splitMarkdownBlocks(md)).toEqual(["````\n```\n\nx\n````", "y"]);
  });

  it("runs an unterminated fence to the end (streaming)", () => {
    const md = "text\n\n```py\nprint(1)\n\n\nprint(2)";
    expect(splitMarkdownBlocks(md)).toEqual([
      "text",
      "```py\nprint(1)\n\n\nprint(2)",
    ]);
  });

  it("keeps a loose list together but splits a following paragraph", () => {
    const md = "- a\n- b\n\n- c\n\n  more c\n\nParagraph";
    expect(splitMarkdownBlocks(md)).toEqual([
      "- a\n- b\n\n- c\n\n  more c",
      "Paragraph",
    ]);
  });

  it("keeps an ordered loose list together", () => {
    expect(splitMarkdownBlocks("1. one\n\n2. two\n\nend")).toEqual([
      "1. one\n\n2. two",
      "end",
    ]);
  });

  it("starts a new block for a list after a paragraph", () => {
    expect(splitMarkdownBlocks("Intro:\n\n- a\n- b")).toEqual(["Intro:", "- a\n- b"]);
  });

  it("round-trips: joined blocks keep all non-blank content", () => {
    const md = "# T\n\npara\n\n```\nx\n\ny\n```\n\n| a |\n| - |\n| 1 |";
    const blocks = splitMarkdownBlocks(md);
    expect(blocks.join("\n\n")).toBe(md);
  });
});

describe("StreamingMarkdown", () => {
  beforeEach(() => {
    parsed.length = 0;
  });
  afterEach(cleanup);

  it("re-parses only the trailing block while streaming", () => {
    const { container, rerender } = render(
      <StreamingMarkdown content={"# Title\n\nFirst para\n\nSec"} streaming />,
    );
    expect(parsed).toEqual(["# Title", "First para", "Sec"]);
    parsed.length = 0;
    rerender(<StreamingMarkdown content={"# Title\n\nFirst para\n\nSecond"} streaming />);
    expect(parsed).toEqual(["Second"]);
    parsed.length = 0;
    rerender(
      <StreamingMarkdown content={"# Title\n\nFirst para\n\nSecond\n\nThi"} streaming />,
    );
    expect(parsed).toEqual(["Thi"]);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
  });

  it("renders the final reply with a single parse once streaming stops", () => {
    const content = "# Title\n\n- a\n- b";
    const { container } = render(<StreamingMarkdown content={content} streaming={false} />);
    expect(parsed).toEqual([content]);
    expect(container.querySelectorAll('[data-component="RichText"]')).toHaveLength(1);
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("keeps RichText sanitization while streaming", () => {
    const { container } = render(
      <StreamingMarkdown
        content={'ok <b>bold</b>\n\n<script>alert(1)</script> <a href="javascript:alert(2)">x</a>'}
        streaming
      />,
    );
    expect(container.innerHTML).toContain("<b>bold</b>");
    expect(container.innerHTML).not.toContain("<script");
    expect(container.innerHTML).not.toContain("javascript:");
  });
});
