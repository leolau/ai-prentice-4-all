/**
 * Client-side reader for the agent-home chat stream (`POST /api/chat/stream`).
 *
 * The BFF proxies the Python `chat/stream` SSE verbatim; this parses the
 * `event:`/`data:` frames and dispatches them to typed handlers. The one event
 * that is new to agent-home is `approval.request`: a tool gated by
 * `approvals.tools` (e.g. calendar) blocks the turn and asks for approval, so
 * the caller renders an approve/deny card and resolves it via
 * `POST /api/chat/approval`. Runs in the browser (client component only).
 */
import type { ChatApprovalRequest, ChatAttachment } from "@/types";

export interface ChatStreamHandlers {
  /** Incremental assistant text. */
  onDelta?(delta: string): void;
  /** A tool is blocked awaiting the user's approval decision. */
  onApproval?(req: ChatApprovalRequest): void;
  /** The turn finished; `content` is the full assistant message. */
  onCompleted?(content: string, sessionId: string): void;
  /** A terminal, user-safe error message from the stream. */
  onError?(message: string): void;
}

export interface ChatStreamParams {
  sessionId: string | null;
  message: string;
  attachments: ChatAttachment[];
  signal?: AbortSignal;
  /**
   * Which profile answers this turn (FG-28). The whole turn runs under that
   * profile's `HERMES_HOME` — its SOUL, config, skills, memory and
   * credentials — so omitting it here would run the default brain and file the
   * reply in the selected profile's history.
   */
  profile?: string;
}

interface StreamFrame {
  event: string;
  data: Record<string, unknown>;
}

/** Parse one SSE block (`event:`/`data:` lines) into a frame, or null. */
function parseFrame(block: string): StreamFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
  } catch {
    return null;
  }
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * Send one turn and stream the reply. Resolves to the session id the turn
 * landed on (a new conversation is created server-side when `sessionId` is
 * null). Rejects on transport failure; stream-level errors are delivered via
 * `onError`.
 */
export async function streamChatTurn(
  params: ChatStreamParams,
  handlers: ChatStreamHandlers,
): Promise<{ sessionId: string }> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      sessionId: params.sessionId,
      message: params.message,
      attachments: params.attachments,
      profile: params.profile,
    }),
    signal: params.signal,
  });

  if (!res.ok || !res.body) {
    let detail = "The message could not be sent.";
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Non-JSON error body — keep the generic message.
    }
    throw new Error(detail);
  }

  const sessionId = res.headers.get("x-hermes-session-id") || params.sessionId || "";
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completedContent = "";
  let landedSessionId = sessionId;

  const dispatch = (frame: StreamFrame): void => {
    const { event, data } = frame;
    if (event === "assistant.delta") {
      const delta = str(data.delta);
      if (delta) handlers.onDelta?.(delta);
    } else if (event === "approval.request") {
      handlers.onApproval?.({
        runId: str(data.run_id) ?? "",
        toolName: str(data.tool_name),
        command: str(data.command),
        description: str(data.description),
        patternKey: str(data.pattern_key),
        choices: Array.isArray(data.choices)
          ? (data.choices.filter((c) => typeof c === "string") as string[])
          : ["once", "deny"],
      });
    } else if (event === "assistant.completed") {
      completedContent = str(data.content) ?? completedContent;
    } else if (event === "run.completed") {
      landedSessionId = str(data.session_id) ?? landedSessionId;
    } else if (event === "error") {
      handlers.onError?.(str(data.message) ?? "The turn failed.");
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (block.trim() && !block.startsWith(":")) {
        const frame = parseFrame(block);
        if (frame) dispatch(frame);
      }
      sep = buffer.indexOf("\n\n");
    }
  }

  handlers.onCompleted?.(completedContent, landedSessionId);
  return { sessionId: landedSessionId };
}
