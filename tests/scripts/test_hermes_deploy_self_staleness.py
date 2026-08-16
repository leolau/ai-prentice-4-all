"""A fix to the deploy script does nothing until someone copies it to the box.

`/opt/data/deploy-hermes.sh` is a hand-installed copy of
`deploy/hermes-deploy.sh`. #292 fixed the deploy's inability to delete files,
merged, deployed green — and did not run, because the box was still executing
the old copy. Nothing said so.

The check is reported, never self-applied: a script that overwrites itself while
bash is still reading it is its own class of bug. Silent when the two agree, for
the same reason the handover note is (#274).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "hermes-deploy.sh"


def _self_check_block() -> str:
    """The staleness check, verbatim from the deploy script."""
    text = DEPLOY_SCRIPT.read_text()
    match = re.search(r'^SELF=\$\(readlink -f "\$0"\)$.*?^fi$', text, re.S | re.M)
    assert match, "the deploy-tool staleness check moved; update this test with it"
    return match.group(0)


def _run(tmp_path: Path, reviewed: str) -> str:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy" / "hermes-deploy.sh").write_text(reviewed)
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -euo pipefail\n"
        f"REPO={repo}\n"
        "AFTER=abc1234\n" + _self_check_block() + "\n"
    )
    result = subprocess.run(
        ["bash", str(runner)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_stale_installed_copy_is_reported(tmp_path: Path) -> None:
    """`$0` is the runner, which differs from the reviewed copy: stale."""
    output = _run(tmp_path, reviewed="a newer deploy script\n")

    assert "DEPLOY TOOL STALE" in output
    assert "install -m 755" in output


def test_an_identical_copy_says_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    runner = tmp_path / "run.sh"
    body = (
        "set -euo pipefail\n"
        f"REPO={repo}\n"
        "AFTER=abc1234\n" + _self_check_block() + "\n"
    )
    runner.write_text(body)
    # The reviewed copy IS what is running, byte for byte.
    (repo / "deploy" / "hermes-deploy.sh").write_text(body)

    result = subprocess.run(["bash", str(runner)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_missing_reviewed_copy_does_not_break_the_deploy(tmp_path: Path) -> None:
    """The check runs after `deploy OK`; it must never fail the deploy itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = tmp_path / "run.sh"
    runner.write_text(
        "set -euo pipefail\n"
        f"REPO={repo}\n"
        "AFTER=abc1234\n" + _self_check_block() + "\n"
        "echo survived\n"
    )

    result = subprocess.run(["bash", str(runner)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "survived"
