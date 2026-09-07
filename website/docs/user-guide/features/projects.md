# Projects

Projects are for work that takes more than one sitting. A chat is a conversation; a task (to-do) is one thing to do; a **project** is a durable record with a goal, the outputs it must deliver, a plan, a board of cards and a history of runs — all under one name.

This guide follows the Projects pages in agent-home. You can also ask the agent in chat: it knows this guide (`app_page_help`), can open the pages for you and fill in forms, and confirms anything destructive (archive, delete, close) before doing it.

## The lifecycle at a glance

```
Create → Get it ready → Plan → Activate → Run → Board / Outputs → Accept → Repeat or Close
```

## 1. Create

**Projects › New project.**

1. Name and goal — one sentence each is enough; the goal is what the agent works towards.
2. Outputs — what the project must deliver (a document, a report, a summary…). Mark the required ones; a one-off project offers to close once every required output is accepted.
3. Cadence and autonomy (see [the two axes](#the-two-axes) below). Both can be changed later in **Settings**.
4. Plan — choose **ask the agent to draft one** (the agent reads the brief and proposes a plan in the background; it appears on the project page as a proposed revision you then activate) or **write it myself**. Either way, creating lands you on the project's own page — never in chat.

You land on the project page with the readiness checklist open.

## 2. Get it ready

The header shows a **readiness checklist**: outputs declared, a host profile, an active plan and — for repeatable projects — a schedule. Each unmet item links to the panel that fixes it. **Run now** stays disabled until every item is ticked, so a project can never fail on "no plan" after you press the button.

## 3. Plan and activate

The **Plan** panel holds the plan: prose plus ordered steps. Each step has a title, an assignee (one of the project's profiles), whether it is a **checkpoint** (a supervised run pauses there for you) and what it needs first.

- **Save** creates a *proposed revision*. Revisions are kept, so proposing a new plan never loses the old one.
- **Activate** (project lead or admin only) makes a revision the one that runs. An agent can propose a plan but never activate one — activation is a human judgement.

## 4. Run

**Run now** starts a run and opens the **live run page**, which streams the agent's activity (reasoning, tools, steps). A run turns the plan's steps into cards on the board and works through them.

- **Stop** interrupts the run at its next safe point.
- A supervised run that paused at a checkpoint shows **Continue**.
- The **Runs** panel lists every run with status, elapsed time and, when finished, a retrospective.

## 5. The board

Cards move **triage → ready → in progress → done** (or **blocked**). From the board or a card's page you can:

- add a card in any column;
- edit title and description, assign it to a profile;
- move it between columns;
- stop a running card, or reclaim / make ready a stopped one.

In a **manual** project every new card waits in triage until you make it ready.

## 6. Outputs and acceptance

When a run delivers an output it shows as **delivered** on the project row and in the **Progress** panel. A project member **accepts** it — a human judgement, agents cannot. Once every required output is accepted, a one-off project offers to close.

## 7. Repeat

- **Repeatable** projects run again on their schedule — set it in **Settings**; nothing fires until you do — or whenever you press Run now.
- **Standing** projects never finish: cards keep arriving and progress is measured by recent deliveries.

## 8. Review

**Summarise** writes a short status summary into the header and records when it was written. **Progress** shows outputs and card counts. **Edit brief** changes name, goal, description and audience; **Pause / Resume** lives there too.

## 9. Finish

- **Close** when the goal is met.
- **Archive** to shelve it: an archived project does not run or learn, but its record stays readable and can be **restored** (restoring drops the schedule — set it again).
- **Delete** only for projects created by mistake; it never touches board history.

## The two axes

### Cadence — how often

| Cadence | What you will see |
|---|---|
| **One-off** | You start each run. Once every required output is accepted the project offers to close. |
| **Repeatable** | Runs fire on the schedule you set in Settings; each run delivers the outputs again. |
| **Standing** | An ongoing duty, never "done": cards keep arriving; progress is recent deliveries. |

### Autonomy — how much the agent does on its own

| Autonomy | What you will see |
|---|---|
| **Manual** | Nothing happens without you: every step waits in triage until you make it ready, a schedule never fires, only Run now starts a run. |
| **Supervised** | The agent works through the plan and pauses at checkpoints for you; you accept the outputs. |
| **Autonomous** | The agent promotes its own steps and runs to the end without stopping; irreversible acts still ask for approval and you still accept the outputs. |

## Who can do what

| | Member | Lead / admin | Agent |
|---|---|---|---|
| Read, run, edit cards | yes | yes | yes |
| Accept outputs | yes | yes | no |
| Activate a plan, change settings | no | yes | no |
| Propose a plan, deliver outputs | — | — | yes |

## Doing it from chat

Ask the agent to draft or revise the plan, run the project, summarise it or explain a run. From agent-home the agent knows which page you are on, so "run this project" or "what does this panel do?" needs no further context.

## Command line

`hermes projects` mirrors the pages: `list`, `show`, `playbook`, `runs`, `retro`, `cards`, `card`, `outputs`, `members`, `contacts`, `link`, `guidance`. Use `--as-human` only when you, the operator, are making a human-only judgement (accepting an output, activating a plan).
