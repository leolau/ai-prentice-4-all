/**
 * The durable in-transcript reference to a private-bucket media object
 * (FG-20 multi-user PR-5).
 *
 * A persisted turn can never carry a usable media URL: the bucket is private,
 * so a public URL would break isolation and a signed URL would expire. Instead
 * the transcript carries the **object path** wrapped in the BFF read route
 * (`/api/chat/media?path=…`), which re-signs on demand after checking that the
 * caller owns the path. This module is the single place that shape is built and
 * parsed, shared by the send route (writer) and the chat thread (reader).
 */

/** Route the browser fetches a signed URL from. */
export const MEDIA_ROUTE = "/api/chat/media";

/**
 * Route that streams the object's bytes. The browser never gets the signed URL
 * itself: it names Supabase as the *server* reaches it (loopback on the box),
 * so only this BFF-hosted URL is loadable from a phone or laptop.
 */
export const MEDIA_CONTENT_ROUTE = "/api/chat/media/content";

/** The in-transcript reference for one object path. */
export function mediaRef(path: string): string {
  return `${MEDIA_ROUTE}?path=${encodeURIComponent(path)}`;
}

/** The loadable URL for one object path. */
export function mediaContentRef(path: string): string {
  return `${MEDIA_CONTENT_ROUTE}?path=${encodeURIComponent(path)}`;
}

/**
 * The object path a media reference points at, or null when `value` is not one
 * (e.g. a plain external URL in an older transcript).
 */
export function mediaRefPath(value: string): string | null {
  if (!value.startsWith(`${MEDIA_ROUTE}?`)) return null;
  const query = value.slice(`${MEDIA_ROUTE}?`.length);
  const path = new URLSearchParams(query).get("path");
  return path || null;
}
