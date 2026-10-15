"""Per-role limits on the tools an agent may call and the files it may touch.

Owner and admin keep every tool. Members and viewers lose the tools that run
arbitrary commands (a command denylist can't hold: ``python3 -c`` reads any
file the service account can), and their file tools are confined to their own
folder under ``$HERMES_HOME/workspaces/``. An agent with no logged-in person
(CLI, cron, operator) is not limited here.

Overrides live in config.yaml::

    access:
      roles:
        member:
          deny_tools: [terminal, code_execution]   # tool or toolset names
          files: own_folder                         # or "any"
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, Optional, Tuple

_COMMAND_TOOLS: Tuple[str, ...] = (
    "terminal",
    "process",
    "read_terminal",
    "close_terminal",
    "execute_code",
    "cronjob",
    "skill_manage",
)

DEFAULT_DENIED_TOOLS: Dict[str, Tuple[str, ...]] = {
    "owner": (),
    "admin": (),
    "member": _COMMAND_TOOLS,
    "viewer": _COMMAND_TOOLS + ("write_file", "patch"),
}

_FULL_FILE_ROLES = frozenset({"owner", "admin"})

_principal: contextvars.ContextVar[Tuple[Optional[str], Optional[str]]] = (
    contextvars.ContextVar("tool_access_principal", default=(None, None))
)


def _role(user_id: Optional[str], role: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    from hermes_cli.access import normalize_role

    return normalize_role(role)


def _role_config(role: str) -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly

        roles = (load_config_readonly().get("access") or {}).get("roles") or {}
        cfg = roles.get(role) if isinstance(roles, dict) else None
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _expand(names) -> FrozenSet[str]:
    import toolsets

    out: set = set()
    for name in names or ():
        name = str(name).strip()
        if not name:
            continue
        if name in toolsets.TOOLSETS:
            out.update(toolsets.resolve_toolset(name))
        else:
            out.add(name)
    return frozenset(out)


def denied_tools(user_id: Optional[str], role: Optional[str]) -> FrozenSet[str]:
    """Tool names *user_id* (holding *role*) may not call; empty when unlimited."""
    r = _role(user_id, role)
    if r is None:
        return frozenset()
    cfg = _role_config(r)
    if "deny_tools" in cfg:
        return _expand(cfg.get("deny_tools"))
    return _expand(DEFAULT_DENIED_TOOLS.get(r, _COMMAND_TOOLS))


def _folder_name(user_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.@-]", "_", user_id).lstrip(".")
    return name or "_"


def files_root(user_id: Optional[str], role: Optional[str]) -> Optional[Path]:
    """The only folder *user_id*'s file tools may use, or ``None`` for anywhere."""
    r = _role(user_id, role)
    if r is None:
        return None
    files = _role_config(r).get("files")
    if files == "any" or (files is None and r in _FULL_FILE_ROLES):
        return None
    from hermes_constants import get_hermes_home

    root = Path(get_hermes_home()) / "workspaces" / _folder_name(str(user_id))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


@contextlib.contextmanager
def acting_as(user_id: Optional[str], role: Optional[str]) -> Iterator[None]:
    """Bind the person a tool call runs for, for the duration of the call."""
    token = _principal.set((user_id, role))
    try:
        yield
    finally:
        _principal.reset(token)


def current_files_root() -> Optional[Path]:
    return files_root(*_principal.get())


def check_file_access(path: Path) -> Optional[str]:
    """Error message when the bound person may not touch *path*, else ``None``."""
    root = current_files_root()
    if root is None:
        return None
    from tools.path_security import validate_within_dir

    if validate_within_dir(Path(path), root):
        return (
            f"Access denied: you can only use files in your own folder ({root}). "
            "Relative paths start there."
        )
    return None


def blocked_tool_error(function_name: str) -> str:
    return (
        f"'{function_name}' is not available to your role. "
        "Ask the owner or an admin if you need it."
    )
