"""The handover document's own freshness claim must be checked, not trusted.

`docs/deployment/README.md` says it is "authoritative for what is currently
true of the live box" and names the revision it was verified against. Nothing
made that true: it sat at `657f1190b` across three deploys while the box moved
to `f4bc8af21` — including the deploy that moved the phone app to a different
checkout and a different user. An agent picking the box up cold reads that file
as fact, so a silently stale one is worse than none.

These tests pin the check on a real git repository rather than a mock, because
the failure mode being guarded against is a claim that looks right.
"""

import subprocess

import pytest

import scripts.deploy_state as ds

DRIFT = ds.SEVERITY_DRIFT
NOTE = ds.SEVERITY_NOTE

DOC = """# The `hermes-systest` deployment — handover

Last verified: {date}, application at `{revision}`.

## The box
"""


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def checkout(tmp_path):
    """A real git checkout with a handover doc, and its own HEAD."""
    repo = tmp_path / "checkout"
    (repo / ds.HANDOVER_DOC.parent).mkdir(parents=True)
    _git(repo.parent, "init", "-q", "checkout")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")

    def commit(text: str) -> str:
        (repo / ds.HANDOVER_DOC).write_text(text, encoding="utf-8")
        _git(repo, "add", str(ds.HANDOVER_DOC))
        _git(repo, "commit", "-q", "-m", "doc")
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return repo, commit


def test_a_doc_naming_the_deployed_revision_is_clean(checkout):
    repo, commit = checkout
    head = commit(DOC.format(date="2026-08-05", revision="0" * 9))
    (repo / ds.HANDOVER_DOC).write_text(
        DOC.format(date="2026-08-05", revision=head), encoding="utf-8"
    )
    assert ds.check_handover_doc(repo) == []


def test_the_real_failure_a_doc_three_deploys_behind_is_drift(checkout):
    repo, commit = checkout
    commit(DOC.format(date="2026-08-04", revision="657f1190b"))
    findings = ds.check_handover_doc(repo)
    assert [f["severity"] for f in findings] == [DRIFT]
    finding = findings[0]
    assert finding["component"] == "handover-doc"
    # The report must name both revisions: "stale" without them is unactionable.
    assert "657f1190b" in finding["actual"]
    assert "2026-08-04" in finding["actual"]
    assert "deployed" in finding["expected"]


def test_an_abbreviated_claim_matching_head_is_not_drift(checkout):
    """The doc carries a short sha; HEAD is full. That is not a mismatch."""
    repo, commit = checkout
    head = commit(DOC.format(date="2026-08-05", revision="0" * 9))
    (repo / ds.HANDOVER_DOC).write_text(
        DOC.format(date="2026-08-05", revision=head[:7]), encoding="utf-8"
    )
    assert ds.check_handover_doc(repo) == []


def test_a_doc_with_no_claim_cannot_be_checked_and_says_so(checkout):
    repo, commit = checkout
    commit("# handover\n\nNo revision stated anywhere.\n")
    findings = ds.check_handover_doc(repo)
    assert [f["severity"] for f in findings] == [DRIFT]
    assert "no parseable claim" in findings[0]["actual"]


def test_a_missing_doc_is_drift(tmp_path):
    findings = ds.check_handover_doc(tmp_path)
    assert [f["severity"] for f in findings] == [DRIFT]
    assert findings[0]["actual"] == "absent"


def test_an_unreadable_revision_is_a_note_not_a_false_stale_report(tmp_path):
    """No git repository: unknown freshness must not masquerade as staleness."""
    (tmp_path / ds.HANDOVER_DOC.parent).mkdir(parents=True)
    (tmp_path / ds.HANDOVER_DOC).write_text(
        DOC.format(date="2026-08-05", revision="deadbee"), encoding="utf-8"
    )
    findings = ds.check_handover_doc(tmp_path)
    assert [f["severity"] for f in findings] == [NOTE]


def test_the_cli_exits_nonzero_on_a_stale_doc_and_names_it(
    capsys, monkeypatch, checkout
):
    repo, commit = checkout
    commit(DOC.format(date="2026-08-04", revision="657f1190b"))
    monkeypatch.setattr(ds, "REPO_ROOT", repo)
    assert ds.main(["handover"]) == 1
    out = capsys.readouterr().out
    assert "STALE" in out and "657f1190b" in out

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / ds.HANDOVER_DOC).write_text(
        DOC.format(date="2026-08-05", revision=head[:9]), encoding="utf-8"
    )
    assert ds.main(["handover"]) == 0
    assert "current" in capsys.readouterr().out


def test_the_state_check_does_not_report_doc_freshness():
    """`check` compares the box against its snapshot; HEAD is not part of that.

    Keeping the doc check out of `check` matters for a development clone, where
    HEAD is legitimately ahead of any deployed revision — otherwise every PR
    would carry a permanent, meaningless drift finding.
    """
    source = (ds.REPO_ROOT / "scripts" / "deploy_state.py").read_text(encoding="utf-8")
    body = source.split("def check(", 1)[1].split("\ndef ", 1)[0]
    assert "check_handover_doc" not in body


def test_the_deploy_script_reports_doc_freshness_without_failing_the_deploy():
    script = (ds.REPO_ROOT / "deploy" / "hermes-deploy.sh").read_text(encoding="utf-8")
    line = next(
        raw for raw in script.splitlines() if "deploy_state.py handover" in raw
    )
    # A stale document is a documentation problem; it must not abort a deploy
    # under `set -e`, and it must be reported after the unit verdict.
    assert line.rstrip().endswith("|| true")
    assert script.index("deploy OK") < script.index("deploy_state.py handover")
