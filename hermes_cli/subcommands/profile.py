"""``hermes profile`` subcommand parser.

Extracted verbatim from ``hermes_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_profile_parser(subparsers, *, cmd_profile: Callable) -> None:
    """Attach the ``profile`` subcommand to ``subparsers``."""
    # =========================================================================
    # profile command
    # =========================================================================
    profile_parser = subparsers.add_parser(
        "profile",
        help="Manage profiles — multiple isolated Hermes instances",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")

    profile_subparsers.add_parser("list", help="List all profiles")
    profile_use = profile_subparsers.add_parser(
        "use", help="Set sticky default profile"
    )
    profile_use.add_argument("profile_name", help="Profile name (or 'default')")

    profile_create = profile_subparsers.add_parser(
        "create", help="Create a new profile"
    )
    profile_create.add_argument(
        "profile_name", help="Profile name (lowercase, alphanumeric)"
    )
    profile_create.add_argument(
        "--clone",
        action="store_true",
        help="Copy config.yaml, .env, SOUL.md, and skills from active profile",
    )
    profile_create.add_argument(
        "--clone-all",
        action="store_true",
        help="Full copy of active profile (all state, excluding per-profile history)",
    )
    profile_create.add_argument(
        "--clone-from",
        metavar="SOURCE",
        help="Source profile to clone from; implies --clone unless --clone-all is set",
    )
    profile_create.add_argument(
        "--no-alias", action="store_true", help="Skip wrapper script creation"
    )
    profile_create.add_argument(
        "--no-skills",
        action="store_true",
        help="Create an empty profile with no bundled skills (opts out of `hermes update` skill sync)",
    )
    profile_create.add_argument(
        "--description",
        default=None,
        help="One- or two-sentence description of what this profile is good at. "
             "Used by the kanban decomposer to route tasks based on role instead "
             "of profile name alone. Skip and add later via `hermes profile describe`.",
    )

    profile_delete = profile_subparsers.add_parser("delete", help="Delete a profile")
    profile_delete.add_argument("profile_name", help="Profile to delete")
    profile_delete.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt"
    )

    profile_describe = profile_subparsers.add_parser(
        "describe",
        help="Read or set a profile's description (used by the kanban orchestrator)",
    )
    profile_describe.add_argument(
        "profile_name",
        nargs="?",
        default=None,
        help="Profile to describe (omit + use --all --auto to sweep)",
    )
    profile_describe.add_argument(
        "--text",
        default=None,
        help="Set description to this exact text (overwrites any existing description)",
    )
    profile_describe.add_argument(
        "--auto",
        action="store_true",
        help="Auto-generate description via the auxiliary LLM "
             "(uses auxiliary.profile_describer)",
    )
    profile_describe.add_argument(
        "--overwrite",
        action="store_true",
        help="With --auto, replace user-authored descriptions too (default: only "
             "fill in missing or previously-auto descriptions)",
    )
    profile_describe.add_argument(
        "--all",
        dest="all_missing",
        action="store_true",
        help="With --auto, run on every profile missing a description",
    )

    profile_show = profile_subparsers.add_parser("show", help="Show profile details")
    profile_show.add_argument("profile_name", help="Profile to show")

    profile_alias = profile_subparsers.add_parser(
        "alias", help="Manage wrapper scripts"
    )
    profile_alias.add_argument("profile_name", help="Profile name")
    profile_alias.add_argument(
        "--remove", action="store_true", help="Remove the wrapper script"
    )
    profile_alias.add_argument(
        "--name",
        dest="alias_name",
        metavar="NAME",
        help="Custom alias name (default: profile name)",
    )

    profile_rename = profile_subparsers.add_parser("rename", help="Rename a profile")
    profile_rename.add_argument("old_name", help="Current profile name")
    profile_rename.add_argument("new_name", help="New profile name")

    profile_export = profile_subparsers.add_parser(
        "export", help="Export a profile to archive"
    )
    profile_export.add_argument("profile_name", help="Profile to export")
    profile_export.add_argument(
        "-o", "--output", default=None, help="Output file (default: <name>.tar.gz)"
    )

    profile_import = profile_subparsers.add_parser(
        "import", help="Import a profile from archive"
    )
    profile_import.add_argument("archive", help="Path to .tar.gz archive")
    profile_import.add_argument(
        "--name",
        dest="import_name",
        metavar="NAME",
        help="Profile name (default: inferred from archive)",
    )

    # ---------- Distribution subcommands (issue #20456) ----------
    profile_install = profile_subparsers.add_parser(
        "install",
        help="Install a profile distribution from a git URL or local directory",
        description=(
            "Install a Hermes profile distribution. SOURCE can be a git URL "
            "(github.com/user/repo, https://..., git@...) or a local "
            "directory containing distribution.yaml at its root."
        ),
    )
    profile_install.add_argument(
        "source",
        help="Distribution source (git URL or local directory)",
    )
    profile_install.add_argument(
        "--name", dest="install_name", metavar="NAME",
        help="Override profile name (default: read from manifest)",
    )
    profile_install.add_argument(
        "--alias", action="store_true",
        help="Create a shell wrapper alias for the installed profile",
    )
    profile_install.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing profile of the same name (user data preserved)",
    )
    profile_install.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip manifest preview confirmation",
    )

    profile_update = profile_subparsers.add_parser(
        "update",
        help="Re-pull a distribution and apply updates (user data preserved)",
        description=(
            "Fetch the distribution from its recorded source and overwrite "
            "distribution-owned files (SOUL.md, skills/, cron/, mcp.json). "
            "User data (memories, sessions, auth, .env) is never touched. "
            "config.yaml is preserved unless --force-config is passed."
        ),
    )
    profile_update.add_argument("profile_name", help="Profile to update")
    profile_update.add_argument(
        "--force-config", action="store_true",
        help="Also overwrite config.yaml (normally preserved to keep user overrides)",
    )
    profile_update.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation",
    )

    profile_info = profile_subparsers.add_parser(
        "info",
        help="Show a profile's distribution manifest (version, requirements, source)",
    )
    profile_info.add_argument("profile_name", help="Profile to inspect")

    # ---------- FG-28: multi-profile control-plane registry ----------
    profile_registry = profile_subparsers.add_parser(
        "registry",
        help="Control-plane list of profiles this box serves and how to reach each "
             "one (FG-28 multi-profile console). Holds no authority data — "
             "enrolment stays in each profile's principals table.",
    )
    profile_registry_sub = profile_registry.add_subparsers(
        dest="registry_action", metavar="{list,health}"
    )
    profile_registry_sub.add_parser(
        "list",
        help="List every profile with its served flag, routing prefix, and "
             "derived schema (no database calls).",
    )
    profile_registry_sub.add_parser(
        "health",
        help="Probe each profile's app-datastore binding (schema_owner claim) "
             "and badge it ok / unclaimed / claimed-by-other / unreachable.",
    )

    # ---------- FG-30: profile lifecycle (suggest, adopt, retire) ----------
    profile_subparsers.add_parser(
        "suggest",
        help="Generate a profile suggestion from the learning loop's evidence "
             "(monthly cycle — skipped if a suggestion is already open)",
    )

    profile_subparsers.add_parser(
        "suggestions",
        help="List pending profile suggestions with evidence",
    )

    profile_adopt = profile_subparsers.add_parser(
        "adopt",
        help="Adopt a suggestion — creates the profile with sub-goal and "
             "promoted skills (owner only)",
    )
    profile_adopt.add_argument(
        "suggestion_id",
        help="Suggestion id to adopt",
    )

    profile_dismiss = profile_subparsers.add_parser(
        "dismiss",
        help="Dismiss a suggestion — latched so it is never re-proposed "
             "on the same evidence (owner only)",
    )
    profile_dismiss.add_argument(
        "suggestion_id",
        help="Suggestion id to dismiss",
    )
    profile_dismiss.add_argument(
        "--reason",
        default="",
        help="Optional reason for the dismissal (recorded in audit)",
    )

    profile_retire = profile_subparsers.add_parser(
        "retire",
        help="Retire a profile — offer its skills for promotion once, archive, "
             "release channel, mark goal completed",
    )
    profile_retire.add_argument(
        "profile_name",
        help="Profile to retire",
    )
    profile_retire.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompt",
    )

    profile_merge = profile_subparsers.add_parser(
        "merge",
        help="Merge one profile into another — both profiles' skills go "
             "through promotion; the source is archived. Memory is NOT merged.",
    )
    profile_merge.add_argument(
        "source",
        help="Source profile to merge (will be archived)",
    )
    profile_merge.add_argument(
        "target",
        help="Target profile to merge into",
    )
    profile_merge.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompt",
    )

    profile_parser.set_defaults(func=cmd_profile)
