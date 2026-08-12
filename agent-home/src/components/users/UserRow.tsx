"use client";

import { useState } from "react";

import { ASSIGNABLE_ROLES } from "@/components/users/CreateUserForm";
import type { Member, Role } from "@/types";

/**
 * One roster row and its management controls.
 *
 * Two independent states are shown because they mean different things and the
 * remedies differ: the **account** is box-wide (a banned account is what a
 * created-but-not-activated user looks like, fixed with an activation link),
 * while the **enrolment** is this profile's (suspended here, fixed with
 * Restore). Collapsing them would send an admin to the wrong control.
 *
 * The owner row is read-only — `owner` cannot be created or assigned here, and
 * ownership moves via `hermes owner transfer`. The self row hides role and
 * removal controls: an admin who demotes or deletes themselves locks the
 * console, and Python refuses these regardless.
 */
export function UserRow({
  member,
  self,
  canDelete,
  busy,
  onChangeRole,
  onSetEnrolled,
  onInvite,
  onRevokeInvite,
  onRename,
  onLinkChannel,
  onDelete,
}: {
  member: Member;
  /** True when this row is the acting principal. */
  self: boolean;
  /** Hard delete is owner-only: the account is shared across profiles. */
  canDelete: boolean;
  busy: boolean;
  onChangeRole: (m: Member, r: Role) => void;
  onSetEnrolled: (m: Member, enrolled: boolean) => void;
  onInvite: (m: Member) => void;
  onRevokeInvite: (m: Member) => void;
  onRename: (m: Member, display: string) => void;
  onLinkChannel: (m: Member, platform: string, channelUserId: string) => void;
  onDelete: (m: Member) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [display, setDisplay] = useState(member.display);
  const [platform, setPlatform] = useState("telegram");
  const [handle, setHandle] = useState("");

  const invitation = member.invitation;

  return (
    <li
      data-component="UserRow"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">
          {member.display || member.email || member.user_id}
          {self ? <span className="text-[var(--color-muted)]"> (you)</span> : null}
        </p>
        <p className="truncate text-xs text-[var(--color-muted)]">
          {member.email || "(no email)"} · {member.user_id}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full bg-[var(--color-accent)] px-2 py-1 text-[var(--color-accent-fg)]">
            {member.role}
          </span>
          {member.enrolled ? null : (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-amber-300">
              suspended here
            </span>
          )}
          {member.active ? null : (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-red-300">
              {invitation && invitation.status === "open"
                ? "awaiting activation"
                : "account locked"}
            </span>
          )}
          {invitation ? (
            <span className="rounded-full bg-[var(--color-surface-2)] px-2 py-1">
              invite {invitation.status}
            </span>
          ) : null}
          {member.channels.map((c) => (
            <span key={c} className="rounded-full bg-[var(--color-surface-2)] px-2 py-1">
              {c}
            </span>
          ))}
        </div>
      </div>

      {member.is_owner ? (
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          Owner — managed via <code>hermes owner transfer</code>.
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {self ? (
            <span className="text-xs text-[var(--color-muted)]">
              Your own role and enrolment are managed by another admin.
            </span>
          ) : (
            <>
              <label className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
                Role
                <select
                  aria-label={`Role for ${member.display || member.user_id}`}
                  value={member.role}
                  disabled={busy}
                  onChange={(e) => onChangeRole(member, e.target.value as Role)}
                  className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs disabled:opacity-50"
                >
                  {ASSIGNABLE_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={busy}
                onClick={() => onSetEnrolled(member, !member.enrolled)}
                className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
              >
                {member.enrolled ? "Suspend" : "Restore"}
              </button>
            </>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => onInvite(member)}
            className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
          >
            {invitation ? "Regenerate link" : "Send link"}
          </button>
          {invitation && invitation.status === "open" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => onRevokeInvite(member)}
              className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
            >
              Revoke link
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs"
          >
            {expanded ? "Hide details" : "Details"}
          </button>
        </div>
      )}

      {expanded && !member.is_owner ? (
        <div
          data-component="UserRowDetails"
          className="mt-3 flex flex-col gap-3 border-t border-[var(--color-border)] pt-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              aria-label={`Display name for ${member.user_id}`}
              value={display}
              onChange={(e) => setDisplay(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs"
            />
            <button
              type="button"
              disabled={busy || !display.trim() || display === member.display}
              onClick={() => onRename(member, display.trim())}
              className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
            >
              Rename
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={`Channel platform for ${member.user_id}`}
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs"
            >
              {["telegram", "discord", "slack", "email", "whatsapp"].map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <input
              type="text"
              aria-label={`Channel handle for ${member.user_id}`}
              placeholder="channel user id"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-xs"
            />
            <button
              type="button"
              disabled={busy || !handle.trim()}
              onClick={() => {
                onLinkChannel(member, platform, handle.trim());
                setHandle("");
              }}
              className="rounded-lg bg-[var(--color-surface-2)] px-3 py-1 text-xs disabled:opacity-50"
            >
              Link channel
            </button>
          </div>
          {canDelete && !self ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => onDelete(member)}
              className="self-start rounded-lg bg-red-500/15 px-3 py-1 text-xs text-red-300 disabled:opacity-50"
            >
              Remove from profile…
            </button>
          ) : (
            <p className="text-xs text-[var(--color-muted)]">
              {self
                ? "You cannot remove your own enrolment."
                : "Only the owner can remove a user: the account is shared across profiles."}
            </p>
          )}
        </div>
      ) : null}
    </li>
  );
}
