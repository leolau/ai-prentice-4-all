import type { ProjectDetail } from "@/types";

/**
 * Participants — members (people) and profiles (instruments) in one list,
 * because "who is on this" means both; contacts below a rule, marked
 * external. `address` only arrives for non-viewers — the Python layer drops
 * it, so the render merely shows what it was given (§11 rule 3).
 */
export function PeoplePanel({ project }: { project: ProjectDetail }) {
  const hostProfile = project.host_profile;
  return (
    <section
      id="panel-people"
      data-component="PeoplePanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        People
      </h2>

      <ul className="mt-2 flex flex-col gap-1.5">
        {project.members.map((member) => (
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

      {project.contacts.length > 0 ? (
        <>
          <hr className="my-3 border-[var(--color-border)]" />
          <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
            External contacts
          </p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {project.contacts.map((contact) => (
              <li
                key={contact.id}
                className="rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm"
              >
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
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
