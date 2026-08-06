/**
 * Pure helpers for the in-flight chat turn's message list.
 *
 * The streaming turn appends a placeholder assistant message and then rewrites
 * its content on every `assistant.delta` / `assistant.completed` event. The
 * update MUST NOT rely on object identity: the first delta replaces the
 * placeholder with a new object, so an identity match (`m === live`) silently
 * stops matching after the first delta and the reply is truncated to its first
 * token. Targeting the trailing assistant message by position is stable across
 * those replacements. This is the regression fix for "only shows the first
 * couple of characters then stopped".
 */
import type { ChatMessage } from "@/types";

/**
 * Return a new list with the content of the trailing assistant message set to
 * `content`. If the last message is not an assistant turn (nothing streaming),
 * the list is returned unchanged.
 */
export function setLastAssistantContent(
  messages: ChatMessage[],
  content: string,
): ChatMessage[] {
  if (messages.length === 0) return messages;
  const idx = messages.length - 1;
  const last = messages[idx];
  if (last.role !== "assistant") return messages;
  if (last.content === content) return messages;
  const next = messages.slice();
  next[idx] = { ...last, content };
  return next;
}
