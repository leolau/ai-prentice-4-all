"""Regression: ``hermes profile suggest`` crashed before it could do anything.

The defect (found by the 2026-08-17 systest UAT, case D2): the CLI branch
constructed ``GoalRegistryStore()`` without its required injected store —
``TypeError: GoalRegistryStore.__init__() missing 1 required positional
argument: 'store'`` — while the scheduled sibling (``run_review_pass``)
built the same tree correctly via ``default_tree_store()``. The timer stayed
green and nothing on the box ever typed the command, so the crash shipped.
"""

from __future__ import annotations

from types import SimpleNamespace

from hermes_cli import main as cli_main
from hermes_cli.goal_tree import GoalRegistryStore


class _AppStore:
    """Stand-in for the contract-C3 supabase-app store."""

    mode = "prod"
    schema = "app_prod"


class _Owner:
    user_id = "root"
    display = "root"
    role = "owner"
    is_owner = True


def test_profile_suggest_wires_the_registry_to_the_app_store(monkeypatch):
    """The suggest branch must inject the app store, not construct it bare."""
    app_store = _AppStore()
    registry_stores: list[object] = []
    generated: list[object] = []

    real_registry = GoalRegistryStore

    def _spy_registry(store):
        registry_stores.append(store)
        return real_registry(store)

    class _Principals:
        def __init__(self, _store):
            pass

        async def get_owner(self):
            return _Owner()

    class _Promotions:
        def __init__(self, _store):
            pass

    async def _generate(tree, promotions, principal, **_kwargs):
        generated.append(tree)
        return None

    # Every import inside the branch is call-local, so patching the source
    # modules is what the running branch sees.
    monkeypatch.setattr(
        "hermes_cli.datastore.get_store", lambda *a, **k: app_store
    )
    monkeypatch.setattr("hermes_cli.goal_tree.GoalRegistryStore", _spy_registry)
    monkeypatch.setattr("hermes_cli.access.PrincipalStore", _Principals)
    monkeypatch.setattr(
        "hermes_cli.skill_promotion.SkillPromotionStore", _Promotions
    )
    monkeypatch.setattr(
        "hermes_cli.profile_suggestion.generate_suggestion", _generate
    )

    # Raises TypeError on the pre-fix code: GoalRegistryStore() without a store.
    cli_main.cmd_profile(SimpleNamespace(profile_action="suggest"))

    assert registry_stores == [app_store], (
        "the suggest branch must inject the app store into GoalRegistryStore — "
        "constructing it bare raises TypeError before anything runs"
    )
    assert len(generated) == 1, "generate_suggestion was never reached"
