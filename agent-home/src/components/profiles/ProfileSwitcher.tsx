"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";

import { BusyRegion } from "@/components/ui/BusyRegion";
import type { AdministeredProfileEntry } from "@/types";

export interface ProfileSwitcherProps {
  /**
   * The profiles the acting admin may switch the console to. Re-derived
   * server-side per request, so the option set the switcher renders is a
   * routing hint, never a grant: each console route re-resolves the
   * principal in the target profile's own `principals` table.
   */
  profiles: AdministeredProfileEntry[];
  /**
   * The profile the page is currently scoped to (the `?profile=` query the
   * page read on the server). May not appear in {@link profiles} when the
   * caller is enrolled here only as a `member`/`viewer` (the switcher is
   * then read-only) or when the active profile is the unnamed default and
   * the registry lists it under `"default"`.
   */
  value: string;
  /**
   * The acting role — a `member`/`viewer` sees the current profile name as
   * a read-only label rather than a select: the admin roster toolbar is not
   * their surface, and rendering an empty dropdown would imply authority
   * they do not have.
   */
  canManage: boolean;
}

/**
 * FG-28 — the profile switcher for the multi-profile admin console.
 *
 * The switcher sets `?profile=<name>` on the URL; the page is a server
 * component that re-reads its data under that profile, so the moment of
 * switching is one round-trip and the rendered rows are guaranteed to come
 * from the profile the select shows — never a client-side cache held from
 * the previous one. Health badges come from the registry's live probe
 * (`probe_registry_health`): a `claimed-by-other` profile would fail closed
 * on connect, so the switcher marks it **before** the user opens a turn
 * there rather than letting them hit the 403 mid-action.
 *
 * Single-profile behaviour is byte-identical to the legacy Pill: with one or
 * zero administered profiles the switcher renders as a plain label, so an
 * operator on a single-profile box sees nothing new. The branch is decided
 * **before** any hooks run — :func:`ProfileSwitcher` is a pure dispatcher,
 * and the interactive (hook-bearing) split lives in its own component below
 * so a readonly render (e.g. a server-side test of the non-admin view) does
 * not require an App Router context.
 */
export function ProfileSwitcher(props: ProfileSwitcherProps) {
  const { profiles, value, canManage } = props;
  if (!canManage || profiles.length <= 1) {
    return <ProfileSwitcherLabel value={value} health={profiles[0]?.health} />;
  }
  return <ProfileSwitcherSelect {...props} />;
}

/** The single-profile / non-admin read-only label — no hooks, no router. */
function ProfileSwitcherLabel({
  value,
  health,
}: {
  value: string;
  health: AdministeredProfileEntry["health"] | undefined;
}) {
  return (
    <span
      data-component="ProfileSwitcher"
      data-active={value}
      data-readonly="true"
      className="inline-flex items-center gap-1 rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-xs text-[var(--color-fg)]"
    >
      <HealthDot health={health} />
      profile: {value}
    </span>
  );
}

/** The multi-profile interactive select — owns the router transition. */
function ProfileSwitcherSelect({
  profiles,
  value,
}: ProfileSwitcherProps) {
  const router = useRouter();
  const [switching, startSwitch] = useTransition();

  function switchTo(next: string) {
    if (next === value) return;
    startSwitch(() => {
      const params = new URLSearchParams(window.location.search);
      if (next && next !== "default") params.set("profile", next);
      else params.delete("profile");
      const qs = params.toString();
      router.replace(qs ? `/users?${qs}` : "/users");
    });
  }

  const activeHealth =
    profiles.find((p) => p.name === value)?.health ?? "unknown";

  return (
    <BusyRegion
      busy={switching}
      label="Switching profile…"
      className="inline-flex items-center gap-1"
    >
      <span
        data-component="ProfileSwitcher"
        data-active={value}
        className="inline-flex items-center gap-1 rounded-full bg-[var(--color-surface-2)] px-2 py-1 text-xs text-[var(--color-fg)]"
        title={healthTitle(activeHealth)}
      >
        <HealthDot health={activeHealth} />
        <label className="sr-only" htmlFor="profile-switcher-select">
          Administer profile
        </label>
        <select
          id="profile-switcher-select"
          value={value}
          onChange={(e) => switchTo(e.target.value)}
          disabled={switching}
          className="bg-transparent text-xs outline-none"
        >
          {profiles.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
              {p.health === "ok" ? "" : ` — ${healthLabel(p.health)}`}
            </option>
          ))}
        </select>
      </span>
    </BusyRegion>
  );
}

function healthLabel(health: AdministeredProfileEntry["health"]): string {
  switch (health) {
    case "ok":
      return "";
    case "core-only":
      return "core-only";
    case "unreachable":
      return "unreachable";
    case "unclaimed":
      return "unclaimed";
    case "claimed-by-other":
      return "owned by another profile";
    case "unknown":
    default:
      return "health unknown";
  }
}

function healthTitle(health: AdministeredProfileEntry["health"]): string {
  switch (health) {
    case "ok":
      return "Profile schema is contactable and self-owned.";
    case "core-only":
      return "This profile has no app datastore — its `principals` table cannot be administered here.";
    case "unreachable":
      return "The profile's datastore could not be reached. A routed turn would fail on connect.";
    case "unclaimed":
      return "The profile's schema has no owner marker yet. The first admin turn would adopt it.";
    case "claimed-by-other":
      return "This profile's derived schema is owned by another profile. A routed turn would fail closed on connect.";
    case "unknown":
    default:
      return "Profile health has not been probed.";
  }
}

function HealthDot({
  health,
}: {
  health: AdministeredProfileEntry["health"] | undefined;
}) {
  if (!health || health === "ok") return null;
  const tone =
    health === "claimed-by-other" || health === "unreachable"
      ? "bg-red-400"
      : health === "core-only" || health === "unclaimed"
        ? "bg-amber-400"
        : "bg-[var(--color-muted)]";
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-2 w-2 rounded-full ${tone}`}
    />
  );
}