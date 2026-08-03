# Deploy credential

How the deployment box pulls this repository, and why the credential is
deliberately out of the agent's reach.

## The threat this is shaped by

The agent reads untrusted input — inbound WhatsApp, email, Telegram — and runs
as the `hermes` service user with an ungated `terminal` and `write_file`. Any
credential that user can read is a credential a prompt injection can use. The
deploy path therefore does not merely *avoid* giving the agent a token; the
filesystem has to refuse it.

Two independent limits, so neither is load-bearing alone:

1. **Root-owned key.** `hermes` cannot read the private key, so it cannot
   authenticate as the deployment at all.
2. **Read-only on GitHub's side.** Even with the key in hand, the worst it
   could do is read. The deploy key is registered `read_only: true`, which
   GitHub enforces regardless of what the box believes.

## Layout on the box

```
/root/.ssh/hermes_deploy        600 root:root   ed25519 private key
/root/.ssh/hermes_deploy.pub    644 root:root
/root/.ssh/config               600 root:root   host alias + IdentitiesOnly
/root/.ssh/known_hosts          600 root:root   github.com host key, pinned
```

```
# /root/.ssh/config
Host github-hermes-deploy
    HostName github.com
    User git
    IdentityFile /root/.ssh/hermes_deploy
    IdentitiesOnly yes
```

`IdentitiesOnly yes` stops ssh from offering any other key that happens to be
in the agent — the deployment authenticates as itself or not at all.

The checkout's remote is fetch-only; the push URL is deliberately invalid so a
careless `git push` from the box fails locally rather than at GitHub:

```bash
git remote set-url origin git@github-hermes-deploy:<owner>/<repo>.git
git remote set-url --push origin DISABLED-deploy-is-read-only
```

## Setup

```bash
# on the box, as root
install -d -m 700 /root/.ssh
ssh-keygen -t ed25519 -N '' -C '<host> deploy (read-only)' -f /root/.ssh/hermes_deploy
ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts   # pin, don't TOFU
cat /root/.ssh/hermes_deploy.pub
```

Register the public key on the repository as a **read-only** deploy key
(Settings → Deploy keys, or the API — leave "Allow write access" unchecked):

```bash
curl -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/<owner>/<repo>/keys \
  -d '{"title":"<host> deploy (read-only)","key":"<contents of .pub>","read_only":true}'
```

A deploy key is scoped to a single repository. That is the point: unlike a
personal access token, it cannot reach any other repo even if it leaks.

### A second key for the state store

Because of that scoping, the private deployment-state repo needs its own key
rather than a reuse of this one. Same recipe, different filename and alias:

```
/root/.ssh/hermes_state_deploy   600 root:root
Host github-hermes-state         -> /opt/data/hermes-deploy-state
```

Also read-only, for a different reason: a box that could push would be able to
rewrite the record of what it is supposed to look like, which is the one thing
drift detection depends on. See `docs/deployment/deployment-path.md`.

## Verifying it (all five must hold)

```bash
# 1. root can fetch
git -C /opt/data/hermes-agent fetch origin develop            # rc 0

# 2. the key cannot write, enforced by GitHub, not by us
git push --dry-run git@github-hermes-deploy:<owner>/<repo>.git HEAD:refs/heads/probe
# ERROR: The key you are authenticating with has been marked as read only.

# 3. hermes cannot read the key
sudo -u hermes test -r /root/.ssh/hermes_deploy                # rc non-zero

# 4. hermes cannot authenticate (host key pre-accepted, so this isolates auth)
sudo -u hermes ssh -o BatchMode=yes -i /root/.ssh/hermes_deploy -T git@github.com
# git@github.com: Permission denied (publickey).

# 5. hermes cannot fetch
sudo -u hermes git -C /opt/data/hermes-agent fetch origin develop   # rc 128
```

Check 4 matters: without pre-accepting the host key, the attempt fails with
*Host key verification failed* — a failure for the wrong reason, which proves
nothing about access.

## Consequences

- **Deploys run as root.** The service user cannot pull, by design. That is
  also why `.git/config`, `HEAD` and `index` in the checkout are root-owned.
- **This is required before the repository goes private.** A public repo
  fetches anonymously, so the credential is unnecessary until the moment
  visibility changes — at which point every deploy fetch starts failing.
  Install and verify it *first*; the flip is then a non-event.
- **Rotation** is a new keypair plus a new deploy key entry; delete the old
  entry from the repo's Deploy keys. Nothing else references it.
