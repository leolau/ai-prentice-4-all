import { ChatMedia } from "@/components/chat/ChatMedia";
import { RichText } from "@/components/chat/RichText";
import { mediaRefPath } from "@/lib/chat/media-ref";
import type { ChatMessage } from "@/types";

interface Segment {
  kind: "text" | "image";
  value: string;
  alt?: string;
}

/** Split content into text + inline `![alt](url)` image segments for render. */
function segment(content: string): Segment[] {
  const out: Segment[] = [];
  const re = /!\[([^\]]*)\]\((\S+?)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    if (m.index > last) {
      out.push({ kind: "text", value: content.slice(last, m.index) });
    }
    out.push({ kind: "image", value: m[2], alt: m[1] });
    last = re.lastIndex;
  }
  if (last < content.length) {
    out.push({ kind: "text", value: content.slice(last) });
  }
  return out;
}

/** A user turn: literal text plus any inline `![alt](ref)` media they sent. */
function UserContent({ content }: { content: string }) {
  return (
    <>
      {segment(content).map((s, i) => {
        if (s.kind !== "image") return <span key={i}>{s.value}</span>;
        const path = mediaRefPath(s.value);
        return path ? (
          <ChatMedia key={i} path={path} alt={s.alt || "attachment"} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={i}
            src={s.value}
            alt={s.alt || "attachment"}
            className="mt-1 max-h-64 rounded-lg"
          />
        );
      })}
    </>
  );
}

/**
 * One chat turn rendered as a mobile bubble — user turns align right, the
 * agent's align left. The agent's reply is rendered as sanitized Markdown/HTML
 * (tables, lists, headings, code, links, inline media). User turns stay literal
 * text (their input is never interpreted as markup) plus any media they sent.
 */
export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const content = message.content ?? "";
  return (
    <div
      data-component="MessageBubble"
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`max-w-[85%] break-words rounded-2xl px-3 py-2 text-sm ${
          isUser
            ? "whitespace-pre-wrap bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
            : "bg-[var(--color-surface-2)] text-[var(--color-fg)]"
        }`}
      >
        {isUser ? (
          <UserContent content={content} />
        ) : (
          <RichText content={content} />
        )}
      </div>
    </div>
  );
}
