# Runtime drift: the interpreter nobody patches

**Applies to:** long-lived deployments (a VPS/ECS box running the gateway as a
systemd service), not laptop installs.

## The gap

A running deployment is three layers, and until now only two of them had
anything watching for staleness:

| Layer | Watched by | Drifts silently? |
|---|---|---|
| Source code | `git status` vs `origin` | no |
| Python packages | exact pins in `pyproject.toml` | no |
| **The interpreter + its bundled OpenSSL** | **nothing** | **yes** |

`scripts/install.sh` builds the venv on a **uv-managed CPython** under
`UV_PYTHON_INSTALL_DIR` (`/usr/local/share/uv/python` for root installs).
That interpreter **statically bundles its own OpenSSL**. Measured on the
`hermes-systest` box:

```
python ssl:  OpenSSL 3.5.7   (9 Jun 2026)   <- what Hermes actually uses
system:      OpenSSL 3.0.13  (30 Jan 2024)  <- what apt patches
```

`unattended-upgrades` only manages `.deb` packages. When the next OpenSSL CVE
lands, the sequence is:

1. Ubuntu ships a fix.
2. Auto-update installs it overnight and logs success.
3. `apt list --upgradable` is empty; every dashboard reports "patched".
4. **Hermes is still vulnerable** — every TLS connection it makes (messaging
   platforms, model providers, MCP servers) goes through the bundled copy
   that was never touched.

The hazard is the false confidence, not the CVE. A box that is behind while
reporting itself current is one nobody thinks to check.

The same applies to interpreter CVEs themselves: a uv-managed CPython is
upgraded by `uv python install`, never by `apt`.

## The baseline

`pyproject.toml` declares what a deployment is expected to be running:

```toml
[tool.hermes.runtime-baseline]
python = "3.11.15"
openssl = "3.5.7"
```

This is deliberately a different question from `requires-python`, which says
which interpreters the *project supports*. This says which one a *deployment
is actually on*, so an interpreter upgrade becomes a reviewable commit rather
than an undocumented act on one machine.

Both values are **floors**. Below them is drift. Above them is reported as a
note, so the pin gets bumped and keeps describing reality.

## The check

```bash
/opt/data/hermes-agent/.venv/bin/python scripts/check_runtime_drift.py
```

Run it with the deployment's interpreter — it reports on the Python executing
it, not on any Python it can find. Exit codes: `0` clean, `1` drift, `2` the
baseline could not be read.

Flags: `--json` for machine-readable findings, `--notify` to send a Telegram
message **only when drift is found** (silent otherwise, so the absence of a
message is meaningful).

`--notify` delivers to `TELEGRAM_HOME_CHANNEL` (falling back to the first
entry of `TELEGRAM_ALLOWED_USERS`), in the thread named by
`TELEGRAM_CRON_THREAD_ID` or `TELEGRAM_HOME_CHANNEL_THREAD_ID`. With no
credentials in the environment it prints a line and exits normally — the
report is already in the journal by then, so a broken notifier never hides a
finding.

### Weekly timer

Installed on the box, not in the repo, because it is deployment-specific:

```ini
# /etc/systemd/system/hermes-drift-check.service
[Unit]
Description=Hermes runtime drift check
After=network-online.target

[Service]
Type=oneshot
User=hermes
WorkingDirectory=/opt/data/hermes-agent
EnvironmentFile=/opt/data/hermes-home-staging/.env
ExecStart=/opt/data/hermes-agent/.venv/bin/python \
    /opt/data/hermes-agent/scripts/check_runtime_drift.py --notify
# Drift is reported via --notify; a non-zero exit is the signal, not a fault.
SuccessExitStatus=0 1
```

```ini
# /etc/systemd/system/hermes-drift-check.timer
[Unit]
Description=Weekly Hermes runtime drift check

[Timer]
OnCalendar=Mon 09:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload && systemctl enable --now hermes-drift-check.timer
systemctl start hermes-drift-check.service   # run once now
```

`Persistent=true` matters: if the box is down at the scheduled time, the run
happens at next boot instead of being skipped.

## Upgrade path when the check fires

### OpenSSL or interpreter below the floor

Rebuild the venv on a patched interpreter. The packages are exact-pinned, so
this changes the interpreter and nothing else:

```bash
# as root
uv python install 3.11            # fetches the newest 3.11.x patch release
cd /opt/data/hermes-agent
sudo -u hermes .venv/bin/python -c "import ssl; print(ssl.OPENSSL_VERSION)"  # before

systemctl stop 'hermes-*'
sudo -u hermes uv venv .venv --python 3.11 --clear
sudo -u hermes uv sync --frozen     # --frozen: install uv.lock exactly, no re-resolve
systemctl start 'hermes-*'

sudo -u hermes .venv/bin/python -c "import ssl; print(ssl.OPENSSL_VERSION)"  # after
sudo -u hermes .venv/bin/python scripts/check_runtime_drift.py
```

Then bump `[tool.hermes.runtime-baseline]` in the repo to the new versions and
open a PR — otherwise the next check reports the deployment as ahead of the
baseline, and a later downgrade would go unnoticed.

Expect ~2 minutes of downtime for the restart. Verify afterwards:

```bash
systemctl list-units 'hermes-*' --no-pager   # all active, NRestarts=0
```

### A package below its pin

Something installed outside `uv sync`. Restore it:

```bash
cd /opt/data/hermes-agent && sudo -u hermes uv sync --frozen
```

### Raising the floor after a CVE

Bump `openssl` in `[tool.hermes.runtime-baseline]` and merge. Every deployment
then fails the check until it is rebuilt — that is the intended behaviour: the
repo becomes the place a security floor is declared, and the timer is what
notices a box that has not caught up.

## Related

- `docs/rca-ssl-cacert-post-git-pull.md` — CA bundle breakage after a partial
  venv refresh. Different failure, same layer.
