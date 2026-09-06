/**
 * Turn a backend refusal into a sentence about what to do next (§16).
 *
 * The registry answers 409/422/403 with an engineer's `detail` — "project
 * 'x' is 'paused': only an active project runs" — which is precise and
 * unhelpful. Each rule below recognises one family of refusal and says
 * what the user can do about it; anything unrecognised falls back to the
 * raw detail (better than a generic apology), and a missing detail to the
 * caller's own fallback.
 */
export interface RefusalInput {
  status?: number;
  detail?: unknown;
}

interface Rule {
  test: RegExp;
  say: (m: RegExpMatchArray) => string;
}

const RULES: Rule[] = [
  {
    test: /is archived/i,
    say: () => "This project is archived — restore it from the ⋯ menu first.",
  },
  {
    test: /is '(paused|draft|needs_completion|done|closed)'.*only an active/i,
    say: (m) =>
      m[1] === "paused"
        ? "The project is paused — resume it (Edit → status) before running."
        : `The project is ${m[1].replace("_", " ")} — activate it before running.`,
  },
  {
    test: /has no playbook|has no steps/i,
    say: () => "There is no active plan yet — write one in the Plan panel and activate it.",
  },
  {
    test: /has no profiles/i,
    say: () => "No agent profile is attached — add one under People before running.",
  },
  {
    test: /has no outputs|declares at least one output/i,
    say: () => "Declare at least one output first — a run needs something to deliver.",
  },
  {
    test: /is a human act/i,
    say: () =>
      "Only a signed-in person can do this — sign in (not an agent turn or API call) and try again.",
  },
  {
    test: /run (\d+) is already (\w+)/i,
    say: (m) => `Run ${m[1]} has already ${m[2]} — refresh to see its current state.`,
  },
  {
    test: /is not waiting/i,
    say: () => "That run is not waiting for you any more — refresh to see where it is.",
  },
  {
    test: /is not one of this project's profiles/i,
    say: () => "Pick a profile that is on this project — add it under People first.",
  },
  {
    test: /only a ready or running card can be (marked done|blocked)/i,
    say: (m) => `Only a ready or running card can be ${m[1]} — make it ready first.`,
  },
  {
    test: /cannot be made ready/i,
    say: () => "This card cannot be made ready from its current column.",
  },
  {
    test: /already archived/i,
    say: () => "This card is already archived.",
  },
  {
    test: /^(\w[\w ]*) must not be empty|^(\w[\w ]*) (is|are) required/i,
    say: (m) => `${capital(m[1] ?? m[2])} is required.`,
  },
  {
    test: /must be at most (\d+) characters/i,
    say: (m) => `Keep it under ${m[1]} characters.`,
  },
  {
    test: /unknown toolset\(s\): (.*)/i,
    say: (m) => `Unknown toolset: ${m[1]}. Use the names the host profile knows.`,
  },
  {
    test: /unknown skill\(s\): (.*)/i,
    say: (m) => `Unknown skill: ${m[1]}. Use the names the host profile knows.`,
  },
  {
    test: /already a member|already on the project/i,
    say: () => "They are already on this project.",
  },
  {
    test: /forbidden|not a (lead|member)|only (a|the) lead/i,
    say: () => "Only a lead of this project can do that.",
  },
];

function capital(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The sentence to show for a failed write. */
export function friendlyError(
  input: RefusalInput,
  fallback = "That did not go through.",
): string {
  const detail =
    typeof input.detail === "string"
      ? input.detail.trim()
      : input.detail != null && typeof input.detail === "object" && "message" in input.detail
        ? String((input.detail as { message: unknown }).message)
        : "";
  if (detail) {
    for (const rule of RULES) {
      const m = detail.match(rule.test);
      if (m) return rule.say(m);
    }
    return detail;
  }
  if (input.status === 403) return "You do not have permission to do that.";
  if (input.status === 404) return "That no longer exists — refresh the page.";
  if (input.status === 409) return "The project is not in a state that allows this right now.";
  if (input.status != null && input.status >= 500) return "The agent is having trouble — try again shortly.";
  return fallback;
}
