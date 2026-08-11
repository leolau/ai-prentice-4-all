/**
 * POST /api/chat/send — BFF send-one-turn (FG-20 Wave C1).
 *
 * Body: `{ sessionId?, message, attachments?, profile? }`. When `sessionId` is absent a
 * conversation is created first. Forwards to the Python API
 * `POST /api/sessions/{id}/chat` under the bridged C1 principal, which drives
 * one one-brain `AIAgent` turn against the shared `SessionDB`. The browser
 * never calls the AI layer directly and never re-implements the loop.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromBody } from "@/lib/chat/profile";
import { mediaRef } from "@/lib/chat/media-ref";
import { canReadMediaPath, createMediaSignedUrl } from "@/lib/supabase/storage";
import type { AgentAttachmentPayload, ChatAttachment, Principal } from "@/types";

interface SendBody {
  sessionId?: unknown;
  message?: unknown;
  attachments?: unknown;
  /** Which profile's brain answers this turn (FG-28); default when absent. */
  profile?: unknown;
}

/**
 * Append attachment references so the persisted turn carries the media links.
 * The bucket is private, so what is persisted is the **path-bearing BFF ref**
 * (re-signed per read), never a URL.
 */
function withAttachments(message: string, attachments: ChatAttachment[]): string {
  if (attachments.length === 0) return message;
  const refs = attachments
    .map((a) => `![${a.name}](${mediaRef(a.path)})`)
    .join("\n");
  return message ? `${message}\n\n${refs}` : refs;
}

/**
 * Read the client's attachment list, keeping only objects the principal owns.
 * A crafted `path` pointing at another member's object is dropped here as well
 * as refused by the read route — the transcript never references foreign media.
 */
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

/**
 * Mint a short-lived signed URL per owned attachment so the Python endpoint can
 * download the bytes once into its document cache and make the upload readable
 * by the brain. Attachments that fail to sign are dropped (the transcript link
 * still renders) rather than failing the whole turn.
 */
async function signAttachments(
  attachments: ChatAttachment[],
): Promise<AgentAttachmentPayload[]> {
  const signed = await Promise.all(
    attachments.map(async (a) => {
      const s = await createMediaSignedUrl(a.path).catch(() => null);
      if (!s) return null;
      return {
        name: a.name,
        content_type: a.content_type,
        size: a.size,
        url: s.url,
      } satisfies AgentAttachmentPayload;
    }),
  );
  return signed.filter((x): x is AgentAttachmentPayload => x !== null);
}

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: SendBody;
  try {
    body = (await request.json()) as SendBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const rawMessage = typeof body.message === "string" ? body.message.trim() : "";
  const attachments = readAttachments(principal, body.attachments);
  if (!rawMessage && attachments.length === 0) {
    return NextResponse.json(
      { error: "empty_message", detail: "A message or attachment is required." },
      { status: 400 },
    );
  }
  const message = withAttachments(rawMessage, attachments);

  try {
    const client = await apiClientForRequest({ profile: profileFromBody(body) });
    let sessionId =
      typeof body.sessionId === "string" && body.sessionId ? body.sessionId : "";
    if (!sessionId) {
      const created = await client.createSession();
      sessionId = created.session_id;
    }
    const agentAttachments = await signAttachments(attachments);
    const reply = await client.sendChat(sessionId, message, agentAttachments);
    return NextResponse.json(reply);
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
