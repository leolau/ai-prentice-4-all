"use client";

import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { ChatMedia } from "@/components/chat/ChatMedia";
import { mediaRefPath } from "@/lib/chat/media-ref";

/**
 * Renders an assistant reply as Markdown plus a *sanitized* subset of inline
 * HTML, so the agent can use tables, lists, headings, code, links and simple
 * HTML elements. `rehype-raw` lifts raw HTML into the tree and
 * `rehype-sanitize` then strips anything unsafe (scripts, event handlers,
 * `javascript:` URLs, styles) — the render is fail-closed, so untrusted model
 * output can never inject executable markup.
 */
export function RichText({ content }: { content: string }) {
  return (
    <div
      data-component="RichText"
      className="space-y-2 text-sm leading-relaxed [overflow-wrap:anywhere]"
    >
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={{
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="underline underline-offset-2"
            >
              {children}
            </a>
          ),
          // Inline images may be private-bucket refs that must resolve through
          // the signing BFF; fall back to a plain <img> for absolute URLs.
          img: ({ src, alt }) => {
            const url = typeof src === "string" ? src : "";
            const path = mediaRefPath(url);
            if (path) return <ChatMedia path={path} alt={alt || "attachment"} />;
            return (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={url}
                alt={alt || "attachment"}
                className="mt-1 max-h-64 rounded-lg"
              />
            );
          },
          h1: ({ children }) => (
            <h1 className="text-base font-semibold">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-semibold">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold">{children}</h3>
          ),
          ul: ({ children }) => (
            <ul className="list-disc space-y-1 pl-5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal space-y-1 pl-5">{children}</ol>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-[var(--color-border)] pl-3 text-[var(--color-muted)]">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) => {
            const isBlock = (className ?? "").includes("language-");
            if (isBlock) {
              return (
                <code className={`${className ?? ""} font-mono`}>{children}</code>
              );
            }
            return (
              <code className="rounded bg-[var(--color-surface)] px-1 py-0.5 font-mono text-[0.85em]">
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-lg bg-[var(--color-surface)] p-3 text-xs">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-[var(--color-border)] px-2 py-1 text-left font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-[var(--color-border)] px-2 py-1 align-top">
              {children}
            </td>
          ),
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}
