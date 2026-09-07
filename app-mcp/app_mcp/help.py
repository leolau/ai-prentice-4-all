"""Per-page usage guides, for the agent to explain agent-home to the user.

``pages.PAGES`` is the map (where things are); this is the manual (how each
screen is used). Text is written to be quoted or paraphrased in chat, so it
describes what the user sees and does, not internal enums or routes.

Keys are page paths as reported by the browser bridge (``/projects/[slug]``
style templates). Sub-pages without an entry fall back to the nearest
ancestor, so ``/projects/[slug]/runs/[runNo]`` inherits the Projects guide.
"""
from __future__ import annotations

PROJECTS_GUIDE = """\
Projects are for work that takes more than one sitting: a durable record with
a goal, the outputs it must deliver, a plan, a kanban board, and a history of
runs. A chat is for a conversation; a task (to-do) is one thing to do; a
project holds many runs and many cards under one goal.

HOW TO USE PROJECTS, START TO FINISH

1. Create — Projects › New project. Give it a name and goal, list the outputs
   it must deliver (a document, a report, a summary…), pick a cadence and an
   autonomy level (below), then choose who drafts the plan: "ask the agent"
   has the agent read the brief and propose a plan in the background (you
   land on the project page and watch it arrive), or "write it myself" lets
   you type the steps. Either way you end up on the project's own page.
2. Get it ready — the project page opens on a readiness checklist: outputs
   declared, a host profile, an active plan, and (for repeatable projects) a
   schedule. Each unmet item links to the panel that fixes it. "Run now" stays
   disabled until everything is ticked.
3. Plan — the Plan panel holds the plan: prose plus ordered steps (title,
   assignee, whether it is a checkpoint). Saving creates a proposed revision;
   the project lead (or an admin) must press Activate before it can run.
   Revisions are kept, so you can propose a new plan later without losing the
   old one. An agent can propose a plan but never activate one.
4. Run — "Run now" starts a run and lands on the live run page, which streams
   the agent's activity. A run turns plan steps into cards on the board. Runs
   can be stopped, and a supervised run that paused at a checkpoint shows
   Continue. The Runs panel lists every run with its status and a
   retrospective when finished.
5. Board — cards move through triage › ready › in progress › done (or blocked).
   You can add a card in any column, edit its title/description, assign it to
   a profile, move it between columns, and stop or reclaim it. "Manual"
   projects keep every new card in triage until you make it ready.
6. Outputs — when a run delivers an output it shows as "delivered". A member
   accepts it (a human judgement — agents cannot). Once all required outputs
   are accepted, a one-off project offers to close.
7. Repeat — a repeatable project runs again on its schedule (set in Settings;
   nothing fires until you set one) or whenever you press Run now. Standing
   projects never finish: new cards keep arriving and progress is measured by
   recent deliveries.
8. Review — Summarise writes a short status summary into the header (it shows
   when it was last written). Progress shows outputs and card counts.
9. Finish — Close when the goal is met; Archive to shelve it (archived
   projects do not run or learn, but their record stays readable and can be
   restored — restoring drops the schedule, so set it again). Delete is only
   for projects created by mistake.

CADENCE (how often)
- One-off: you start each run; once every required output is accepted the
  project offers to close.
- Repeatable: runs fire on the schedule you set in Settings; each run
  delivers the outputs again.
- Standing: an ongoing duty that is never "done".

AUTONOMY (how much the agent does on its own)
- Manual: nothing happens without you — every step waits in triage until you
  make it ready, a schedule never fires, only Run now starts a run.
- Supervised: the agent works through the plan and pauses at checkpoints; you
  accept the outputs.
- Autonomous: the agent promotes its own steps and runs to the end without
  stopping; irreversible acts still ask for approval and you still accept the
  outputs.
Both can be changed later in the project's Settings panel; the brief (name,
goal, description, audience) is edited with Edit brief; Pause/Resume is there
too.

WHO CAN DO WHAT
Members can read, run, edit cards and accept outputs. Only the lead or an
admin activates a plan or changes settings. Agents propose, run and deliver;
they do not activate plans or accept outputs.

FROM CHAT
Ask the agent to draft or revise the plan, run the project, summarise it, or
explain a run — it can open the pages for you and fill in forms, but
anything destructive (archive, delete, close) is confirmed with you first.
The equivalent CLI is `hermes projects` (list, show, playbook, runs, cards,
outputs, guidance, retro).
"""

HELP: dict[str, str] = {
    "/": (
        "Home is the landing page: who you are signed in as and shortcuts into "
        "every other screen via the app grid."
    ),
    "/chat": (
        "Chats are full conversations with the agent. Send a message and the "
        "agent acknowledges at once; the status line shows what it is doing "
        "(thinking, running a tool, writing) and how long it has taken. Long "
        "tasks keep running on the server if you close the browser — the "
        "result is here when you return, and Stop interrupts the agent at its "
        "next safe point. New chat starts a fresh session; older sessions are "
        "in the list."
    ),
    "/todos": (
        "Tasks are single to-dos: open, staged (queued for the agent) or done. "
        "Add one, hand it to the agent, or promote it into a project card when "
        "it belongs to a bigger piece of work."
    ),
    "/inbox": (
        "Inbox collects incoming items (messages and requests from your "
        "channels) to triage, plus pending approvals and proposed changes. Open "
        "one to see where it came from, Remember it into memory, or add it to a "
        "project; Acknowledge clears it."
    ),
    "/memory": (
        "Memory is a map of what the agent remembers: each dot is a memory, "
        "nearby dots are related, and a query highlights what it would recall. "
        "Legend explains the colours. It is read-only here — tell the agent in "
        "chat to remember or forget something."
    ),
    "/projects": PROJECTS_GUIDE,
    "/files": (
        "Files is the shared registry of documents the agent knows about: "
        "browse what is registered and where it lives; link a file to a project "
        "from the project's Files panel."
    ),
    "/activity": (
        "Activity shows what the agent has been doing as traces; open one to "
        "see the steps, tools and timing of a single piece of work."
    ),
    "/graph": "Graph shows who communicates with whom across your channels.",
    "/capacity": (
        "Capacity shows how much the agent and its profiles are handling and "
        "what is queued."
    ),
    "/users": "Users lists the people with access to this agent-home and their roles.",
    "/members": "Members lists the people and profiles taking part in communications.",
    "/profiles/suggestions": (
        "Suggestions are profile changes the agent proposes from what it has "
        "learned; accept or dismiss each."
    ),
    "/tools": (
        "Tools lists the agent's toolsets and connected accounts; enable, "
        "disable or connect them here."
    ),
    "/core": "Core shows the agent's health: services, model, memory tier and versions.",
    "/webview": "Webview is a console for pages the agent opens on your behalf.",
    "/settings": (
        "Settings covers your account, notifications, connected accounts and "
        "app preferences."
    ),
    "/onboarding": "Onboarding walks a new user through the first-run setup.",
}


def help_for(path: str | None) -> str | None:
    """Guide for ``path``, falling back to the nearest ancestor page."""
    if not path:
        return None
    if path == "/":
        return HELP["/"]
    parts = path.rstrip("/").split("/")
    while len(parts) > 1:
        candidate = "/".join(parts)
        if candidate in HELP:
            return HELP[candidate]
        parts.pop()
    return None
