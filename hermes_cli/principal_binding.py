"""Which person is a *non-channel* session (FG-24 §"unscoped sessions").

The C1 seam (``hermes_cli.access.resolve_principal``) answers "whose message is
this?" for inbound channel traffic. Sessions that arrive without a channel —
the local CLI, ``hermes cron`` jobs, the digest, the email poller, the calendar
triage agent — have no sender to resolve, so before this module they ran with no
principal at all. Under FG-24 that is not neutral: an unscoped store's
``target='memory'`` file is the very file every resolved principal renders as
the profile-wide **shared** block, and after the owner migration such a session
renders no person block at all.

Owner's decision (2026-08-12): resolve the principal instead of falling back.
The ladder here, in order:

1. **The remembered binding** — a previous answer for this profile.
2. **The login user** — a login subject already in scope resolves through
   ``principal_aliases`` (the same mapping the web/BFF seam uses).
3. **The setup binding** — when the deployment has exactly one enrolled
   principal, that person set the box up and is unambiguously the one this
   session belongs to.
4. **Ask, once** — with several candidates and a terminal attached, ask and
   remember the answer.
5. **Unresolved** — nothing is guessed. The caller learns how many candidates
   there were so it can fail closed instead of writing into shared memory.

The binding is stored per profile (``$HERMES_HOME/local_principal.json``) as an
id and a role, because it answers a profile-level question: which participation
a local session is acting in.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Where the remembered answer lives, inside the profile home.
BINDING_FILENAME = "local_principal.json"

#: How the binding was arrived at (recorded so ``show`` can explain itself).
SOURCES = ("login", "setup", "asked")


@dataclass(frozen=True)
class LocalBinding:
    """The principal a non-channel session in this profile acts as."""

    user_id: str
    role: str
    source: str


@dataclass(frozen=True)
class LocalResolution:
    """The outcome of the ladder.

    ``binding`` is ``None`` when the principal could not be determined.
    ``candidates`` is how many principals were enrolled at that moment (``0``
    when the directory could not be read at all, e.g. no database configured —
    the single-user case, where pre-FG-24 behaviour is still correct).
    """

    binding: Optional[LocalBinding]
    candidates: int

    @property
    def ambiguous(self) -> bool:
        """True when nothing resolved *and* more than one person could own it.

        This is the only case that must fail closed: with two or more enrolled
        principals, an unattributed write into the shared block would put one
        person's private notes in everyone else's prompt.
        """
        return self.binding is None and self.candidates > 1


def binding_path() -> Path:
    """Return the remembered-binding file for the active profile."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / BINDING_FILENAME


def read_binding() -> Optional[LocalBinding]:
    """Return the remembered binding, or ``None`` when absent/unreadable."""
    path = binding_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError) as err:
        logger.warning("Ignoring unreadable local principal binding: %s", err)
        return None
    if not isinstance(raw, dict):
        return None
    user_id = str(raw.get("user_id") or "").strip()
    if not user_id:
        return None
    role = str(raw.get("role") or "member").strip() or "member"
    source = str(raw.get("source") or "asked").strip()
    return LocalBinding(user_id=user_id, role=role, source=source)


def remember_binding(user_id: str, role: str, source: str) -> LocalBinding:
    """Persist ``user_id``/``role`` as this profile's local binding."""
    from hermes_cli.access import normalize_role, validate_user_id

    binding = LocalBinding(
        user_id=validate_user_id(user_id),
        role=normalize_role(role),
        source=source if source in SOURCES else "asked",
    )
    path = binding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "user_id": binding.user_id,
        "role": binding.role,
        "source": binding.source,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return binding


def forget_binding() -> bool:
    """Remove the remembered binding. Returns True when one was removed."""
    try:
        binding_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as err:
        logger.warning("Could not remove the local principal binding: %s", err)
        return False


def _run_async(coro: Any, *, timeout: float) -> Any:
    """Run ``coro`` to completion from sync code, loop or no loop.

    Agent init is synchronous but is reached both from the CLI (no loop) and
    from inside a running loop. Blocking a live loop with ``run_until_complete``
    would deadlock it, so when one is running the work goes to a thread with its
    own loop. Any failure is the caller's to treat as "unresolved".
    """
    import asyncio

    async def _with_timeout() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())

    result: List[Any] = []
    error: List[BaseException] = []

    def _worker() -> None:
        try:
            result.append(asyncio.run(_with_timeout()))
        except BaseException as err:  # noqa: BLE001 - re-raised below
            error.append(err)

    thread = threading.Thread(target=_worker, name="local-principal", daemon=True)
    thread.start()
    thread.join(timeout + 1.0)
    if error:
        raise error[0]
    if not result:
        raise TimeoutError("Resolving the local principal timed out")
    return result[0]


def prompt_for_principal(candidates: List[Any]) -> Optional[Any]:
    """Ask which enrolled person this session belongs to (interactive only)."""
    print(
        "\nThis session has no channel identity, so Hermes does not know whose "
        "memory it is writing.",
        file=sys.stderr,
    )
    print("Enrolled people:", file=sys.stderr)
    for index, principal in enumerate(candidates, start=1):
        display = getattr(principal, "display", "") or "no display name"
        print(
            f"  {index}. {principal.user_id}  ({principal.role}) — {display}",
            file=sys.stderr,
        )
    print(
        "Answer once and Hermes will remember it for this profile "
        "(change it later with 'hermes member local-principal').",
        file=sys.stderr,
    )
    try:
        answer = input("Which one are you? [number, or blank to skip] ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return None
    if not answer:
        return None
    if answer.isdigit():
        position = int(answer)
        if 1 <= position <= len(candidates):
            return candidates[position - 1]
        print(f"No such choice: {answer}", file=sys.stderr)
        return None
    for principal in candidates:
        if principal.user_id == answer:
            return principal
    print(f"No enrolled principal matches {answer!r}.", file=sys.stderr)
    return None


def can_ask() -> bool:
    """True when there is a human on the other end of this process."""
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError):  # closed or replaced streams
        return False


def resolve_local_principal(
    *,
    login_subject: Optional[str] = None,
    ask: Optional[Callable[[List[Any]], Optional[Any]]] = None,
    store: Optional[Any] = None,
    timeout: float = 8.0,
) -> LocalResolution:
    """Resolve the principal for a session that arrived without a channel.

    Never raises: an unreachable directory is reported as unresolved with zero
    candidates, which keeps the single-user (no database) deployment on its
    pre-FG-24 path.
    """
    remembered = read_binding()
    try:
        principals = _enrolled(store=store, timeout=timeout)
    except Exception as err:  # noqa: BLE001 - directory is best-effort
        logger.debug("Local principal directory unavailable: %s", err)
        # A remembered answer is still the best available truth: it was
        # validated when it was written.
        return LocalResolution(binding=remembered, candidates=0)

    by_id = {p.user_id: p for p in principals}

    if remembered is not None:
        live = by_id.get(remembered.user_id)
        if live is not None:
            # Re-read the role: a promotion or demotion since must not be
            # frozen into a file that grants shared-block authority.
            if live.role != remembered.role:
                remembered = remember_binding(live.user_id, live.role, remembered.source)
            return LocalResolution(binding=remembered, candidates=len(principals))
        # The person was un-enrolled; a stale binding must not keep acting.
        logger.info(
            "Forgetting the local principal binding: %s is no longer enrolled",
            remembered.user_id,
        )
        forget_binding()

    if login_subject:
        # An unaliased subject *is* the principal id (the common case for anyone
        # enrolled after the auth provider existed), so fall back to it.
        resolved = (
            _resolve_login_subject(login_subject, store=store, timeout=timeout)
            or login_subject
        )
        if resolved in by_id:
            principal = by_id[resolved]
            return LocalResolution(
                binding=remember_binding(principal.user_id, principal.role, "login"),
                candidates=len(principals),
            )

    if len(principals) == 1:
        only = principals[0]
        return LocalResolution(
            binding=remember_binding(only.user_id, only.role, "setup"),
            candidates=1,
        )

    if principals and ask is not None:
        chosen = ask(list(principals))
        if chosen is not None:
            return LocalResolution(
                binding=remember_binding(chosen.user_id, chosen.role, "asked"),
                candidates=len(principals),
            )

    return LocalResolution(binding=None, candidates=len(principals))


def _default_store() -> Any:
    from hermes_cli.access import PrincipalStore
    from hermes_cli.datastore import get_store

    return PrincipalStore(get_store("supabase-app", "prod"))


def _enrolled(*, store: Optional[Any], timeout: float) -> List[Any]:
    principal_store = store if store is not None else _default_store()
    return list(_run_async(principal_store.list_principals(), timeout=timeout))


def _resolve_login_subject(
    subject: str, *, store: Optional[Any], timeout: float
) -> Optional[str]:
    principal_store = store if store is not None else _default_store()
    try:
        resolved = _run_async(principal_store.resolve_alias(subject), timeout=timeout)
    except Exception as err:  # noqa: BLE001 - the next rung still applies
        logger.debug("Login-subject alias lookup failed: %s", err)
        return None
    return str(resolved) if resolved else None
