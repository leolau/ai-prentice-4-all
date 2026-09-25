/**
 * Chat attachment size cap in bytes. **Unlimited by default** — Supabase
 * Storage enforces its own ceiling (`FILE_SIZE_LIMIT` on the storage
 * service, or the bucket's `file_size_limit`), and uploads stream through
 * the BFF which buffers them anyway, so a second hard cap here only added a
 * refusal point that disagreed with the backend's real limit.
 *
 * A deploy that still wants a BFF-side guard (proxy budget, box memory) can
 * set `AGENT_HOME_UPLOAD_MAX_BYTES` to a positive byte count.
 */
export function uploadMaxBytes(): number {
  const raw = Number(process.env.AGENT_HOME_UPLOAD_MAX_BYTES);
  return Number.isFinite(raw) && raw > 0 ? raw : Number.POSITIVE_INFINITY;
}

/**
 * The client-side *advisory* threshold (100 MB): files bigger than this ask
 * the user to confirm before uploading — very large uploads take a while and
 * buffer in memory on the box — but are never refused outright.
 */
export const UPLOAD_WARN_BYTES = 100 * 1024 * 1024;

/** The refusal copy for a configured cap — kept in one place for the routes. */
export function uploadTooLargeDetail(maxBytes: number): string {
  const mb = Math.ceil(maxBytes / (1024 * 1024));
  return `File exceeds the ${mb} MB limit.`;
}
