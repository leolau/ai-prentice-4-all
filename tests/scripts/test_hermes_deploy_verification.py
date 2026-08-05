"""The deploy script's own verdict must survive a unit that isn't active.

`deploy/hermes-deploy.sh` runs under `set -euo pipefail` and ends with a loop
that reports each service's state. `systemctl is-active` exits nonzero for
anything that is not active (3 for an inactive oneshot that has finished), so
an unguarded `s=$(systemctl is-active "$u")` aborted the loop at the first such
unit: the script exited 3 on an otherwise successful deploy, printed neither
the remaining units nor its own "deploy OK", and reported nothing about the
services after the one that stopped it.

This exercises the real loop text lifted from the script against a stubbed
`systemctl`, so the guard cannot regress silently.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "hermes-deploy.sh"

STUB = """#!/bin/bash
# Mimic systemctl's exit codes: 0 + "active", 3 + "inactive" otherwise.
if [ "$1" = "is-active" ]; then
  case "$2" in
    *down*) echo inactive; exit 3;;
    *) echo active; exit 0;;
  esac
fi
exit 0
"""


def _verification_loop() -> str:
    """The `fail=0 … DEPLOY WARNING` block, verbatim from the deploy script."""
    text = DEPLOY_SCRIPT.read_text()
    match = re.search(r"^fail=0$.*?^\[ \"\$fail\" = 0 \].*?$", text, re.S | re.M)
    assert match, "the verification loop moved; update this test with it"
    return match.group(0)


def _run(units: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "systemctl"
    stub.write_text(STUB)
    stub.chmod(0o755)
    script = tmp_path / "loop.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'VERIFY_UNITS="{units}"\n'
        "sleep() { :; }\n" + _verification_loop() + "\n"
        'echo "deploy OK"\n'
    )
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{stub_dir}:/usr/bin:/bin"},
        check=False,
    )


def test_every_unit_is_reported_even_after_an_inactive_one(tmp_path: Path) -> None:
    result = _run("a.service down.service b.service", tmp_path)
    reported = [line.split()[0] for line in result.stdout.splitlines() if ".service" in line]
    assert reported == ["a.service", "down.service", "b.service"], result.stdout
    # A down unit is a failed deploy — but a *deliberate* exit 1, not an abort.
    assert result.returncode == 1
    assert "DEPLOY WARNING" in result.stderr


def test_all_active_reports_success(tmp_path: Path) -> None:
    result = _run("a.service b.service", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "deploy OK" in result.stdout


@pytest.mark.parametrize(
    ("needle", "why"),
    [
        (
            "--state=enabled",
            "the unit list must come from enabled unit files, or the oneshot "
            "jobs behind timers get restarted (i.e. run) by every deploy",
        ),
        (
            "agent-home.service",
            "a deploy that leaves the phone app down must fail loudly",
        ),
    ],
)
def test_script_keeps_the_properties_the_loop_depends_on(needle: str, why: str) -> None:
    assert needle in DEPLOY_SCRIPT.read_text(), why
