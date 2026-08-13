"""A transient fetch failure must not leave a silent no-op deploy.

`deploy/hermes-deploy.sh` fetches over SSH to github.com from cn-hongkong, and
a connect timeout there is occasional but real: one happened mid-deploy and the
box stayed on the previous revision while the surrounding output looked
ordinary. Aborting on an unverifiable revision is correct; giving up after a
single attempt, and doing so quietly, is not.

The real fetch block is lifted out of the script and run against a stubbed
`git`, so both halves — retry, then a loud abort — cannot regress silently.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "hermes-deploy.sh"

#: Fails `fetch` until a counter file reaches $GIT_FAIL_UNTIL, so one stub
#: covers "recovers on the 2nd attempt" and "never recovers".
STUB = """#!/bin/bash
if [ "$1" = "fetch" ]; then
  n=$(cat "$COUNTER" 2>/dev/null || echo 0)
  n=$((n + 1))
  echo "$n" > "$COUNTER"
  if [ "$n" -le "$GIT_FAIL_UNTIL" ]; then
    echo "ssh: connect to host github.com port 22: Connection timed out" >&2
    exit 128
  fi
  echo "fetched on attempt $n"
  exit 0
fi
echo deadbeef
exit 0
"""


def _fetch_block() -> str:
    """The retry loop and its abort, verbatim from the deploy script."""
    text = DEPLOY_SCRIPT.read_text()
    match = re.search(r"^fetched=$.*?^fi$", text, re.S | re.M)
    assert match, "the fetch retry block moved; update this test with it"
    return match.group(0)


def _run(fail_until: int, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(STUB)
    stub.chmod(0o755)
    script = tmp_path / "fetch.sh"
    script.write_text(
        "set -euo pipefail\n"
        'BRANCH=develop\n'
        "sleep() { :; }\n" + _fetch_block() + "\n"
        'echo "reached the install steps"\n'
    )
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "COUNTER": str(tmp_path / "n"),
            "GIT_FAIL_UNTIL": str(fail_until),
        },
        check=False,
    )


def test_a_transient_failure_is_retried_and_the_deploy_continues(tmp_path: Path) -> None:
    result = _run(1, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fetched on attempt 2" in result.stdout
    assert "reached the install steps" in result.stdout


def test_a_persistent_failure_aborts_loudly_and_says_nothing_was_deployed(
    tmp_path: Path,
) -> None:
    result = _run(99, tmp_path)
    assert result.returncode == 1
    assert "reached the install steps" not in result.stdout
    # The operator must be able to tell "nothing happened" from "deployed".
    assert "FETCH FAILED" in result.stderr
    assert "nothing deployed" in result.stderr
    assert (tmp_path / "n").read_text().strip() == "3", "should try three times"
