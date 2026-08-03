"""Invariants for scripts/check_runtime_drift.py.

Context: Hermes deployments run from a venv built on a uv-managed CPython
that statically bundles its own OpenSSL. `unattended-upgrades` patches the
*system* libssl and reports success, leaving the interpreter's copy — the
one every TLS connection actually uses — untouched. On the systest box the
two read 3.0.13 and 3.5.7 respectively. The checker exists so that gap
surfaces as a notification instead of a box that silently rots while
claiming to be fully patched.

These tests pin the contracts that make it trustworthy:
  1. A runtime below the declared floor is drift and exits non-zero.
  2. A runtime at or above the floor is not drift (an upgrade must not page
     someone at 3am), but running ahead of the baseline is reported so the
     repo pin gets bumped.
  3. Package pins are compared exactly; ranges are ignored rather than
     guessed at.
  4. The repo's own baseline is parseable and complete — otherwise the
     check silently degrades to a no-op, which is the failure mode that
     matters most here.
"""

import json
import sys

import pytest

import scripts.check_runtime_drift as drift

DRIFT = drift.SEVERITY_DRIFT
NOTE = drift.SEVERITY_NOTE


def _severities(findings, component):
    return [f["severity"] for f in findings if f["component"] == component]


# --- version parsing -------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3.11.15", (3, 11, 15)),
        ("OpenSSL 3.5.7 9 Jun 2026", (3, 5, 7)),
        ("3.0.13-0ubuntu3.12", (3, 0, 13)),
        ("OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13)", (3, 0, 13)),
        ("not a version", ()),
    ],
)
def test_parse_version_handles_real_world_shapes(text, expected):
    assert drift._parse_version(text) == expected


def test_version_ordering_is_numeric_not_lexicographic():
    # "3.5.7" > "3.0.13" numerically, but < lexicographically on the last
    # component. Getting this wrong would report a patched box as vulnerable.
    assert drift._parse_version("3.5.7") > drift._parse_version("3.0.13")
    assert drift._parse_version("3.11.15") > drift._parse_version("3.9.20")


# --- floor comparison ------------------------------------------------------


def test_below_floor_is_drift():
    findings = drift._check_floor("openssl", "3.0.13", "3.5.7", "stale crypto")
    assert [f["severity"] for f in findings] == [DRIFT]
    assert findings[0]["actual"] == "3.0.13"


def test_at_floor_is_clean():
    assert drift._check_floor("openssl", "3.5.7", "3.5.7", "why") == []


def test_above_floor_is_a_note_not_drift():
    # An upgraded runtime must not fire an alert, but the stale repo pin
    # should still be surfaced — otherwise the baseline stops describing
    # the deployment and a later downgrade would go unnoticed.
    findings = drift._check_floor("python", "3.12.4", "3.11.15", "why")
    assert [f["severity"] for f in findings] == [NOTE]


def test_uncomparable_version_is_drift_not_silence():
    findings = drift._check_floor("openssl", "unknown", "3.5.7", "why")
    assert [f["severity"] for f in findings] == [DRIFT]


# --- package pins ----------------------------------------------------------


def test_exact_pins_ignores_ranges_and_strips_extras():
    pins = drift._exact_pins(
        [
            "openai==2.24.0",
            "httpx[socks]==0.28.1",
            "urllib3>=2.7.0,<3",  # a range — nothing exact to assert
        ]
    )
    assert pins == {"openai": "2.24.0", "httpx": "0.28.1"}


def test_exact_pins_respects_environment_markers():
    # tzdata and concurrent-log-handler are Windows-only pins. Reporting
    # them as "not installed" on the Linux deployment would make every
    # weekly run cry wolf, and a report that always fires gets ignored.
    marker = "sys_platform == 'win32'" if sys.platform != "win32" else "sys_platform == 'linux'"
    assert drift._exact_pins([f"tzdata==2026.2; {marker}"]) == {}
    applies = "sys_platform != 'definitely-not-a-platform'"
    assert drift._exact_pins([f"rich==14.3.3; {applies}"]) == {"rich": "14.3.3"}


def test_repo_pins_do_not_report_platform_specific_deps_as_missing():
    # Guards the whole pipeline against the same cry-wolf failure using the
    # repo's real dependency list rather than a synthetic one.
    pins, _ = drift.load_baseline(drift.REPO_ROOT / "pyproject.toml")
    if sys.platform != "win32":
        assert "tzdata" not in pins
        assert "concurrent-log-handler" not in pins
    assert "openai" in pins, "expected the core exact pins to be collected"


def test_package_mismatch_and_absence_are_both_drift(monkeypatch):
    import importlib.metadata as md

    def fake_version(name):
        if name == "openai":
            return "2.23.0"  # older than the pin
        if name == "rich":
            return "14.3.3"  # matches
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(drift.metadata, "version", fake_version)
    findings = drift.check_packages(
        {"openai": "2.24.0", "rich": "14.3.3", "ghost": "1.0.0"}
    )
    by_component = {f["component"]: f for f in findings}
    assert set(by_component) == {"openai", "ghost"}
    assert by_component["ghost"]["actual"] == "not installed"


# --- baseline in this repo -------------------------------------------------


def test_repo_declares_a_complete_runtime_baseline():
    # A missing or half-filled table would make the check pass vacuously on
    # every box forever, which looks identical to "no drift".
    _, baseline = drift.load_baseline(drift.REPO_ROOT / "pyproject.toml")
    assert baseline, "[tool.hermes.runtime-baseline] is missing from pyproject.toml"
    assert drift._parse_version(str(baseline["python"])), "python floor unparseable"
    assert drift._parse_version(str(baseline["openssl"])), "openssl floor unparseable"


def test_baseline_python_is_within_requires_python():
    import tomllib

    with (drift.REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    baseline = data["tool"]["hermes"]["runtime-baseline"]["python"]
    requires = data["project"]["requires-python"]
    major_minor = ".".join(str(baseline).split(".")[:2])
    assert major_minor in requires or f">={major_minor}" in requires, (
        f"baseline python {baseline} is outside requires-python {requires}"
    )


# --- CLI contract ----------------------------------------------------------


def _write_baseline(tmp_path, *, python="3.11.15", openssl="99.0.0", deps=""):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        f"dependencies = [{deps}]\n"
        "[tool.hermes.runtime-baseline]\n"
        f'python = "{python}"\n'
        f'openssl = "{openssl}"\n',
        encoding="utf-8",
    )
    return pyproject


def test_exit_code_and_json_shape(monkeypatch, capsys, tmp_path):
    pyproject = _write_baseline(tmp_path, deps='"ghost==1.0.0"')
    code = drift.main(["--json", "--pyproject", str(pyproject)])
    assert code == 1
    findings = json.loads(capsys.readouterr().out)
    assert {f["component"] for f in findings} >= {"ghost", "openssl"}
    assert all(set(f) >= {"severity", "component", "expected", "actual"} for f in findings)


def test_clean_runtime_exits_zero_and_does_not_notify(monkeypatch, tmp_path):
    pyproject = _write_baseline(
        tmp_path,
        python=".".join(str(p) for p in sys.version_info[:3]),
        openssl=".".join(
            str(p) for p in drift._parse_version(drift.ssl.OPENSSL_VERSION)
        ),
    )
    sent = []
    monkeypatch.setattr(drift, "notify", lambda text: sent.append(text))
    assert drift.main(["--notify", "--pyproject", str(pyproject)]) == 0
    assert sent == [], "a clean run must stay silent"


def test_drift_notifies_once_with_the_report(monkeypatch, tmp_path):
    pyproject = _write_baseline(tmp_path)
    sent = []
    monkeypatch.setattr(drift, "notify", lambda text: sent.append(text))
    assert drift.main(["--notify", "--pyproject", str(pyproject)]) == 1
    assert len(sent) == 1
    assert "openssl" in sent[0]


# --- notification target ---------------------------------------------------

TELEGRAM_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_HOME_CHANNEL",
    "TELEGRAM_HOME_CHANNEL_THREAD_ID",
    "TELEGRAM_CRON_THREAD_ID",
    "TELEGRAM_ALLOWED_USERS",
)


@pytest.fixture
def clean_telegram_env(monkeypatch):
    for var in TELEGRAM_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_notify_targets_the_home_channel_and_its_thread(clean_telegram_env):
    # The systest deployment has TELEGRAM_HOME_CHANNEL + a thread id and no
    # TELEGRAM_ALLOWED_USERS at all; reading only the latter made the first
    # live run print "no Telegram credentials" and send nothing.
    clean_telegram_env.setenv("TELEGRAM_BOT_TOKEN", "t")
    clean_telegram_env.setenv("TELEGRAM_HOME_CHANNEL", "-1001234567890")
    clean_telegram_env.setenv("TELEGRAM_HOME_CHANNEL_THREAD_ID", "42")
    assert drift._telegram_target() == ("t", "-1001234567890", "42")


def test_notify_falls_back_to_allowed_users(clean_telegram_env):
    clean_telegram_env.setenv("TELEGRAM_BOT_TOKEN", "t")
    clean_telegram_env.setenv("TELEGRAM_ALLOWED_USERS", "111,222")
    assert drift._telegram_target() == ("t", "111", "")


def test_cron_thread_overrides_the_home_channel_thread(clean_telegram_env):
    clean_telegram_env.setenv("TELEGRAM_BOT_TOKEN", "t")
    clean_telegram_env.setenv("TELEGRAM_HOME_CHANNEL", "-100")
    clean_telegram_env.setenv("TELEGRAM_HOME_CHANNEL_THREAD_ID", "42")
    clean_telegram_env.setenv("TELEGRAM_CRON_THREAD_ID", "99")
    assert drift._telegram_target()[2] == "99"


def test_notify_without_credentials_is_not_fatal(clean_telegram_env, capsys):
    assert drift.notify("anything") is False
    assert "no Telegram credentials" in capsys.readouterr().out


def test_unreadable_baseline_exits_two_not_zero(tmp_path):
    # Exiting 0 on a broken baseline would report "no drift" from a check
    # that never ran.
    assert drift.main(["--pyproject", str(tmp_path / "missing.toml")]) == 2


@pytest.mark.parametrize(
    "table",
    [
        "",  # no [tool.hermes.runtime-baseline] at all
        "[tool.hermes.runtime-baseline]\n",  # present but empty
        '[tool.hermes.runtime-baseline]\npython = "3.11.15"\n',  # openssl dropped
        '[tool.hermes.runtime-baseline]\nopenssl = "3.5.7"\n',  # python dropped
    ],
)
def test_incomplete_baseline_is_loud_not_a_vacuous_pass(table, tmp_path, capsys):
    # A merge that drops or renames one floor would otherwise leave that layer
    # unwatched while the weekly run still printed "No runtime drift" and
    # exited 0 — output identical to a healthy box, which is exactly the false
    # confidence this script exists to remove.
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\ndependencies = []\n" + table, encoding="utf-8")
    assert drift.main(["--pyproject", str(pyproject)]) == 2
    out = capsys.readouterr().out
    assert "No runtime drift" not in out
    assert "missing" in out


def test_incomplete_baseline_does_not_notify(monkeypatch, tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\ndependencies = []\n", encoding="utf-8")
    sent = []
    monkeypatch.setattr(drift, "notify", lambda text: sent.append(text))
    assert drift.main(["--notify", "--pyproject", str(pyproject)]) == 2
    assert sent == []
