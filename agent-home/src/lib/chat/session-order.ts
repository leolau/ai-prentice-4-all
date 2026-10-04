/**
 * Pick the conversation to open after `archivedId` is archived out of the
 * displayed list. Returns the next remaining session's id in display order
 * (preferring the one after the archived row, else the one before), or `null`
 * when nothing remains — the caller then falls back to the empty state.
 *
 * Archiving the open conversation must switch to a neighbouring conversation,
 * never drop the user into a fresh "New conversation".
 */
export function nextActiveAfterArchive(
  orderedIds: string[],
  archivedId: string,
): string | null {
  const remaining = orderedIds.filter((id) => id !== archivedId);
  if (remaining.length === 0) return null;
  const idx = orderedIds.indexOf(archivedId);
  if (idx === -1) return remaining[0];
  // First still-present id at or after the archived slot, else the last one
  // before it.
  for (let i = idx + 1; i < orderedIds.length; i += 1) {
    if (orderedIds[i] !== archivedId) return orderedIds[i];
  }
  for (let i = idx - 1; i >= 0; i -= 1) {
    if (orderedIds[i] !== archivedId) return orderedIds[i];
  }
  return remaining[0];
}
