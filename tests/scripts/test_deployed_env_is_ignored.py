"""The files a deploy writes *into* the checkout must be git-ignored.

FG-23 A0 retires the second clone and runs `agent-home` from the deployment's
own checkout. Two things then live inside a git tree that the drift check
inspects and that `deploy/hermes-deploy.sh` pulls into:

  * `agent-home/.next/` — the build output, regenerated on every deploy;
  * `agent-home/agent-home.env` — the session-signing key and the
    `agent_home_app` DATABASE_URL, which is the one thing on the box not
    reproducible from git.

If either is untracked-but-not-ignored, the deployment's checkout is
permanently dirty (so "clean tree" stops meaning anything) and the secret is
one `git add .` from being committed. The `.env*` patterns do not match
`agent-home.env`, so this is asserted rather than assumed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [
        "agent-home/agent-home.env",
        "agent-home/.next/BUILD_ID",
    ],
)
def test_path_written_by_the_deploy_is_ignored(relative_path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{relative_path} is not git-ignored; the deployment writes it into the "
        "checkout, so the tree would never be clean again"
    )
