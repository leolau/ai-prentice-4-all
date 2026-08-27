/**
 * Chat attachment size cap in bytes. Enforced server-side by
 * `/api/chat/upload` and pre-checked client-side in the composer so an
 * oversize file is refused before a long upload round-trip.
 */
export const UPLOAD_MAX_BYTES = 100 * 1024 * 1024;
