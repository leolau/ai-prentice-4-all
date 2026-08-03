"""Invariants for scripts/backup_secrets.py.

The material this backs up cannot be regenerated: a lost WhatsApp
`creds.json` means re-pairing a phone, a lost Google token means re-running
consent for every account. Before this existed the only copies were on the
same disk as the live data.

Three properties decide whether the mechanism is worth trusting, and each is
asserted rather than argued:

  1. **Nothing readable leaves the box.** The bundle must be real `age`
     ciphertext and the plaintext must never be written to disk — including
     when the run fails partway.
  2. **A restore actually restores.** Contents, modes and owners must come
     back, or "we have backups" is a story rather than a recovery.
  3. **Silence must mean current.** A stale or incomplete backup has to be
     reported; a check that cannot tell the difference is worse than none.

`age` is required — these tests exercise the real binary rather than a fake,
because the failure mode being guarded against is "we wrote something that
turned out not to be encrypted".
"""

import os
import shutil
import subprocess
import tarfile

import pytest
import yaml

import scripts.backup_secrets as bs
import scripts.deploy_state as ds

DRIFT = ds.SEVERITY_DRIFT
NOTE = ds.SEVERITY_NOTE

pytestmark = pytest.mark.skipif(
    shutil.which("age") is None, reason="age is not installed"
)

ENV_TEXT = "TELEGRAM_BOT_TOKEN=8123:AAF-secret\nAWS_REGION=ap-east-1\n"
TOKEN_JSON = '{"refresh_token": "1//0eZZZZZZZZZZZZZZZZZZZZZZ"}'
SECRETS_TEXT = "MCP_SERVERS_GITHUB_HEADERS_AUTHORIZATION=ghp_zzzzzzzzzzzzzzzzzzzz\n"


@pytest.fixture
def identity(tmp_path):
    """A throwaway age keypair, standing in for the one in a password manager."""
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], capture_output=True, check=True)
    recipient = ""
    for line in key.read_text(encoding="utf-8").splitlines():
        if line.startswith("# public key:"):
            recipient = line.split(":", 1)[1].strip()
    assert recipient.startswith("age1")
    return key, recipient


@pytest.fixture
def deployment(tmp_path):
    """A miniature deployment plus its captured state manifest."""
    home = tmp_path / "hermes-home"
    (home / "creds").mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": "deepseek"}}), encoding="utf-8"
    )
    env = home / ".env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    env.chmod(0o600)
    token = home / "creds" / "user@example.com.json"
    token.write_text(TOKEN_JSON, encoding="utf-8")
    token.chmod(0o600)

    units = tmp_path / "systemd"
    units.mkdir()
    (units / "hermes-gateway.service").write_text(
        "[Service]\nUser=hermes\n", encoding="utf-8"
    )

    # In its own directory, mirroring the box: the secrets file lives under a
    # root-only `0700` dir, which is what makes it unreachable to the checker.
    (tmp_path / "deploy").mkdir()
    secrets_file = tmp_path / "deploy" / "state-secrets.env"
    secrets_file.write_text(SECRETS_TEXT, encoding="utf-8")
    secrets_file.chmod(0o600)

    state_root = tmp_path / "state"
    ds.capture(
        "systest-fixture",
        home,
        units,
        "hermes-*",
        None,
        ["creds/*.json"],
        secrets_out=secrets_file,
        state_root=state_root,
    )
    # A clone with a remote, because "offsite" is a property of the push, not of
    # a file existing in a directory.
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = tmp_path / "backups"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(repo)], capture_output=True, check=True
    )
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(repo, "push", "-q", "origin", "HEAD")
    return home, state_root, repo, secrets_file


def _git(repo, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@localhost",
            *args,
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def _publish(repo):
    """Commit and push whatever a test just did to the repo, so the offsite
    findings stay quiet and the test's own subject is what is asserted."""
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", "test edit")
    _git(repo, "push", "-q", "origin", "HEAD")


def _backup(deployment, recipient, push=True, **kwargs):
    home, state_root, repo, _ = deployment
    return bs.backup(
        "systest-fixture", state_root, repo, recipient, push=push, **kwargs
    )


def test_the_bundle_is_real_age_ciphertext_and_leaks_nothing(deployment, identity):
    """The whole point: a compromised box can add backups it cannot read."""
    _, recipient = identity
    result = _backup(deployment, recipient)
    blob = (bs.Path(result["bundle"])).read_bytes()

    assert blob.startswith(bs.AGE_MAGIC)
    for secret in (b"8123:AAF-secret", b"1//0eZ", b"ghp_z"):
        assert secret not in blob


def test_what_gets_backed_up_comes_from_the_manifest(deployment, identity):
    """A credential added to the deployment is backed up as soon as it is
    captured — the two cannot drift apart by construction."""
    home, state_root, repo, secrets_file = deployment
    paths = {str(p) for p in bs.members("systest-fixture", state_root)}
    assert paths == {
        str(home / ".env"),
        str(home / "creds" / "user@example.com.json"),
        str(secrets_file),
    }


def test_restore_brings_back_contents_modes_and_owners(deployment, identity, tmp_path):
    key, recipient = identity
    home, _, _, secrets_file = deployment
    result = _backup(deployment, recipient)

    out = tmp_path / "restored"
    written = bs.restore(bs.Path(result["bundle"]), key, out)

    restored_env = out / str(home / ".env").lstrip("/")
    assert restored_env.read_text(encoding="utf-8") == ENV_TEXT
    assert oct(restored_env.stat().st_mode & 0o777) == "0o600"
    assert {item["mode"] for item in written} == {"600"}
    restored_secrets = out / str(secrets_file).lstrip("/")
    assert restored_secrets.read_text(encoding="utf-8") == secrets_file.read_text(
        encoding="utf-8"
    )


def test_a_bundle_cannot_be_read_with_the_wrong_key(deployment, identity, tmp_path):
    _, recipient = identity
    result = _backup(deployment, recipient)
    other = tmp_path / "other.txt"
    subprocess.run(["age-keygen", "-o", str(other)], capture_output=True, check=True)

    with pytest.raises(ds.StateError, match="could not decrypt"):
        bs.restore(bs.Path(result["bundle"]), other, tmp_path / "nope")


def test_an_unchanged_secret_set_does_not_write_a_new_bundle(deployment, identity):
    """Otherwise a daily timer produces a daily commit of noise, and nobody
    reads the history that is supposed to show when a secret changed."""
    _, recipient = identity
    first = _backup(deployment, recipient)
    second = _backup(deployment, recipient)

    assert first["unchanged"] is False
    assert second["unchanged"] is True
    assert second["digest"] == first["digest"]


def test_a_rotated_secret_does_write_a_new_bundle(deployment, identity):
    home, _, _, _ = deployment
    _, recipient = identity
    first = _backup(deployment, recipient)
    (home / ".env").write_text(
        ENV_TEXT.replace("AAF-secret", "AAF-rotated"), encoding="utf-8"
    )
    second = _backup(deployment, recipient)

    assert second["unchanged"] is False
    assert second["digest"] != first["digest"]


def test_the_index_records_paths_but_never_contents(deployment, identity):
    """The index is the one file readable without the private key, so it must
    hold nothing a reader could not already get from the state repo."""
    home, _, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient)

    text = (repo / "systest-fixture" / bs.INDEX_NAME).read_text(encoding="utf-8")
    index = yaml.safe_load(text)
    assert index["files"] == sorted(index["files"])
    assert "8123:AAF-secret" not in text and "1//0eZ" not in text
    # One aggregate digest, not a per-file hash of every secret.
    assert "content_digest" in index
    assert not any(key.endswith("sha256") for key in index)


def test_verify_is_clean_right_after_a_backup(deployment, identity):
    home, state_root, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient)
    assert bs.verify("systest-fixture", state_root, repo) == []


def test_a_credential_added_since_the_last_backup_is_reported(deployment, identity):
    """The failure this is really guarding: Google tokens were installed on the
    box after the last backup ran, so no backup held them at all."""
    home, state_root, repo, secrets_file = deployment
    _, recipient = identity
    _backup(deployment, recipient)

    new_token = home / "creds" / "second@example.com.json"
    new_token.write_text(TOKEN_JSON, encoding="utf-8")
    new_token.chmod(0o600)
    ds.capture(
        "systest-fixture",
        home,
        state_root.parent / "systemd",
        "hermes-*",
        None,
        ["creds/*.json"],
        secrets_out=secrets_file,
        state_root=state_root,
    )

    findings = bs.verify("systest-fixture", state_root, repo)
    assert [f["severity"] for f in findings] == [DRIFT]
    assert "second@example.com" in findings[0]["component"]


def test_a_stale_backup_is_reported(deployment, identity):
    home, state_root, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient)
    index = bs.read_index(repo, "systest-fixture")
    index["created_at"] = (
        bs.datetime.now(bs.timezone.utc) - bs.timedelta(days=30)
    ).isoformat(timespec="seconds")
    bs.write_index(repo, "systest-fixture", index)
    _publish(repo)

    findings = bs.verify("systest-fixture", state_root, repo, max_age_days=8)
    assert [f["component"] for f in findings] == ["backup:age"]
    assert "30 days old" in findings[0]["actual"]


def test_no_backup_at_all_is_drift_not_silence(deployment):
    _, state_root, repo, _ = deployment
    findings = bs.verify("systest-fixture", state_root, repo)
    assert [f["severity"] for f in findings] == [DRIFT]
    assert "ever been written" in findings[0]["detail"]


def test_a_plaintext_bundle_is_refused_rather_than_trusted(deployment, identity):
    """Belt and braces: if anything ever wrote a bundle that is not encrypted,
    the check must say so instead of counting it as a good backup."""
    _, state_root, repo, _ = deployment
    _, recipient = identity
    result = _backup(deployment, recipient)
    bs.Path(result["bundle"]).write_bytes(b"TELEGRAM_BOT_TOKEN=8123:AAF-secret\n")
    _publish(repo)

    findings = bs.verify("systest-fixture", state_root, repo)
    assert [f["component"] for f in findings] == ["backup:bundle"]
    assert findings[0]["actual"] == "not an age file"


def test_a_credential_dropped_from_the_manifest_is_a_note(deployment, identity):
    home, state_root, repo, secrets_file = deployment
    _, recipient = identity
    _backup(deployment, recipient)
    (home / "creds" / "user@example.com.json").unlink()
    ds.capture(
        "systest-fixture",
        home,
        state_root.parent / "systemd",
        "hermes-*",
        None,
        ["creds/*.json"],
        secrets_out=secrets_file,
        state_root=state_root,
    )

    findings = bs.verify("systest-fixture", state_root, repo)
    assert [f["severity"] for f in findings] == [NOTE]
    assert "user@example.com" in findings[0]["component"]


def test_a_bundle_that_never_left_the_box_is_not_a_backup(deployment, identity):
    """Found on the box: the push failed, and `verify` still reported "current"
    because a file existed. A bundle on the same disk as the secrets it protects
    is the situation this tool exists to end."""
    _, state_root, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient, push=False)

    findings = bs.verify("systest-fixture", state_root, repo)

    offsite = [f for f in findings if f["component"] == "backup:offsite"]
    assert [f["severity"] for f in offsite] == [DRIFT]
    assert "never committed" in offsite[0]["detail"]


def test_commits_that_were_never_pushed_are_drift(deployment, identity):
    _, state_root, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient, push=False)
    _git(repo, "add", "--all", "systest-fixture")
    _git(repo, "commit", "-q", "-m", "local only")

    findings = bs.verify("systest-fixture", state_root, repo)

    offsite = [f for f in findings if f["component"] == "backup:offsite"]
    assert [f["actual"] for f in offsite] == ["local only"]


def test_a_backup_directory_that_is_not_a_clone_is_drift(
    deployment, identity, tmp_path
):
    _, state_root, _, _ = deployment
    _, recipient = identity
    plain = tmp_path / "plain"
    bs.backup("systest-fixture", state_root, plain, recipient)

    findings = bs.verify("systest-fixture", state_root, plain)

    assert [(f["component"], f["actual"]) for f in findings] == [
        ("backup:offsite", "not a git repository")
    ]


def test_a_secrets_file_the_checker_cannot_see_is_a_note_not_a_crash(
    deployment, identity
):
    """`verify` runs as unprivileged `hermes` while the secrets file is
    root-only, so it *will* meet a file it cannot stat. Crashing there loses the
    freshness and coverage findings it could have reported — found on the box,
    the same way #87 was."""
    home, state_root, repo, secrets_file = deployment
    _, recipient = identity
    _backup(deployment, recipient)

    secrets_file.parent.chmod(0o000)
    try:
        findings = bs.verify("systest-fixture", state_root, repo)
    finally:
        secrets_file.parent.chmod(0o755)

    assert [(f["component"], f["severity"]) for f in findings] == [
        (f"backup:coverage:{secrets_file.name}", NOTE)
    ]
    assert findings[0]["actual"] == "permission denied"


def test_backup_refuses_without_a_recipient(deployment, monkeypatch, capsys):
    _, state_root, repo, _ = deployment
    monkeypatch.delenv("HERMES_BACKUP_RECIPIENT", raising=False)
    code = bs.main([
        "--state-root",
        str(state_root),
        "--repo",
        str(repo),
        "backup",
        "--deployment",
        "systest-fixture",
    ])
    assert code == 2
    assert "age recipient" in capsys.readouterr().err


def test_prune_keeps_the_working_tree_small_without_losing_history(
    deployment, identity
):
    """Git keeps every bundle; the working tree does not need to."""
    _, state_root, repo, _ = deployment
    _, recipient = identity
    for index in range(4):
        _backup(deployment, recipient, force=True)
        # Timestamps are second-resolution, so make the names deterministic.
        newest = sorted((repo / "systest-fixture").glob(f"*{bs.BUNDLE_SUFFIX}"))[-1]
        newest.rename(newest.with_name(f"2026010{index + 1}T000000Z{bs.BUNDLE_SUFFIX}"))

    removed = bs.prune(repo / "systest-fixture", keep=2)
    remaining = sorted(
        p.name for p in (repo / "systest-fixture").glob(f"*{bs.BUNDLE_SUFFIX}")
    )
    assert len(removed) == 2
    assert remaining == [
        f"20260103T000000Z{bs.BUNDLE_SUFFIX}",
        f"20260104T000000Z{bs.BUNDLE_SUFFIX}",
    ]


def test_the_tar_is_deterministic_so_digests_mean_something(deployment, identity):
    """Zeroed mtimes and ids, sorted members: otherwise every run looks changed
    and the skip-if-unchanged behaviour silently stops working."""
    _, state_root, _, _ = deployment
    paths = bs.members("systest-fixture", state_root)
    first, digest_one, _ = bs.build_tar(paths)
    second, digest_two, _ = bs.build_tar(paths)

    assert first == second
    assert digest_one == digest_two
    with tarfile.open(fileobj=bs.io.BytesIO(first), mode="r:gz") as archive:
        assert {member.mtime for member in archive.getmembers()} == {0}
        assert bs.INNER_MANIFEST in archive.getnames()


def test_modes_and_owners_travel_inside_the_ciphertext(deployment, identity):
    """Not in the index: file permissions of credential files are a map of what
    to attack, and the index is the part readable without the key."""
    _, state_root, repo, _ = deployment
    _, recipient = identity
    _backup(deployment, recipient)
    index = bs.read_index(repo, "systest-fixture")

    assert not any(key in index for key in ("modes", "owners", "files_detail"))
    assert all(isinstance(entry, str) for entry in index["files"])
    _, _, inventory = bs.build_tar(bs.members("systest-fixture", state_root))
    assert all(record["mode"] == "600" for record in inventory)
    assert all(":" in record["owner"] for record in inventory)


def test_nothing_is_written_when_the_manifest_has_no_files(tmp_path, identity):
    """Rather than committing an empty bundle that looks like a good backup."""
    home = tmp_path / "empty-home"
    home.mkdir()
    units = tmp_path / "units"
    units.mkdir()
    (home / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    (home / ".env").write_text("", encoding="utf-8")
    state_root = tmp_path / "state"
    ds.capture("empty", home, units, "hermes-*", None, [], state_root=state_root)
    (home / ".env").unlink()

    _, recipient = identity
    with pytest.raises(ds.StateError, match="nothing to back up"):
        bs.backup("empty", state_root, tmp_path / "repo", recipient)


def test_plaintext_never_reaches_the_disk(deployment, identity, monkeypatch):
    """The tar is streamed into age on stdin. Asserted by failing the
    encryption and checking nothing was left behind."""
    _, state_root, repo, _ = deployment
    _, recipient = identity

    def explode(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "age", stderr=b"boom")

    monkeypatch.setattr(bs.subprocess, "run", explode)
    with pytest.raises(ds.StateError, match="age failed"):
        bs.backup("systest-fixture", state_root, repo, recipient)

    leftovers = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    assert leftovers == []
