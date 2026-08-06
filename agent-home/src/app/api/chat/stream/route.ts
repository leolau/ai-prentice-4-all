/**
 * POST /api/chat/stream — BFF streaming send-one-turn with approval surface.
 *
 * Body: `{ sessionId?, message, attachments? }`. When `sessionId` is absent a
 * conversation is created first. Proxies the SSE stream from the Python API
 * `POST /api/sessions/{id}/chat/stream` under the bridged C1 principal so the
 * browser gets live assistant deltas AND `approval.request` events for
 * tool-approval-gated tools (which the non-streaming `/api/chat/send` path
 * cannot surface). The resolved session id is returned in the
 * `X-Hermes-Session-Id` header so the client can attribute a new conversation.
 * The browser never calls the AI layer directly and never re-implements the loop.
 */
import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { mediaRef } from "@/lib/chat/media-ref";
import { canReadMediaPath } from "@/lib/supabase/storage";
import type { ChatAttachment, Principal } from "@/types";

interface SendBody {
  sessionId?: unknown;
  message?: unknown;
  attachments?: unknown;
}

function withAttachments(message: string, attachments: ChatAttachment[]): string {
  if (attachments.length === 0) return message;
  const refs = attachments
    .map((a) => {
      // Images embed inline (`![]`) for a preview; any other file type
      // (PDF/DOC/XLS/…) is a download link (`[]`) so it isn't treated as a
      // broken image.
      const marker = a.content_type.startsWith("image/") ? "!" : "";
      return `${marker}[${a.name}](${mediaRef(a.path)})`;
    })
    .join("\n");
  return message ? `${message}\n\n${refs}` : refs;
}

function readAttachments(principal: Principal, raw: unknown): ChatAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatAttachment[] = [];
  for (const item of raw) {
    if (
      item &&
      typeof item === "object" &&
      typeof (item as ChatAttachment).path === "string" &&
      typeof (item as ChatAttachment).name === "string" &&
      canReadMediaPath(principal, (item as ChatAttachment).path)
    ) {
      const a = item as ChatAttachment;
      out.push({
        path: a.path,
        name: a.name,
        content_type: typeof a.content_type === "string" ? a.content_type : "",
        size: typeof a.size === "number" ? a.size : 0,
      });
    }
  }
  return out;
}

function jsonError(error: string, detail: string, status: number): Response {
  return new Response(JSON.stringify({ error, detail }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export async function POST(request: Request): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return jsonError("unauthenticated", "Sign in to continue.", 401);
  }

  let body: SendBody;
  try {
    body = (await request.json()) as SendBody;
  } catch {
    return jsonError("invalid_json", "Malformed request body.", 400);
  }

  const rawMessage = typeof body.message === "string" ? body.message.trim() : "";
  const attachments = readAttachments(principal, body.attachments);
  if (!rawMessage && attachments.length === 0) {
    return jsonError("empty_message", "A message or attachment is required.", 400);
  }
  const message = withAttachments(rawMessage, attachments);

  try {
    const client = await apiClientForRequest();
    let sessionId =
      typeof body.sessionId === "string" && body.sessionId ? body.sessionId : "";
    if (!sessionId) {
      const created = await client.createSession();
      sessionId = created.session_id;
    }
    const upstream = await client.openChatStream(sessionId, message);
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
        "x-hermes-session-id": sessionId,
      },
    });
  } catch (err) {
    if (err instanceof HermesApiError) {
      return jsonError("api_error", err.message, err.status);
    }
    return jsonError("api_unreachable", "The AI layer could not be reached.", 502);
  }
}
