"use client";

import { useState } from "react";

import { BusyRegion } from "@/components/ui/BusyRegion";
import type {
  ProjectContact,
  ProjectDetail,
  ProjectMember,
  ProjectMemberRole,
} from "@/types";

/**
 * Participants — members (people) and profiles (instruments) in one list,
 * because "who is on this" means both; contacts below a rule, marked
 * external. `address` only arrives for non-viewers — the Python layer drops
 * it, so the render merely shows what it was given (§11 rule 3).
 *
 * Writes: **add/remove member** (a person with a box account) and
 * **add/remove contact** (a person outside the box — no principal, no
 * permissions, just someone the work involves).
 */
export function PeoplePanel({
  project,
  archived = false,
}: {
  project: ProjectDetail;
  archived?: boolean;
}) {
  const hostProfile = project.host_profile;
  const slug = project.slug;
  const slugPath = `/api/projects/${encodeURIComponent(slug)}`;

  const [members, setMembers] = useState<ProjectMember[]>(project.members);
  const [contacts, setContacts] = useState<ProjectContact[]>(project.contacts);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Add-member form state
  const [memberId, setMemberId] = useState("");
  const [memberRole, setMemberRole] = useState<ProjectMemberRole>("member");
  const [addingMember, setAddingMember] = useState(false);

  // Add-contact form state
  const [contactName, setContactName] = useState("");
  const [contactRole, setContactRole] = useState("");
  const [contactPlatform, setContactPlatform] = useState("");
  const [contactAddress, setContactAddress] = useState("");
  const [addingContact, setAddingContact] = useState(false);

  const addMember = async () => {
    const id = memberId.trim();
    if (!id) return;
    setAddingMember(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/members`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: id, role: memberRole }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not add the member.");
      setMembers((prev) => [
        ...prev,
        {
          project_id: project.id,
          user_id: id,
          role: memberRole,
          added_by: null,
          added_at: Math.floor(Date.now() / 1000),
        },
      ]);
      setMemberId("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setAddingMember(false);
    }
  };

  const removeMember = async (userId: string) => {
    setBusyId(`m:${userId}`);
    setError(null);
    try {
      const res = await fetch(
        `${slugPath}/members/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not remove the member.");
      setMembers((prev) => prev.filter((m) => m.user_id !== userId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setBusyId(null);
    }
  };

  const addContact = async () => {
    const name = contactName.trim();
    if (!name) return;
    setAddingContact(true);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/contacts`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name,
          role: contactRole.trim() || undefined,
          platform: contactPlatform.trim() || undefined,
          address: contactAddress.trim() || undefined,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as ProjectContact &
        { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not add the contact.");
      setContacts((prev) => [...prev, data]);
      setContactName("");
      setContactRole("");
      setContactPlatform("");
      setContactAddress("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setAddingContact(false);
    }
  };

  const removeContact = async (contactId: string) => {
    setBusyId(`c:${contactId}`);
    setError(null);
    try {
      const res = await fetch(
        `${slugPath}/contacts/${encodeURIComponent(contactId)}`,
        { method: "DELETE" },
      );
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (!res.ok) throw new Error(data.detail ?? "Could not remove the contact.");
      setContacts((prev) => prev.filter((c) => c.id !== contactId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setBusyId(null);
    }
  };

  const inputClass =
    "w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-2)] px-3 py-2 text-sm";

  return (
    <section
      id="panel-people"
      data-component="PeoplePanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        People
      </h2>

      {error ? (
        <p className="mt-2 text-sm text-red-300" role="alert">
          {error}
        </p>
      ) : null}

      <ul className="mt-2 flex flex-col gap-1.5">
        {members.map((member) => (
          <li
            key={member.user_id}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
          >
            <span aria-hidden className="text-[var(--color-muted)]">
              ◉
            </span>
            <span className="min-w-0 flex-1 truncate">{member.user_id}</span>
            <span className="text-xs text-[var(--color-muted)]">
              {member.role}
            </span>
            {archived ? null : (
              <BusyRegion busy={busyId === `m:${member.user_id}`} label="Removing…">
                <button
                  type="button"
                  onClick={() => void removeMember(member.user_id)}
                  aria-label={`Remove ${member.user_id}`}
                  className="shrink-0 text-xs text-[var(--color-muted)] underline disabled:opacity-40"
                >
                  Remove
                </button>
              </BusyRegion>
            )}
          </li>
        ))}
        {project.profiles.map((profile) => (
          <li
            key={profile.profile}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
          >
            <span aria-hidden className="text-[var(--color-muted)]">
              ⚙
            </span>
            <span className="min-w-0 flex-1 truncate">
              {profile.profile}
              {profile.profile === hostProfile ? (
                <span className="ml-2 rounded-full bg-[var(--color-accent)] px-2 py-0.5 text-xs text-[var(--color-accent-fg)]">
                  host
                </span>
              ) : null}
            </span>
            <span className="text-xs text-[var(--color-muted)]">
              {profile.role}
            </span>
          </li>
        ))}
      </ul>

      {contacts.length > 0 ? (
        <>
          <hr className="my-3 border-[var(--color-border)]" />
          <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            External contacts
          </p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {contacts.map((contact) => (
              <li
                key={contact.id}
                className="flex items-start gap-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{contact.name}</span>
                  <span className="ml-2 text-xs text-[var(--color-muted)]">
                    {[contact.role, contact.platform]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  {contact.address ? (
                    <p className="mt-0.5 text-xs text-[var(--color-muted)]">
                      {contact.address}
                    </p>
                  ) : null}
                </div>
                {archived ? null : (
                  <BusyRegion busy={busyId === `c:${contact.id}`} label="Removing…">
                    <button
                      type="button"
                      onClick={() => void removeContact(contact.id)}
                      aria-label={`Remove ${contact.name}`}
                      className="shrink-0 text-xs text-[var(--color-muted)] underline disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </BusyRegion>
                )}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {archived ? (
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          This project is archived — restore it (⋯) to manage people.
        </p>
      ) : (
        <>
          {/* ── Add member ── */}
          <div className="mt-3" data-component="AddMemberForm">
            <p className="text-xs text-[var(--color-muted)]">
              Members are people with a box account — they can log in, see the
              project, and run the agent.
            </p>
            <BusyRegion busy={addingMember} label="Adding…">
              <div className="mt-1.5 flex flex-col gap-1.5">
                <div className="flex gap-1.5">
                  <input
                    className={inputClass}
                    value={memberId}
                    onChange={(e) => setMemberId(e.target.value)}
                    placeholder="User id (e.g. leo)"
                  />
                  <select
                    className={`${inputClass} w-28`}
                    value={memberRole}
                    onChange={(e) =>
                      setMemberRole(e.target.value as ProjectMemberRole)
                    }
                  >
                    <option value="lead">lead</option>
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                  </select>
                </div>
                <button
                  type="button"
                  onClick={() => void addMember()}
                  disabled={!memberId.trim()}
                  className="self-start rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
                >
                  Add member
                </button>
              </div>
            </BusyRegion>
          </div>

          {/* ── Add contact ── */}
          <div className="mt-3" data-component="AddContactForm">
            <p className="text-xs text-[var(--color-muted)]">
              Contacts are people outside the box — a client, a stakeholder —
              no account, no permissions, just someone the work involves.
            </p>
            <BusyRegion busy={addingContact} label="Adding…">
              <div className="mt-1.5 flex flex-col gap-1.5">
                <input
                  className={inputClass}
                  value={contactName}
                  onChange={(e) => setContactName(e.target.value)}
                  placeholder="Name (e.g. Ricky Lui)"
                />
                <div className="flex gap-1.5">
                  <input
                    className={inputClass}
                    value={contactRole}
                    onChange={(e) => setContactRole(e.target.value)}
                    placeholder="Role (e.g. instructor)"
                  />
                  <input
                    className={inputClass}
                    value={contactPlatform}
                    onChange={(e) => setContactPlatform(e.target.value)}
                    placeholder="Platform (e.g. email)"
                  />
                </div>
                <input
                  className={inputClass}
                  value={contactAddress}
                  onChange={(e) => setContactAddress(e.target.value)}
                  placeholder="Address (e.g. ricky@example.com)"
                />
                <button
                  type="button"
                  onClick={() => void addContact()}
                  disabled={!contactName.trim()}
                  className="self-start rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] disabled:opacity-40"
                >
                  Add contact
                </button>
              </div>
            </BusyRegion>
          </div>
        </>
      )}
    </section>
  );
}
