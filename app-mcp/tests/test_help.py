from app_mcp.help import HELP, help_for
from app_mcp.pages import PAGES


def test_every_page_has_a_guide():
    for page in PAGES:
        assert help_for(page["path"]), page["path"]


def test_subpages_inherit_the_feature_guide():
    assert help_for("/projects/[slug]/runs/[runNo]") == HELP["/projects"]
    assert help_for("/projects/acme-launch/cards/42") == HELP["/projects"]
    assert help_for("/todos/[id]") == HELP["/todos"]


def test_unknown_and_empty_paths():
    assert help_for(None) is None
    assert help_for("") is None
    assert help_for("/no-such-page") is None


def test_projects_guide_covers_the_lifecycle_and_axes():
    guide = help_for("/projects")
    for word in ("Create", "readiness", "Activate", "Run now", "Board", "Accept",
                 "Summarise", "Archive", "One-off", "Repeatable", "Standing",
                 "Manual", "Supervised", "Autonomous"):
        assert word.lower() in guide.lower(), word
