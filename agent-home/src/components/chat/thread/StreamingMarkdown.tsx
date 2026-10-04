"use client";

import { memo, useMemo } from "react";

import { RichText } from "@/components/chat/RichText";

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)/;

/**
 * Split Markdown into top-level blocks at blank lines that are not inside a
 * fenced (``` / ~~~) code block. An unterminated fence runs to the end of the
 * text (the trailing block of a streaming reply). Blank lines followed by an
 * indented line, or by another item of a list block, stay inside the block so
 * list continuations and loose lists keep rendering as one list.
 */
export function splitMarkdownBlocks(text: string): string[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: string[] = [];
  let cur: string[] = [];
  let blanks = 0;
  let fence: { ch: string; len: number } | null = null;
  const flush = () => {
    if (cur.length > 0) blocks.push(cur.join("\n"));
    cur = [];
  };
  for (const line of lines) {
    if (fence) {
      cur.push(line);
      const close = FENCE_CLOSE.exec(line);
      if (close && close[1][0] === fence.ch && close[1].length >= fence.len) {
        fence = null;
      }
      continue;
    }
    if (line.trim() === "") {
      if (cur.length > 0) blanks += 1;
      continue;
    }
    if (blanks > 0) {
      const continues =
        /^[ \t]/.test(line) || (LIST_ITEM.test(line) && LIST_ITEM.test(cur[0]));
      if (continues) {
        for (let i = 0; i < blanks; i += 1) cur.push("");
      } else {
        flush();
      }
      blanks = 0;
    }
    cur.push(line);
    const open = FENCE_OPEN.exec(line);
    // A backtick fence's info string may not contain backticks.
    if (open && !(open[1][0] === "`" && open[2].includes("`"))) {
      fence = { ch: open[1][0], len: open[1].length };
    }
  }
  flush();
  return blocks;
}

const Block = memo(function Block({ text }: { text: string }) {
  return <RichText content={text} />;
});

/**
 * Markdown for a reply that may still be streaming. While `streaming`, the
 * text is rendered block by block through a memoized component so finished
 * blocks are parsed once and only the trailing block re-parses per update.
 * Once done it renders one `RichText` over the whole text (exact final
 * semantics). Every path goes through `RichText`, so sanitization holds.
 */
export function StreamingMarkdown({
  content,
  streaming,
}: {
  content: string;
  streaming: boolean;
}) {
  const blocks = useMemo(
    () => (streaming ? splitMarkdownBlocks(content) : null),
    [content, streaming],
  );
  if (!blocks) return <RichText content={content} />;
  return (
    <div data-component="StreamingMarkdown" className="space-y-2">
      {blocks.map((block, i) => (
        <Block key={i} text={block} />
      ))}
    </div>
  );
}
