# ai-prentice-4-all — Security Review

| | |
|---|---|
| Commit | `6840c13d6` (branch `develop`, commit date 2026-10-04) |
| Review date | 2026-10-04 |
| Method | Static review split across 14 areas and run in parallel, then consolidated: duplicates merged, every Critical/High finding (and any doubtful Medium) re-checked against the checked-out code, and line references corrected |
| Scope | Gateway and platforms, dashboard (`hermes_cli/web_server.py`, `dashboard_auth`), agent-home (Next.js BFF), app-mcp, `custom/` triage services, WhatsApp bridge, skills/plugins, memory, desktop (Electron), cron, proxy, ACP |
| Out of scope | Live production hosts, runtime config, and dependency CVE scanning. Nothing was contacted or changed. |

Companion report: [`2026-10-04-performance-review.md`](2026-10-04-performance-review.md).

Where a finding depends on how something is deployed (open ports, which non-owner accounts exist), the report says so. Code claims refer to the commit above.

## Executive summary

The most serious risk is **the boundary between the agent's own Unix user (`hermes`) and root/production secrets**. The agent reads untrusted email, WhatsApp, Telegram and web content and has an ungated terminal running as `hermes`. Several paths turn a successful prompt injection, or any code-execution bug running as `hermes`, into root or full credential access:
- root deploys and root timers execute code from the `hermes`-owned checkout (F13, F15);
- a documented sudoers rule grants root through a `hermes`-writable script (F14);
- every production secret can be read from the agent's shell (F16).

The second theme is **authorisation inside a multi-user deployment**:
- the dashboard API checks only that a caller is signed in, not their role (F2);
- session history is not scoped by principal (F20);
- the app-mcp hub attaches to whichever user connected last (F4).

The third theme is **the agent getting around its own approval gates**:
- clicking Approve in the UI (F17);
- trusting a Folder Bridge folder for itself (F18);
- calling the unauthenticated loopback MCP endpoint (F19);
- bypassing the destructive-action guard (F4).

Finally, some exposed services need confirming on the box. The custom email/WhatsApp MCP servers bind `0.0.0.0` with no auth and `Access-Control-Allow-Origin: *` (F1).

**Top 5 actions:**
1. Make the checkout root-owned. Run deploy/backup code from root-owned, reviewed copies. Remove sudo rules that point into the tree (F13–F15).
2. Move secrets out of reach of the `hermes` uid and strip `DATABASE_URL`/secrets from the agent's terminal environment (F16).
3. Enforce roles on operator-grade dashboard/BFF routes, and scope session search by principal (F2, F20).
4. Mark approval/trust controls as agent-unclickable, authenticate the loopback MCP endpoint, and fix the guard (F4, F17–F19).
5. Bind the custom MCP/callback servers to loopback and require a token (F1).

F1–F12 come from the first consolidation pass. F13–F25 were added after a second verification pass: each finding was re-checked against the code by a dedicated reviewer. The unverified raw output of all 14 area reviewers (119 findings) is in [`2026-10-04-security-raw-findings.md`](2026-10-04-security-raw-findings.md). Several of its Medium/Low items are not repeated here.

## Summary (by severity)

| # | Severity | Finding | Area |
|---|---|---|---|
| F1 | **High** (Critical if the ports are reachable) | Custom email/WhatsApp MCP servers and the Telegram callback server listen on `0.0.0.0` with no authentication and `Access-Control-Allow-Origin: *` | custom/ |
| F2 | **High** | The dashboard's gated mode only checks that you are signed in, not your role. Any signed-in member or viewer can write env/config, add stdio MCP servers (host RCE), reveal secrets, and trigger updates | dashboard, agent-home |
| F3 | **High** | Stored XSS on the agent-home origin: the file proxy serves upstream bytes inline with the content type the sender supplied, and there is no `nosniff` or CSP. Inbound files from unknown senders are filed under the owner | agent-home, gateway |
| F4 | **High** | The app-mcp destructive-action guard can be bypassed (it checks one element, the browser acts on another). The hub also attaches whichever user connected last, regardless of identity. Snapshots expose input values | app-mcp, agent-home |
| F13 | **High** | Root deploy executes code from a hermes-owned checkout (.git config/hooks, scripts/ module shadowing, setup.py, npm), so the agent user can become root | deploy / tools |
| F14 | **High** | Documented sudoers rule for wa-bridge-add.sh grants root to anyone who can edit the hermes-owned checkout | deploy |
| F15 | **High** | Root jobs (secret backup timer, deploy handover step) run hermes-writable Python from the checkout | deploy |
| F16 | **High** | Agent shell (uid hermes) can read every production secret: the privileged Postgres DSN, the agent-home session key, OAuth/WhatsApp credentials | deploy / agent tools |
| F17 | **High** | Agent can self-approve its own gated actions by clicking Approve / Always approve through ungated app_act | app-mcp, agent-home |
| F18 | **High** | Agent can tick Folder Bridge "Trusted for import" through app-mcp, then import files without approval | app-mcp, agent-home |
| F19 | **High** | Unauthenticated loopback MCP endpoint lets the agent's own terminal bypass approvals.tools for destructive UI actions and local-file reads | app-mcp |
| F20 | **High** | Session history is not scoped by principal: members can search and read other users' transcripts | agent core / session DB |
| F5 | Medium | Contacts auto-merge whenever a known phone/email appears in the body of a message from an unknown sender | custom/shared |
| F6 | Medium | Facts extracted from third-party messages are written into the profile-wide shared `MEMORY.md`, which goes into every principal's system prompt (persistent prompt injection) | custom/shared, memory |
| F7 | Medium | Gateway `/yolo` has no role check, so any authorised sender can switch off dangerous-command approval for their session | gateway |
| F8 | Medium | WhatsApp bridge runs `ffmpeg` through a shell with `JSON.stringify` quoting, which still expands `$()`. `/send-media` sends any local file to any chat | WhatsApp bridge |
| F9 | Medium | Git option injection: the dashboard's `base` parameter reaches `git diff` before `--` (e.g. `--output=<path>` writes a file) | dashboard |
| F10 | Medium | The skill scanner can be told to skip files via `.skillignore`. Agent-created skills are not scanned by default, and the write gate fails open | skills |
| F11 | Medium | Desktop: third-party embed scripts run in the privileged renderer (which can reach `hermesDesktop.terminal`), and on WSL `openExternal` goes through `cmd.exe start` with the raw URL | desktop |
| F21 | Medium | Default non-strict media delivery attaches any non-denylisted host file to chat replies | gateway |
| F22 | Medium | Gateway approvals are not bound to the requester: any authorised co-participant in a shared session can approve another user's pending dangerous command, and plain-text 'always' skips slash-command gating | gateway |
| F23 | Medium | Gateway expands @file:/@folder:/@diff/@url in untrusted chat text before the model runs | gateway / agent core |
| F24 | Medium | Caddy admin API on localhost:2019 is unauthenticated, so any local user can rewrite the public TLS edge | infra |
| F25 | Medium | tirith auto-install fetches an unpinned 'releases/latest' binary from a third-party repo and runs it as the gateway user | tools / supply chain |
| F12 | Low | Group of hardening items: fail-open/defaults (tirith, oneshot YOLO, host check on `0.0.0.0`, unlimited uploads), Telegram HTML injection, image plugin reading local files / SSRF, stateless sessions, memory visibility not checked server-side | various |

---

## Findings

### F1 — Unauthenticated custom MCP / callback HTTP servers on all interfaces — High

**Evidence**
- `custom/email/email_mcp_server.py:582-603`: `GET /call?tool=<name>&...` runs any entry in `TOOLS` with no authentication. The tools include `email_search`, `email_get_thread`, `email_resolve_escalation`, `contact_merge`, `contact_split`, `contact_resolve_merge` (lines 492-573). `do_POST /call` (605+) behaves the same way.
- `custom/email/email_mcp_server.py:686` sends `Access-Control-Allow-Origin: *`; `:697` binds `HTTPServer(('0.0.0.0', PORT), ...)`.
- `custom/whatsapp/mcp_server.py:351-385, 463, 474`: the same pattern (`whatsapp_search_messages`, `whatsapp_get_recent`, ...).
- `custom/shared/telegram_callback_handler.py:221-242, 268`: `GET /action/{merge|reject}/{id}` changes the contact DB with no authentication or signature, bound to `0.0.0.0`. `custom/shared/contact_manager.py:25` defaults the callback base to plain-HTTP `http://8.217.86.90:7902`.

**Impact**
- Anyone who can reach these ports can read mailbox and WhatsApp content and change contact merges or escalations.
- Because of the wildcard CORS header, any web page opened by someone on a network that can reach the host can read the results.
- Callback IDs are sent over plaintext HTTP and can be replayed.

**Fix**
- Bind to `127.0.0.1`.
- Require a bearer token (or a Unix socket).
- Remove the wildcard CORS header.
- Remove the GET-based `/call`.
- Sign callback URLs (HMAC plus expiry) and serve them over HTTPS.

### F2 — Dashboard authorisation equals authentication (no role checks) — High

**Evidence**
- `hermes_cli/web_server.py:423-446`: `_require_token` defers entirely to the gate when `auth_required`. Any request that carries a session passes.
- `hermes_cli/dashboard_auth/middleware.py:259-302` verifies the session but never looks at the role. Per lines 158 and 278, the portal auto-approves any org member.
- Roles exist (`hermes_cli/members.py:94` — `admin`, `member`, `viewer`), but the only role checks are `require_member_admin`/`require_owner` (`members.py:132-160`), and those are used only for member management.
- Endpoints with no role check:
  - `PUT /api/env` (`web_server.py:6961-6975`; no `_require_token` either)
  - `POST /api/env/reveal` (7100-7112)
  - `POST /api/mcp/servers` (12156-12172; accepts a `command`, i.e. a stdio server, which means arbitrary process execution)
  - `PUT /api/config/raw` (15618-15626)
  - `POST /api/hermes/update` (4999)
  - the `/api/files*` routes (1688-1882), whose root is the whole home directory unless the install lives at `/opt/data` or `HERMES_DASHBOARD_FILES_ROOT` is set (1580-1596)
- agent-home: `src/app/api/models/provider-key/route.ts:70-125` lets any authenticated principal write or delete arbitrary env vars upstream, including an unrestricted `extra_env` map. The only check is a name regex.

**Impact**
- Any enrolled non-owner (member or viewer) can execute commands on the host, read every secret, and rewrite config.
- How exposed this is depends on whether non-owner accounts are enrolled in production. That was not checked.

**Fix**
- Add one role dependency (owner/admin) to every mutating or secret-revealing route, plus the files/terminal/MCP/config/update routes.
- Default-deny new routes.
- Add the same check to the agent-home BFF routes.

### F3 — Stored XSS through agent-home file proxy — High

**Evidence**
- `agent-home/src/lib/http/proxy-bytes.ts:27-34, 53-66`: forwards the upstream `content-type` and defaults to `inline` disposition (75-79). It sets no `X-Content-Type-Options: nosniff`, `Content-Security-Policy`, or sandbox. `redirect: "follow"` (50).
- `agent-home/src/app/api/files/[id]/content/route.ts:20-36` serves these bytes on the app origin.
- `gateway/inbound_files.py:52-56` takes the sender's declared MIME type as given. Lines 161-173 attribute attachments from **unenrolled** senders to the deployment owner.
- The registry stores `content_type` (`hermes_cli/file_registry.py:88, 261`).
- No CSP anywhere: `agent-home/next.config.mjs:45-65` sets headers only for `/activate` and `/survey`, and `deploy/Caddyfile.agent-home` adds no security headers.

**Impact**
1. An outside party sends an `.html` or `.svg` document over a messaging channel.
2. It is filed under the owner.
3. When the owner opens it in agent-home, the script runs same-origin with the owner's session.
4. From there it can call `/api/models/provider-key` (F2), mint `/api/app-mcp/ticket`, and read files.

Confidence: medium-high. It relies on Storage returning the stored content type, which is the normal behaviour.

**Fix**
- Always send `nosniff` and `Content-Security-Policy: sandbox; default-src 'none'`.
- Force `attachment` except for an allowlist of image/video/PDF types.
- Ignore sender-declared MIME types and sniff server-side instead.
- Consider serving user content from a separate origin.

### F4 — app-mcp guard bypass, hub hijack, sensitive snapshots — High

**Evidence**
- **Guard bypass.** `app-mcp/app_mcp/server.py:121-140`: `_element_name` returns the caller-supplied `name` before it looks up `element_id`. It also returns `None` when `selector` doesn't exactly match a snapshot entry, and `looks_destructive(None)` is False (`guard.py:37-41`). The browser, however, resolves `elementId` first, then any CSS `selector` (`agent-home/src/lib/app-mcp/actions.ts:46-58`). So `app_act(click, element_id=<Delete>, name="Open")`, or a non-canonical selector, gets past the `app_act_destructive` approval path (`server.py:176-185`).
- **Hub hijack.** `app-mcp/app_mcp/hub.py:49-54`: the newest connection wins, for any user. `server.py:92-97` checks only that the ticket is valid. Any authenticated principal can mint a ticket (`ticket/route.ts:34-49`), so the agent's UI commands go to whichever user's browser connected last.
- **Snapshot leaks.** `agent-home/src/lib/app-mcp/snapshot.ts:174-177` includes `value` for every `INPUT`, password fields included.

**Impact**
- Prompt-injected content can trigger send, delete or publish actions with no approval.
- Commands can run in another user's session.
- Credentials can leak into model context.

**Fix**
- Resolve the target on the server from `element_id` alone and refuse a `name`/`selector` that doesn't match.
- Run the destructive check inside the browser, against the element that is actually resolved.
- Bind the hub to the agent's principal and reject a different user.
- Drop values for `password`/`autocomplete=*-password` fields and other sensitive inputs.

### F5 — Contact auto-merge triggered by message content — Medium

**Evidence**
- `custom/shared/contact_manager.py:296-311`: if any existing contact's phone (8+ digits) or email appears in the **body** of a message from a new handle, confidence is set to `high`.
- Lines 328-332 then call `auto_merge` without confirmation.
- `custom/email/email_triage_agent.py:290-295` passes the raw email body.

**Impact**
- An attacker who writes "contact me / cc owner@…" gets their handle merged into an existing (possibly trusted) contact.
- That pollutes identity, history and attribution for later triage and escalations.

**Fix**
- Treat content cross-references as suggestions only (medium confidence).
- Never auto-merge on attacker-controlled text.

### F6 — Third-party content written into shared memory — Medium

**Evidence**
- `custom/shared/triage_handlers.py:40-56` forwards LLM-extracted `memory_facts` from inbound WhatsApp/email to `remember_facts`.
- `custom/shared/memory_bridge.py:63-75, 121-203` appends them to `$HERMES_HOME/memories/MEMORY.md`, the profile-wide file that `tools/memory_tool.py:668-669` serves as `shared` to every principal.
- Partly mitigated: snapshot entries go through the threat-pattern sanitiser (`memory_tool.py:527`), so it was downgraded from High.

**Impact**
- Instructions planted by any external sender persist across sessions and users.

**Fix**
- Write bridged facts to a separate, clearly labelled "untrusted" tier that is quoted as data rather than instructions.
- Or require owner approval before facts enter shared memory.

### F7 — `/yolo` toggle has no authorisation — Medium

**Evidence**
- `gateway/slash_commands.py:2895-2910` enables or disables session YOLO for the caller's session key with no owner/admin or config check.
- Dispatch is at `gateway/run.py:9474-9475`.

**Impact**
- Any sender who is allowed to talk to the gateway (including non-owner members) can switch off dangerous-command approvals for their own session.

**Fix**
- Restrict to owner/admin.
- Add a config flag (default off) for non-owners.

### F8 — WhatsApp bridge shell injection and arbitrary file send — Medium

**Evidence**
- `scripts/whatsapp-bridge/bridge.js:737-740`: ``execSync(`ffmpeg -y -i ${JSON.stringify(filePath)} ...`)``. Inside double quotes, `JSON.stringify` does not neutralise `$()` or backticks.
- `/send-media` (699-714) reads any `filePath` on disk and sends it to any `chatId`. Its only protection is the loopback bind plus the Host check (580-603, 833).
- WhatsApp inbound document names are sanitised (507), but other channels keep the original name (`gateway/platforms/base.py:1556-1560` strips only NULs and directories). That is why this is Medium rather than High.

**Fix**
- Use `execFileSync('ffmpeg', [...])`.
- Restrict `filePath` to the media cache directories.
- Add a shared-secret header between the gateway and the bridge.

### F9 — Git argument injection through `base` — Medium

**Evidence**
- `hermes_cli/web_server.py:2135-2144` passes the `base` query parameter to `hermes_cli/web_git.py`, which places it before `--`: `review_diff` (`["diff", base_ref, "--", file]`, 315) and `review_list` (264-270).
- A value such as `--output=/home/u/.bashrc` is parsed as an option, which means writing an arbitrary file. Reachable by any dashboard session (see F2).

**Fix**
- Reject values starting with `-`.
- Resolve them with `git rev-parse --verify --end-of-options <ref>^{commit}`.

### F10 — Skill security-scan bypasses — Medium

**Evidence**
- `tools/skills_guard.py:655-668, 820-826`: files matched by the skill's own `.skillignore` are skipped by both the structural and pattern scans. Only `SKILL.md` is protected (`_NEVER_IGNORABLE`, 963/994).
- Agent-created skills are not scanned unless `skills.guard_agent_created` is set (`tools/skill_manager_tool.py:102-129`).
- The write gate fails open if its import fails (1252-1255).

**Fix**
- Ignore `.skillignore` for skills from the hub or other untrusted sources (or scan everything and use the ignore list only to suppress warnings).
- Make the write gate fail closed.

### F11 — Desktop renderer trust boundary — Medium

**Evidence**
- `apps/desktop/src/components/assistant-ui/embeds/social-embed.tsx:21-25, 51-71` injects Instagram, TikTok and Twitter scripts into the main renderer document.
- That document exposes `window.hermesDesktop` (`electron/preload.cjs:3`), including `api`, `readFileText`, and `terminal.start/write` (48-54, 114-118 → `main.cjs:7080-7095`, which spawns a PTY). `contextIsolation`/`sandbox` (`main.cjs:4442-4444`) do not protect against script that legitimately runs in that page.
- On WSL, `main.cjs:986-992` runs `cmd.exe /c start "" <url>`. `&` survives URL normalisation and is a cmd metacharacter.

**Fix**
- Render embeds in a sandboxed cross-origin iframe or `<webview>` without the preload.
- Add a renderer CSP.
- On WSL, use `rundll32 url.dll,FileProtocolHandler` or escape for cmd.

### F12 — Lower-severity hardening — Low
- **tirith fails open by default** (`tools/tirith_security.py:70-87`). Cosign is optional, and execution failures fall back to SHA-256 only (440-451, auto-installed from `gateway/run.py:2809-2814`).
- **`hermes_cli/oneshot.py:171-172` sets `HERMES_YOLO_MODE`/`HERMES_ACCEPT_HOOKS` in the process environment**, so the setting is inherited by child processes.
- **The dashboard Host check is disabled when binding to `0.0.0.0`** (`web_server.py:515-519`). The OAuth gate is then the only control.
- **Chat upload size is unlimited by default** (`agent-home/src/lib/chat/upload-limit.ts`).
- **Telegram HTML injection.** Escalation messages are sent with `parse_mode='HTML'` without escaping (`custom/shared/escalation_pusher_v2.py:57-66, 218`), which allows link spoofing.
- **The OpenAI image plugin reads arbitrary local paths and fetches arbitrary URLs** (`plugins/image_gen/openai/__init__.py:126-153`). That enables SSRF and sending local files to the provider.
- **Sessions are stateless and cannot be revoked server-side.** The agent-home cookie is a 12 h, HMAC-signed blob that contains the upstream token (`src/lib/auth/session.ts:31-32, 64-111`).
- **Memory `visibility` is not limited to the declared enum on the server.** `plugins/memory/supabase_pgvector/__init__.py:199-203` declares the enum, but line 551 passes the raw value to `_resolve_visibility`, which accepts any `private:<user>` (`store.py:324-334`, `hermes_cli/access.py:167-177`). Only `SELECT` RLS policies were found (`access.py:660-666`), so I could not confirm whether a cross-user insert succeeds.

---

## Findings added in the second verification pass

### F13 — Root deploy executes code from a hermes-owned checkout (.git config/hooks, scripts/ module shadowing, setup.py, npm), so the agent user can become root — High

_Verification: confirmed._

**Evidence**
- `deploy/hermes-deploy.sh:151` runs `find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes` on every deploy. Only `.venv` is excluded (`:152`), so `$REPO/.git` (including `config` and `hooks/`), `scripts/`, `deploy/`, `setup.py`, `package.json`, `web/` and `agent-home/` all end up owned by `hermes`. The cold-rebuild doc says the same (`docs/deployment/deployment-path.md:150-154`).
- `docs/deployment/PRODUCTION.md:118` says the script needs root. The operator runs it as root over SSH or on the box (`:126-145`). The script was taken out of the sudoers grant because `pip install -e .` runs build hooks from the hermes-owned tree (`.agents/skills/testing-hermes-systest-box/SKILL.md:316-323`), but running it as root by hand leaves that hole open.
- Root then runs, inside that tree: `git status` (`:34`), `git fetch` (`:61`), `git checkout`/`reset`/`merge`/`update-ref` (`:73-76`), `git diff`/`status` (`:94`, `:99`, `:120`, `:137`), `./.venv/bin/pip install -q -e .` (`:112`; executes the repo's `setup.py`), `npm ci` plus `npm run build` (`:122-127`, `:139-145`), and `./.venv/bin/python scripts/deploy_state.py handover` (`:179`).
- Git: the repo is owned by another user, so root's git only works because `safe.directory` is set (`SKILL.md:75`, `:210-211`). With that set, git fully honours the hermes-writable `.git/config` and `.git/hooks`: `core.fsmonitor` runs on `git status` at `:34`, `core.sshCommand` runs on the fetch at `:61`, and `post-checkout`/`post-merge`/`reference-transaction` hooks run at `:73-76`. None of these are tracked files. The guard at `:34` uses `--untracked-files=no`, and `:99-103` only prints untracked files without blocking.
- Python: `scripts/deploy_state.py:60-73` imports `argparse`, `json`, `subprocess`, `yaml` and others before `REPO_ROOT` is inserted (`:75-77`). Python puts the script's directory (`$REPO/scripts`, hermes-writable) at `sys.path[0]`, so an untracked `scripts/yaml.py` or `scripts/argparse.py` runs as root at `:179`. Tracked files are not safe either: `git checkout` replaces them with root-owned files, but `hermes` owns the parent directories and can unlink and replace them during the minutes-long npm build that runs between `:74` and `:179`.
- npm: an untracked project `.npmrc` is not in `.gitignore` and is never removed. Settings such as `node-options=--require …` are then applied to root's `npm ci`/`npm run build`. The box runs npm 10.9 (`docs/deployment/hetzner-migration-runbook.md:60`), which, as far as I know, no longer drops privileges for lifecycle scripts when run as root (not verified).
- Agent reachability: the agent runs as `hermes` with an ungated `terminal` and `write_file` (`docs/deployment/deploy-credential.md:8-9`). `write_file` checks only `check_core_write` (`tools/file_operations.py:1359`) and `_is_write_denied` (`:1364`). `core_manifest.yaml` does not list `.git/`, `scripts/`, `deploy/`, `setup.py` or the repo root. `agent/file_safety.py:98-151` protects only credential paths, plus `HERMES_WRITE_SAFE_ROOT` when that is set. `agent/core_boundary.py:33-40` says the raw terminal is not covered at all.
- A related root unit that needs no deploy: `hermes-secret-backup.service` is documented as running as root and executing `/opt/data/hermes-agent/scripts/backup_secrets.py` daily from the checkout, with the same pre-`REPO_ROOT` imports (`docs/deployment/deployment-path.md:299-307`, `scripts/backup_secrets.py:48-62`). `PRODUCTION.md:80` lists it as installed but "not active" on this box, so this path depends on its timer state.
- No mitigation found: there is no systemd confinement for the root deploy, no `-c core.hooksPath`/`core.fsmonitor` override, no `python -I`, and no `--ignore-scripts`/`--userconfig`. The `ProtectSystem=strict` units also list `/opt/data/hermes-agent` in `ReadWritePaths` (e.g. `deploy/hermes-rag-ingest.service:41`).

**Impact**
- Anyone who gets a single command or file write into the agent (prompt injection through inbound WhatsApp, email, Telegram, a web page or a skill, i.e. the threat model in `deploy-credential.md`) can plant, for example, `git config core.fsmonitor '<cmd>'` in `$REPO/.git/config`. It runs as root on the operator's next routine `deploy-hermes.sh`, with no race and no approval prompt.
- Root on the single host gives access to the Supabase DB and its `.env`, `/root/.ssh` deploy keys, the Docker socket, every principal's data and credentials, and persistence that survives redeploys. This defeats the documented goal that a compromised `hermes` cannot reach root (`SKILL.md:308-310`).
- Preconditions: an agent compromise (an ungated terminal makes this realistic) plus one operator deploy, which happens on every merge to `develop`. Rated High rather than Critical only because it is the second stage of a chain.

**Fix**
- Make the deployed tree root-owned and not writable by `hermes`. Remove the blanket chown at `:151` and grant `hermes` only explicit runtime paths (`agent-home/.next`, caches), ideally through `ReadWritePaths` instead of ownership.
- Better: build as an unprivileged `builder` user (not `hermes`) in a fresh directory, then have root atomically switch a root-owned release symlink. Root should never execute anything from a tree `hermes` can write.
- Short-term hardening in the script:
  - Run git as `git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.sshCommand='ssh -F /root/.ssh/config'`, or keep a root-owned `--git-dir` outside the tree.
  - Run Python as `./.venv/bin/python -I -P /opt/data/<root-owned copy>/deploy_state.py`.
  - Run npm as `npm ci --ignore-scripts --userconfig=/root/.npmrc` and refuse to run if a project `.npmrc` exists.
  - Abort, rather than just report, on untracked non-ignored files at `:99-103`.
- Apply the same treatment to every root-run unit that executes checkout code (`hermes-secret-backup.service`): use a root-owned copy and `python -I`.
- Defense in depth: add `.git/**`, `scripts/**`, `deploy/**`, `setup.py`, `pyproject.toml`, `package*.json` and `.npmrc` to `core_manifest.yaml` (this does not cover the raw terminal).

Effort: M · Confidence: high

### F14 — Documented sudoers rule for wa-bridge-add.sh grants root to anyone who can edit the hermes-owned checkout — High

_Verification: confirmed._

**Evidence**
- `deploy/wa-bridge-add.sh:3-6`: the header tells operators to install `hermes ALL=(root) NOPASSWD: /usr/bin/bash /opt/data/hermes-agent/deploy/wa-bridge-add.sh *`. sudo then trusts a script by its path inside the checkout, and the trailing `*` wildcard accepts any arguments.
- `hermes_cli/wa_bridge_api.py:201` builds the path to the script from the running checkout (`Path(__file__)...parent.parent / "deploy" / "wa-bridge-add.sh"`). `:205-206` runs `sudo -n bash <script> <name>`. `:12-13` says this route "needs its own sudoers entry". Without the entry, the Settings "add bridge" feature (#439) fails with a 502, so an operator who wants the feature has to install the documented line.
- `deploy/hermes-deploy.sh:151`: every deploy runs `find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes`, so `deploy/` and `wa-bridge-add.sh` belong to `hermes`. Only `.venv` is returned to root (`:152`).
- The repo already treats this pattern as unsafe for the same reason in a neighbouring case. `.agents/skills/testing-hermes-systest-box/SKILL.md:312-313` says the hermes grant should be *only* `systemctl {start,stop,restart,status,is-active} hermes-*.service`. `:316-323` records that `deploy-hermes.sh` was removed from sudoers because root ran code from the hermes-owned tree. The script header contradicts this policy.
- I found no mitigation. `agent/file_safety.py:61-77` (write denylist) protects `/etc/sudoers.d` and `/etc/systemd` but not `/opt/data/hermes-agent`. The approval patterns in `tools/approval.py` do not flag `sudo -n bash <path>` or an ordinary edit to a `.sh` file. The `git status` guard in the deploy script (`hermes-deploy.sh:34`) only runs at deploy time, so an attacker can edit the script, run it, and revert it in between. The dashboard and gateway units, and the installed `/etc/sudoers.d/*`, are not in the repo, so I could not check for `NoNewPrivileges`. The dashboard already relies on `sudo -n systemctl` working (`wa_bridge_api.py:58-60`), so sudo is evidently not blocked for the service user.

**Impact**
- Precondition: the documented sudoers line is installed. That is likely wherever bridge provisioning from Settings works, but I could not verify it statically.
- Who can exploit it: any code running as `hermes`. That includes the agent's terminal and file tools after a prompt injection from inbound email or WhatsApp content, and the RCE paths in other findings (F2 stdio MCP, F8 ffmpeg shell, F1 if reachable). The attacker overwrites `deploy/wa-bridge-add.sh`, or swaps it for a symlink since `deploy/` is hermes-owned, then runs `sudo -n bash /opt/data/hermes-agent/deploy/wa-bridge-add.sh x`. That gives an immediate root shell, with no approval prompt and no owner session required.
- Consequence: the box's core goal, that a compromised agent cannot reach root, is defeated. With root the attacker can read the Supabase `.env`, the deploy key in `/root/.ssh` and all tenant data, and can set up persistence.

**Fix**
- Never use sudo on a path inside the hermes-writable checkout. At deploy time, install a reviewed copy owned by root: `install -m 0755 -o root -g root deploy/wa-bridge-add.sh /usr/local/sbin/hermes-wa-bridge-add`. Grant exactly `hermes ALL=(root) NOPASSWD: /usr/local/sbin/hermes-wa-bridge-add [a-z0-9]*`, not `bash <path> *`. Point `add_bridge()` at that fixed path, as `hermes-deploy.sh:187-192` already does for its own `SELF`/`REVIEWED` copy.
- Better: ship a root-owned templated unit `hermes-wa-bridge@.service` (with `User=hermes`, `%i` for the session dir and log, and the port from an `EnvironmentFile` written by hermes). Provisioning then needs only the existing `systemctl enable --now hermes-wa-bridge@<name>` grant, and the separate sudoers entry can go.
- Rewrite the header in `wa-bridge-add.sh:3-6` and the docstring in `wa_bridge_api.py:12-13`. Audit `/etc/sudoers.d/` on the box for any rule that references `/opt/data/hermes-agent/**`, and add that check to the SKILL.md privilege checklist.

Effort: S · Confidence: medium (the code and docs are certain; whether the sudoers line is installed on the box cannot be checked statically)

### F15 — Root jobs (secret backup timer, deploy handover step) run hermes-writable Python from the checkout — High

_Verification: confirmed._

**Evidence**
- `docs/deployment/deployment-path.md:299-306`: the documented `hermes-secret-backup.service` is labelled "runs as root, needs .env". It has no `User=` and runs `ExecStart=/opt/data/hermes-agent/.venv/bin/python /opt/data/hermes-agent/scripts/backup_secrets.py ... backup --push` every day. `docs/deployment/README.md:330` repeats that the daily backup runs as root.
- `deploy/hermes-deploy.sh:151`: `find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes` gives `hermes` ownership of everything except `.venv`, including `scripts/`, the repo root and `.git/`. Only `.venv` is kept root-owned (`:152`), because a hermes-writable venv would be "a path back to root" (`:149-150`, `deployment-path.md:150-153`). The same reasoning was never applied to the scripts that root runs.
- `scripts/backup_secrets.py:62-66`: `import yaml` runs while `sys.path[0]` is still the hermes-owned `scripts/` directory. After that, `REPO_ROOT` is put at the front of `sys.path` and `scripts.check_runtime_drift` / `scripts.deploy_state` are imported from it. So hermes does not need to edit a tracked file: an untracked `scripts/yaml.py` (or a `packaging/` directory at the repo root) is enough to run code as root.
- `deploy/hermes-deploy.sh:179`: every deploy, as root, runs `./.venv/bin/python scripts/deploy_state.py handover` after the chown. `scripts/deploy_state.py:73-77` has the same `import yaml` / `sys.path.insert` pattern. The local-modification guard (`:34`) ignores untracked files (`--untracked-files=no`), and the untracked-file scan (`:99`) only prints what it finds. This means a root code path exists on every deploy, whether or not the backup timer is on.
- Timer status is documented inconsistently. `docs/deployment/PRODUCTION.md:80` says it is "installed, not active on source". `docs/deployment/hetzner-migration-runbook.md:106` says it is "installed, all disabled". `HANDOFF.md:24-27` lists `hermes-secret-backup.service` as a pre-existing *failed* unit, so it has run. `docs/testing/uat-hermes-systest.md:151-154` expects its `.timer` to be `enabled/active`.
- Mitigations checked, none effective: the unit file is not in `deploy/`, and the documented unit has no `User=`/`ProtectSystem=` (that hardening exists only on the hermes-user units such as `deploy/hermes-memory-projection.service:39-43`). The `.venv` is root-owned, but the interpreter loads modules from hermes-owned paths. `docs/deployment/deploy-credential.md:124` says `.git/config`, `HEAD` and `index` are root-owned, but `hermes-deploy.sh:151` chowns them to hermes, so that doc does not match the script.

**Impact**
- Precondition: code execution as `hermes`. That is the agent's normal operating mode: its terminal/file tools run as `hermes` and handle untrusted WhatsApp and email (see F6/F7 for paths that weaken approval).
- Result: root within 24h once the backup timer is on, or at the next operator deploy in any case. The backup job reads every credential on the box by design, including the root-only `/opt/data/deploy/state-secrets.env`. Root also has `/root/.ssh` deploy keys and the Supabase/Docker host. This removes the separation the design relies on: a compromised box should not be able to read its own backups or rewrite its audit trail.
- Also likely, but depends on host config: git commands run as root in a hermes-owned `.git/` (`hermes-deploy.sh:34,61-76,99`) would execute `core.fsmonitor`/hooks from `.git/config`, if `safe.directory` allows the checkout system-wide (the deploy could not work otherwise; this is not visible in the repo).

**Fix**
- Never run root jobs from the hermes-owned tree. At deploy time, copy `scripts/backup_secrets.py`, `scripts/deploy_state.py` and `scripts/check_runtime_drift.py` into a root-owned location such as `install -d -o root -g root -m 755 /usr/local/lib/hermes-ops`, and point `ExecStart` and `hermes-deploy.sh:179` at that location. Run them with `python -I` (isolated mode: no script dir or cwd on `sys.path`, `PYTHON*` env ignored) and remove the `sys.path.insert(0, REPO_ROOT)` pattern.
- Better: run `backup` as a dedicated `hermes-backup` user that has read-only group access to exactly the manifest files, plus `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/opt/data/hermes-deploy-backups`. Commit the unit to `deploy/` so `deploy_state.py check` covers it.
- Keep `.git/` root-owned (`find ... -path "$REPO/.git" -prune`), and give root git calls an explicit `-c safe.directory=$REPO -c core.fsmonitor= -c core.hooksPath=/dev/null`. Alternatively, have the deploy fail when `git status --untracked-files=normal` reports `.py` files or packages.

Effort: S · Confidence: high (code path and ownership); medium (current timer/safe.directory state on the box)

### F16 — Agent shell (uid hermes) can read every production secret: the privileged Postgres DSN, the agent-home session key, OAuth/WhatsApp credentials — High

_Verification: partially-confirmed._

**Evidence**
- `docs/deployment/deploy-credential.md:8-12`: the docs state the threat model themselves. The agent reads untrusted WhatsApp/email/Telegram input and runs as `hermes` "with an ungated `terminal` and `write_file`", so "any credential that user can read is a credential a prompt injection can use". That rule is applied to the deploy key only.
- `cli-config.yaml.example:193-194` / `hermes_cli/config.py:1149`: the default terminal backend is `local`, so commands run on the host as the gateway uid with no sandbox.
- `deploy/hermes-deploy.sh:151`: `find "$REPO" -path "$REPO/.venv" -prune -o -print0 | xargs -0 chown hermes:hermes` walks the whole filesystem tree, git-ignored files included. That makes `/opt/data/hermes-agent/agent-home/agent-home.env` owned by `hermes`, so `chmod 600` (`docs/deployment/README.md:69`) only keeps out *other* users.
- `agent-home/deploy/agent-home.service:35-38`: the service runs `User=hermes` with `EnvironmentFile=/opt/data/hermes-agent/agent-home/agent-home.env`. systemd reads EnvironmentFile as root, so the service user does not need to be able to read the file.
- `agent-home/src/lib/auth/session.ts:59-93`: the cookie is `<payload>.<HMAC-SHA256(AGENT_HOME_SESSION_SECRET)>`, and its `principal` (`user_id`, `role`) is trusted as-is. Anyone holding the secret can mint a session for any principal or role on the public agent-home origin.
- Privileged DSN: `MULTI_USER_HANDOFF.md:184-187` says agent-home's own DSN was moved to the NOBYPASSRLS `agent_home_app` role, but "Python/migrations/CLI keep the privileged DSN". The gateway loads it from `/opt/data/hermes-staging.env` through the drop-in `hermes-gateway.service.d/20-database-url.conf` (`docs/design/SESSION-HANDOFF-2026-09-projects-run-resilience.md:99-103`). `DATABASE_URL` is missing from the terminal env blocklist (`tools/environments/local.py:119-212`, `_is_hermes_internal_secret` at :228-271), so every agent shell command inherits it in its environment without reading any file. `/proc/<gateway pid>/environ` is also readable by the same uid.
- `deploy/hermes-calendar-poller.service:14-19`, `deploy/hermes-seminar-outreach.service:12-17`: `/opt/data/hermes-messaging.env` (IMAP app passwords per `docs/design/unified-credential-store.md:16`, `DATABASE_URL`, `HERMES_HOME`) is consumed by `User=hermes` units. HERMES_HOME holds Google Workspace OAuth tokens, WhatsApp `session-*/creds.json` (`deploy/wa-bridge-add.sh:17,39`, created `-o hermes`), `config.yaml` with inline MCP credentials (`.agents/skills/testing-hermes-systest-box/SKILL.md:478-483,576-579`), and the Supabase JWT secret and service-role key (`MULTI_USER_HANDOFF.md:180-181`).
- In-process mitigations don't hold up. `agent/file_safety.py:168-200` (read-deny on `.env`, `auth.json`, `credentials/`) says of itself "This is NOT a security boundary ... the agent can still `cat`", and `agent-home.env` does not match its `.env*` names (:155-164). The approval patterns in `tools/approval.py:284-305` cover *writes* to `.env`/`config.yaml`, not reads or `env`/`printenv`. DSN redaction (`agent/redact.py:222`) only masks the password in model-visible output. It does nothing about `curl`/`base64` exfiltration. No unit for the gateway in the repo shows `InaccessiblePaths=`/`ProtectProc=`. `hermes-*` units that have `ProtectSystem=strict` still leave `/opt/data/hermes-home-staging` and the checkout read-write (e.g. `deploy/hermes-rag-ingest.service:39-41`).

**Impact**
- Anyone who can get a prompt injection to an agent turn (an inbound WhatsApp/email message, a fetched web page or document) can run one shell command such as `env`, `cat /opt/data/hermes-agent/agent-home/agent-home.env` or `cat $HERMES_HOME/...` and exfiltrate the result over the network. No approval prompt fires.
- What leaks:
  - The privileged Python DSN (documented as `postgres`, BYPASSRLS): read/write access to all tenants' `app_prod` rows and the `auth`/`storage` schemas in the single Supabase database.
  - `AGENT_HOME_SESSION_SECRET`: forged owner/admin agent-home sessions.
  - The Supabase JWT secret and service-role key.
  - Google OAuth refresh tokens, Gmail app passwords and WhatsApp `creds.json`: takeover of the owner's mail, calendar and chat accounts.
- Because `hermes` also owns the source tree, `write_file` can plant persistent code that every `hermes-*` unit and agent-home load on their next restart.
- Preconditions: the injected instruction has to be acted on in a turn with terminal access, which is the default toolset. No other access is needed.

**Fix**
- Move every env file out of the checkout and HERMES_HOME to `root:root 0600` (e.g. `/etc/hermes/{agent-home,gateway,messaging}.env`), consumed only through `EnvironmentFile=`. Exclude them from the `hermes-deploy.sh:151` chown, or simply keep them outside `$REPO`.
- Stop agent shells inheriting the secret: add `DATABASE_URL`, `AGENT_HOME_*`, `SUPABASE_*KEY*`, `*_JWT_SECRET` and `EMAIL*_PASSWORD` to `_build_provider_env_blocklist()`, or match on `*_URL` containing `@` plus `*SECRET*`/`*PASSWORD*` in `_is_hermes_internal_secret`. Mount `/proc` with `hidepid=2`, or set `ProtectProc=invisible` on the gateway unit.
- Give tool execution its own uid. Use terminal backend `docker`/`ssh`, or a dedicated `hermes-tools` user via `sudo -u`, with `InaccessiblePaths=` on HERMES_HOME credential dirs, the env files and the checkout. That way the gateway keeps its secrets and model-authored commands cannot read them.
- Run the Python app on the existing non-BYPASSRLS `hermes_app` role (`hermes owner db-role`, `hermes_cli/owner.py:129`). Keep the `postgres` DSN in a root-only file that only migrations use.
- Rotate `AGENT_HOME_SESSION_SECRET`, the DB password, the Supabase JWT secret and service-role key, and the OAuth/IMAP/WhatsApp credentials afterwards.

Effort: L · Confidence: high

### F17 — Agent can self-approve its own gated actions by clicking Approve / Always approve through ungated app_act — High

_Verification: confirmed._

**Evidence**
- `app-mcp/app_mcp/guard.py:14-32` — `DESTRUCTIVE_WORDS` is the only check `app_act` applies (`server.py:178-181`, click/select only). It has no `approve`, `allow`, `grant`, `accept`, `trust`, `always` or `consent`, so "Approve", "Approve for this chat" and "Always approve" are treated as safe clicks.
- `agent-home/src/lib/app-mcp/actions.ts:46-70,126-128` — `resolveElement` matches by id, CSS selector or accessible name, then calls `el.click()`. Nothing marks approval controls as off-limits to the agent. `snapshot.ts:161-185` lists every visible button. The only exclusion is the floating shell chrome (`snapshot.ts:45-50`), and only for snapshot and name lookup. The selector path uses a raw `document.querySelector`, and when a selector matches nothing in the snapshot `_element_name` (`server.py:121-140`) returns `None`, so the guard passes.
- `agent-home/src/components/MobileShell.tsx:117-119` — `AppMcpBridge` is mounted on every MobileShell page, including `/inbox`, `/webview` and `/chat`. `executeCommand` `navigate` (`actions.ts:108-117`) lets the agent move the user's live tab to any internal path.
- FG-10 inbox: the Approve button (`agent-home/src/components/inbox/ApprovalsList.tsx:67-74`, `onClick` at line 70) posts `answer: "approved"` (`InboxView.tsx:115-122`). `NotificationStore.answer` (`hermes_cli/human_comms.py:536-584`) only checks C2 read visibility and records `answered_by = <user>`, `answered_via = "web"`. The approval looks as if the human gave it. These rows gate irreversible outbound replies: `todo_outbound.propose` (`hermes_cli/todo_outbound.py:183-213`, `reversible=False`) creates the approval, and `hermes todos send` (`hermes_cli/todos_cmd.py:450-520`) only requires `approval.granted`. Project-run approvals (`hermes_cli/projects_run.py:736-743`) use the same mechanism.
- C6 webview: the Approve button (`agent-home/src/components/webview/PendingApprovals.tsx:42-49`, `onClick` at line 45) calls `POST /api/webview/approval/{id}`. `webview_resolve_approval` (`hermes_cli/web_server.py:4873-4920`) runs the escalated action (off-scope, credentialed or destructive) directly through `_webview_execute`.
- Chat tool approval: the `ApprovalModal` choices are labelled "Approve", "Approve for this chat" and "Always approve" (`agent-home/src/components/chat/ApprovalModal.tsx:8-13`), and the buttons are plain clickable buttons (lines 76-94, `onClick` at line 84). They go to `/api/chat/approval` → `resolve_gateway_approval`. An `always` choice calls `approve_permanent` + `save_permanent_allowlist` (`tools/approval.py:1615-1618`, `2895-2900`), which permanently allowlists the pattern, including `mcp_app_app_act_destructive` itself. The `/chat` instance (`ChatPane.tsx:1110-1116`) is in the main page and shows up in snapshots. The lead-panel instance (`LeadChatHost.tsx:571`) is hidden from snapshots but can still be reached with a selector such as `[data-component="ApprovalModal"] button`.
- No mitigation found: no `event.isTrusted` checks anywhere in `agent-home/src`, no `data-appmcp-deny` style opt-out, and no server-side marker that tells a web answer apart from an agent-driven one. The gating configuration in `app-mcp/README.md` (`approvals.tools: [mcp_app_app_act_destructive, ...]`) gates only `app_act_destructive`, so `app_act` runs with no approval.

**Impact**
- Who can exploit it: anyone who can prompt-inject the agent (inbound email, WhatsApp or Telegram content, fetched web pages, files). It runs as the signed-in user whose agent-home tab is attached to the hub.
- Precondition: the user has any MobileShell page open, so the bridge is connected. Agent-driven navigation is visible in the user's tab, so the user may notice, but the click finishes in milliseconds.
- Inbox and C6 queues: these are asynchronous and reachable within one turn. One example chain: `app_act(navigate,"/inbox")` → `app_describe_page` → `app_act(click,name="Approve")` → `hermes todos send ...`. This sends an irreversible message to a third party (or grants an escalated webview action) with the approval recorded as the user's own.
- Chat ApprovalModal: the paused turn cannot click its own modal. MCP tools do not run in parallel by default (`agent/tool_dispatch_helpers.py:104-145`). So this needs a second live session for the same user, such as the lead chat plus `/chat`, cron, or a gateway session. "Always approve" then removes the gate permanently for later calls.
- Overall, this defeats the human-in-the-loop design that `app_act_destructive`, FG-10 and C6 depend on. Severity is High rather than Critical because it needs injection plus an open agent-home tab, and the action is visible.

**Fix**
- Treat consent controls as never operable by the agent. Add `data-appmcp-deny` to the Approve, Deny and Always buttons in `ApprovalsList`, `PendingApprovals` and `ApprovalModal`, and to `ConsentForm`. Make `snapshotElements` skip `[data-appmcp-deny]` and its descendants. Make `resolveElement` return `null` (or `executeCommand` fail) when `el.closest('[data-appmcp-deny]')` matches, whichever lookup path was used. Apply the same check in the hub for `app_act_destructive`.
- Second layer: add `approve|allow|grant|accept|trust|always|deny|consent|authori[sz]e` to `DESTRUCTIVE_WORDS`. Have the guard read the resolved element's name from the browser's result (fixes the selector/`None` fail-open shared with F4) and fail closed when the name is unknown.
- Require a real gesture: in each approval `onClick`, `if (!e.isTrusted) return;`. `HTMLElement.click()` dispatches untrusted events.
- Server-side defence: tag BFF requests made while an app-mcp command is in flight, or require a short-lived user-gesture nonce on `/api/comms/notifications/*/answer`, `/api/webview/approval/*` and `/api/chat/approval`. Never accept `always` without a fresh gesture.

Effort: S · Confidence: high

### F18 — Agent can tick Folder Bridge "Trusted for import" through app-mcp, then import files without approval — High

_Verification: confirmed._

**Evidence**
- `agent-home/src/components/files/FolderBridgeView.tsx:269-281`: the trust flag is a plain `<input type="checkbox" data-component="FolderBridgeTrust">`. Its accessible name comes from the wrapping `<label>`, which reads "Trusted for import". It is only disabled while `folder.permission !== "granted"`.
- `FolderBridgeView.tsx:116-119`: `handleToggleTrusted` calls `setFolderTrusted(id, e.target.checked)` with no `event.isTrusted` check and no other user-gesture check.
- `agent-home/src/app/files/bridge/page.tsx:20-24` renders the view inside `MobileShell`. `showCoral` defaults to true, so `MobileShell.tsx:119` mounts `AppMcpBridge` on this page. `lib/app-mcp/snapshot.ts:161-185` excludes only hidden elements and the shell chrome, so the checkbox is listed (with `checked`) in `app_describe_page`.
- `app-mcp/app_mcp/server.py:178-181` and `:121-140`: `app_act` runs `looks_destructive` against the requested name. `guard.py:14-32` has no word that matches "Trusted for import", so the action is forwarded. `lib/app-mcp/actions.ts:126-128` then calls `el.click()`, which toggles the checkbox and fires React's `onChange`. This works without the F4 element-mismatch bypass.
- `lib/folder-bridge/handles.ts:208-214` sets `entry.trusted` in the module-level registry. `transport.ts:168` (`if (!command.approved && !isFolderTrusted(...))`) is the only check on `importFile`, which then uploads the file (`transport.ts:178-191`).
- `app-mcp/app_mcp/server.py:397-406`: `folder_bridge_import_file` sends `approved=False`. `app-mcp/README.md:108-123` leaves it out of `approvals.tools` on purpose, on the grounds that "the human decision has already been made" when the user toggles trust.
- The page does not need to be open. Trust and the WebSocket live in module state and survive in-app navigation (`handles.ts:120-139`; there is no disconnect on unmount). `app_act navigate` uses `softNavigate` (`actions.ts:108-116`), so the agent can move the user's tab to `/files/bridge`, tick the box and keep the bridge connected.

**Impact**
- Who can exploit it: the agent itself, for example after prompt injection through an inbound email or WhatsApp message processed by the triage services. No approval prompt is shown: `app_act`, `folder_bridge_list_directory` and `folder_bridge_import_file` are all ungated in the documented config.
- Preconditions: in the current page load, the user has added a folder (permission `granted`) and pressed Connect, and the agent-home tab is still open. Restored handles start as `prompt`, which keeps the checkbox disabled, and "Add folder" needs a real user gesture. So the agent cannot create access, only widen it.
- Effect: every file in the approved folders (which can be broad, e.g. `~/Documents`) can be listed and copied byte-for-byte into Files. From there other agent tools can read or send it on. This defeats the per-file approval that the README and HANDOFF set up for `read_file`/`search_files`/`import_file_approved`. The only visible signal is the upload progress bar, if the user is looking at the tab.
- Deployment note: `HANDOFF.md:75-80` records only `folder_bridge_read_file` and `folder_bridge_search_files` being added to prod `approvals.tools`. If `folder_bridge_import_file_approved` was never gated on the box, the agent can import without approval in prod anyway. That is a config gap; it does not mitigate this code path. Runtime config was not checked.

**Fix**
- Keep consent controls out of agent reach. Mark them, e.g. `data-appmcp-exclude`, on the trust checkbox, Connect, Add folder, Needs re-approval and Remove. Skip that marker (and its descendants) in `snapshotElements`. Make `resolveElement`/`executeCommand` refuse any target where `el.closest('[data-appmcp-exclude]')` matches, including the `selector` and `elementId` paths.
- Require a real user gesture for the toggle: `onChange={(e) => e.nativeEvent.isTrusted && handleToggleTrusted(folder.id, e.target.checked)}`. Programmatic `el.click()` produces `isTrusted === false`.
- Defence in depth: refuse app-mcp commands while `pathname === "/files/bridge"`, or add a shared `SENSITIVE_SELECTOR` next to `SHELL_CHROME_SELECTOR`. Consider gating `mcp_app_folder_bridge_*` (including `import_file`) in `approvals.tools`, and confirm `import_file_approved` is gated on the box.

Effort: S · Confidence: high (code path); medium (prod approvals config)

### F19 — Unauthenticated loopback MCP endpoint lets the agent's own terminal bypass approvals.tools for destructive UI actions and local-file reads — High

_Verification: confirmed._

**Evidence**
- `app-mcp/app_mcp/server.py:60` — `FastMCP("app", host="127.0.0.1", port=9220)` is created with no `auth_server_provider`/`token_verifier`; nothing on the streamable-HTTP `/mcp` endpoint authenticates callers.
- `app-mcp/app_mcp/server.py:431-439` — `_main()` runs `mcp.run_streamable_http_async()`; the HMAC ticket check exists only in `_ws_handler` for the browser WebSocket paths (`server.py:92-97`).
- `plugins/tool-approval/__init__.py` — the `approvals.tools` gate is a plugin inside the agent process that intercepts the agent's own tool dispatch. A direct `tools/call` over HTTP never traverses it, so gating `mcp_app_*` in `config.yaml` protects only the in-agent path.
- `app-mcp/app_mcp/server.py:270-283` — `app_act_destructive` executes with `enforce_guard=False` and no further check; a direct call clicks/types on the live agent-home UI session with no approval prompt.
- `app-mcp/app_mcp/server.py:369-415` — `folder_bridge_read_file`, `folder_bridge_search_files`, and `folder_bridge_import_file_approved` forward straight to the browser; `agent-home/src/lib/folder-bridge/transport.ts:168` treats `command.approved === true` as sufficient and imports from folders not marked trusted, so the in-chat approval is enforced by nothing server-side.
- `tools/terminal_tool.py:688`, `run_agent.py:82` — `TERMINAL_ENV` defaults to `local`, so the agent's shell runs on the same box as `app-mcp.service` (`User=hermes`, same as all units per `docs/deployment/PRODUCTION.md:67`); a prompt-injected agent can `curl -H 'Host: 127.0.0.1:9220' http://127.0.0.1:9220/mcp` and drive `initialize` + `tools/call` directly.
- `agent-home/deploy/Caddyfile.agent-home:29-31` — Caddy reverse-proxies only `/app-mcp/ws` and `/app-mcp/folders/ws` (9221); 9220 is loopback-only, which bounds the exposure to local processes rather than the internet. mcp==1.26.0 (`uv.lock`) Host/Origin transport checks are header validation, not authentication.

**Impact**
- Prompt injection reaching the agent (WhatsApp/email/web content — the deployment's documented threat model in `docs/deployment/mcp-approval-gating.md` and `app-mcp/README.md:83-94`) can instruct the terminal tool to POST MCP requests directly, converting the approval prompt the user would see into zero visibility.
- With any agent-home tab open (the AppMcpBridge is always-on), an attacker clicks Delete/Send/Submit and reads page state as the signed-in user.
- While a `/files/bridge` session is connected, arbitrary file reads/imports from the user's approved Mac folders — the exact exfiltration path the README recommends gating.
- Any other process or user on the box (WA bridges, dashboard, email pollers — all `hermes`, or any shell user) gets identical access; TCP loopback has no per-uid restriction.

**Fix**
- Give `FastMCP` a `token_verifier`/auth provider backed by a secret shared only with the gateway (send it via `mcp_servers.app.headers` in `config.yaml`, keep it out of the terminal-inherited env), or serve `/mcp` on a Unix socket owned `0600` by a dedicated `app-mcp` user.
- Enforce approval in the service, not the client: require a short-lived, single-use approval token minted by the gateway's approval flow for `app_act_destructive`, `folder_bridge_read_file`, `folder_bridge_search_files`, and `folder_bridge_import_file_approved`; stop trusting the `approved` boolean in `transport.ts:168` on the caller's word.
- Run `app-mcp.service` as its own system user and consider an egress rule blocking `127.0.0.1:9220` from terminal-tool subprocesses.

Effort: M · Confidence: high

### F20 — Session history is not scoped by principal: members can search and read other users' transcripts — High

_Verification: confirmed._

**Evidence**
- `hermes_state.py:730` - `sessions.user_id` is stored, but it holds the *platform* sender id (`create_session`, `hermes_state.py:1843-1895`), not the C1 principal. No session row records which principal owns it.
- `hermes_state.py:4615-4707` - `SessionDB.search_messages` builds its WHERE clause from FTS match, active/compacted, `source_filter`/`exclude_sources` and `role_filter` only. Nothing filters by user or principal. `list_sessions_rich` (`hermes_state.py:2970`) and `search_sessions` (`hermes_state.py:4983-5010`, `WHERE s.source = ?` only) work the same way.
- `tools/session_search_tool.py:619-740` - `session_search()` takes no principal argument. Its four modes all run against the whole profile DB: discovery `_discover` -> `db.search_messages` (`:499-525`), browse `_list_recent_sessions` -> `list_sessions_rich` (`:260-300`), read `_read_session` -> `get_messages` (`:211-257`, the whole transcript), and scroll `_scroll` (`:303`).
- `tools/session_search_tool.py:686-702` + `:167-208` - on a read miss, `_locate_session_db` scans every profile's `state.db` for the id. `profile=` (`:144-164`, `:666-674`) opens any profile's DB. So reads can also cross profiles.
- `agent/tool_executor.py:1193-1208` - the live agent loop handles `session_search` inline and does not pass `agent._internal_user_id`/`_internal_user_role`. The registry handler (`tools/session_search_tool.py:903-918`) also drops the `principal_user_id` that `model_tools.py:1150-1166` / `agent/tool_executor.py:1417` forward. Compare `todo` at `agent/tool_executor.py:1161-1182`, which does scope by principal.
- `session_search` is in `_HERMES_CORE_TOOLS` (`toolsets.py:31-61`), so every gateway platform toolset (`hermes-telegram`, `hermes-whatsapp`, ... `toolsets.py:437-475`) and the `api_server` surface used by agent-home get it. No role-based tool gating exists.
- Agent-home correction: `gateway/session_chat.py:24-92` `build_session_agent` never passes `internal_user_id`/`internal_user_role` to `AIAgent`, so agent-home turns run with `principal_user_id=None`. The member identity is only in session contextvars (`hermes_cli/web_server.py:10519-10527`). Gateway turns do carry it (`gateway/run.py:17378-17379`). The tool ignores it on both paths.
- Same root cause, no agent needed: the dashboard routes `GET /api/sessions` (`hermes_cli/web_server.py:5460`, `list_sessions_rich` at `:5523`), `GET /api/sessions/search` (`:5692`) and `GET /api/sessions/{id}/messages` (`:10307-10315`) never resolve a principal or filter by owner. `POST /api/sessions/{id}/chat` (`:10466-10506`) resolves the principal but does not check that the session belongs to them, so it loads and continues any session's history. The agent-home BFF forwards these for any signed-in principal (`agent-home/src/app/api/chat/sessions/route.ts`, `agent-home/src/app/api/chat/sessions/search/route.ts`, `agent-home/src/app/api/chat/send/route.ts:96-122`). The only check is the `getPrincipal()` login check.
- No mitigation found: the dashboard auth middleware only authenticates. Supabase RLS (`bind_principal`, MULTI_USER_HANDOFF.md) covers Postgres tables, not the SQLite `state.db`.

**Impact**
- Who: any enrolled non-owner principal (admin/member/viewer) signed in to agent-home, and any authorised gateway contact (Telegram/WhatsApp/email sender that passes the platform allowlist or is linked to a principal).
- How: ask the agent "search my past sessions for <keyword>" / "list my recent sessions" / "read session <id>". A member can also call `/api/chat/sessions/search?q=` and open any conversation in the agent-home UI directly.
- What leaks: every principal's transcripts on the shared brain, including the owner's private chats and tool output (`role_filter=tool`), which can contain secrets, plus other profiles' sessions via `profile=`/`_locate_session_db`. This defeats the per-principal isolation the multi-user rollout built for Supabase data.
- Precondition: at least one non-owner principal or authorised third-party contact exists. Per MULTI_USER_HANDOFF.md the member flow is shipped and enabled in prod (the synthetic test member was removed afterwards). With only the owner enrolled, the risk is latent.

**Fix**
- Record ownership: add `sessions.principal_id`, set it in `create_session`/`ensure_session` from `source.internal_user_id` (gateway) and `principal.user_id` (`create_session_endpoint`, `session_chat`), and backfill existing rows to the owner.
- Pass `principal_id`/`role` into `AIAgent` from `build_session_agent` (from the `session_chat` principal), and pass `principal_user_id=agent._internal_user_id, principal_role=agent._internal_user_role` from `agent/tool_executor.py:1193` and the registry lambda into `session_search()`.
- Add a mandatory `principal_id` argument to `search_messages`, `list_sessions_rich`, `search_sessions`, `get_session`/`get_messages`/`get_messages_around`/`get_anchored_view`, and append `s.principal_id = ?` unless the caller is the owner. Fail closed when the principal is None and multi-user is configured.
- In `session_search`, refuse `profile=` and `_locate_session_db` for non-owner principals.
- Apply the same owner filter in the REST routes `/api/sessions`, `/api/sessions/search`, `/api/sessions/{id}`, `/api/sessions/{id}/messages`, `/api/sessions/{id}/chat` (404 on a foreign session), via `_comms_resolve_principal`.
- Regression tests: a member-principal `session_search` (all four modes) and `GET /api/sessions/search` against an owner session return nothing.

Effort: M · Confidence: high

### F21 — Default non-strict media delivery attaches any non-denylisted host file to chat replies — Medium

_Verification: partially-confirmed._

**Evidence**
- `hermes_cli/config.py:2888` ships `gateway.strict: False`; `gateway/run.py:1561-1567` only sets `HERMES_MEDIA_DELIVERY_STRICT` when the key is present, and `gateway/platforms/base.py:1109-1121` (`_media_delivery_strict_mode`) defaults to `"0"`. No deploy doc, unit or script in the repo enables strict mode.
- `gateway/platforms/base.py:1248-1326` (`validate_media_delivery_path`): in non-strict mode (`:1311-1314`) any existing regular file passes unless `_path_under_denied_prefix` matches. Symlinks are resolved first, and there is no recency or ownership check.
- The denylist (`base.py:1010-1035`, `:1124-1187`) covers `/etc`, `/proc`, `/root`, `/var/lib`, `/var/log`, `~/.ssh`, `~/.aws`, `~/.config` and the like, plus specific credential files and directories at the HERMES_HOME/root level. It does not cover `$HERMES_HOME/whatsapp/`, `~/.git-credentials`, `~/.netrc`, `~/.npmrc`, `~/.pypirc`, `/opt/data/**`, `/tmp`, or project `.env` files outside HERMES_HOME. The read guard does block those `.env` files (`agent/file_safety.py:154-165` `_BLOCKED_PROJECT_ENV_BASENAMES`, documented at `:183-185`), so the delivery side lags the read side, contrary to the parity claimed at `base.py:1135-1142`.
- Both extractors can reach these files. `MEDIA_TAG_CLEANUP_RE` (`base.py:1475-1481`) matches `.json`, `.yaml`, `.txt`, `.csv`, `.zip`, `.gz` and others (`:1439-1456`). `MEDIA_EXTENSIONLESS_TAG_RE` (`:1489-1495`) together with `_path_lacks_deliverable_extension` (`:1505-1507`) accepts names with no suffix, and in Python `Path(".env").suffix == ""`, so `MEDIA:/x/.env` is delivered (`extract_media`, `:3649-3656`).
- This runs on every final gateway reply: `_process_message_background` at `base.py:4911-4912` (MEDIA tags) and `:4930-4931` (bare paths), and after streaming through `gateway/run.py:11649` -> `_deliver_media_from_response` (`run.py:12718-12752`). No tool call or approval is involved. Text replies go through `_redact_gateway_user_facing_secrets` (`run.py:412-430`), but attachments skip it.
- Deployment: the gateway runs as `hermes` with `HERMES_HOME=/opt/data/hermes-home-staging` and home `/opt/data/hermes-user` (`docs/deployment/README.md:285`, `docs/deployment/deployment-path.md:151`). The WhatsApp bridges run as `hermes` and keep their login state in `/opt/data/hermes-home-staging/whatsapp/session-*` (`docs/deployment/PRODUCTION.md:73-76`, `:205`), which the denylist does not cover. The `hermes-gateway` unit is not in `deploy/`; only the auxiliary units show `ProtectSystem`/`ProtectHome` hardening.

**Impact**
- A gateway-authorised but lower-trust participant (a paired DM user, or a member of an allowlisted group or thread) can ask for `MEDIA:/opt/data/hermes-home-staging/whatsapp/session-connectar/creds.json`, which would let them hijack the business WhatsApp account. The same works for `~/.git-credentials` (`skills/github/github-auth/SKILL.md:66-68` tells the agent to create it), kanban/state DBs, logs and `/tmp` artifacts. The bytes arrive as a native attachment, with no redaction and no tool call.
- Prompt injection in content the agent reads (an email, web page or document) can trigger the same delivery. The file always goes to the chat being answered (`event.source.chat_id`); `send_message` is not in the default gateway toolsets and is approval-gated (`tools/send_message_tool.py:1844-1850`). So injection only reaches the attacker when the attacker is in that chat (a group, or the attacker is the sender). Otherwise the file lands with the owner.
- Mitigating factors that keep this at Medium: the agent already has terminal and file tools as `hermes`, so an authorised participant could usually get the agent to `cat` the same files. The MEDIA path mainly adds a channel that skips approval, tool calls and redaction, and handles binary files. `/opt/data/supabase/docker/.env` is only reachable if `hermes` can read it, and its ownership/mode is not documented (root-run Docker Compose; it is likely, not confirmed, unreadable). `/opt/data/deploy/state-secrets.env` is documented as root-only.

**Fix**
- Default to strict mode for messaging gateways: set `"strict": True` in `hermes_cli/config.py` (and make `_media_delivery_strict_mode` default to `"1"`), so delivery is limited to cache roots, `media_delivery_allow_dirs` and files produced during the current turn. Keep `gateway.strict: false` as an explicit single-user opt-out. Set it explicitly in the prod `config.yaml` now.
- Make the denylist match the read guard: in `_path_under_denied_prefix`, reject any basename in `agent.file_safety._BLOCKED_PROJECT_ENV_BASENAMES` or matching `.env.*`, plus `.git-credentials`, `.netrc`, `.npmrc`, `.pypirc`, `.vault-token` and `.password-store/`. Add `"whatsapp"` to `_ROOT_CREDENTIAL_DIRS` (also `*.db`/`*.sqlite` under HERMES_HOME).
- Do not deliver dotfiles or extensionless files through `MEDIA_EXTENSIONLESS_TAG_RE` unless they sit under an allowed root.
- Optionally tie delivery to the requesting principal's role, and run the gateway unit with `ProtectSystem=strict`, `ReadWritePaths=` and `InaccessiblePaths=/opt/data/supabase /opt/data/deploy`.
- Add regression tests: `validate_media_delivery_path` must return `None` for `$HERMES_HOME/whatsapp/session-x/creds.json`, `/srv/app/.env`, `~/.git-credentials` and `~/.netrc` in default mode.

Effort: M · Confidence: high

### F22 — Gateway approvals are not bound to the requester: any authorised co-participant in a shared session can approve another user's pending dangerous command, and plain-text 'always' skips slash-command gating — Medium

_Verification: confirmed._

**Evidence**
- `tools/approval.py:1449-1456`: `_ApprovalEntry` stores only `event`, `data`, `result`. It does not record who triggered the command.
- `tools/approval.py:2211-2213`: `_await_gateway_decision` adds the entry to `_gateway_queues[session_key]`. The only key is the session.
- `tools/approval.py:1488-1513`: `resolve_gateway_approval(session_key, choice, resolve_all)` pops the oldest entry (or every entry) and sets `entry.result = choice`. It takes no approver identity, so it cannot check one.
- `gateway/slash_commands.py:4227-4280`: `_handle_approve_command` gets the session key from the sender's source, reads `all`/`session`/`always`, and calls `resolve_gateway_approval` (line 4272). Nothing compares the sender with whoever started the turn.
- `gateway/run.py:5041-5054`: the busy-path gate only checks `_is_user_authorized`, i.e. that the sender may talk to the gateway.
- `gateway/run.py:5103-5131`: while an approval is pending, plain text such as `yes`, `ok`, `always` or `approve session` is rewritten to `/approve [always|session]` and sent directly to `_handle_approve_command`. This path does not call `_check_slash_access`, so it also gets around the opt-in `allow_admin_from` / `user_allowed_commands` gating that `run.py:8916-8920` and `run.py:9318-9321` apply to real slash commands.
- `tools/approval.py:2616-2622`, `1681-1689`: with `always`, the pattern goes into `_permanent_approved` and is written to `command_allowlist` in config. That allowlist is profile-wide, so it applies to all sessions and survives restarts.
- Approval buttons have the same gap. Telegram `plugins/platforms/telegram/adapter.py:4939-4982` only checks `_is_callback_user_authorized`. Slack `plugins/platforms/slack/adapter.py:3657-3659` and Discord `plugins/platforms/discord/adapter.py:6451-6474` (`allowed_user_ids`/`allowed_role_ids`) do likewise. None compare the clicker with the requester.
- Shared sessions are a default for threads: `gateway/config.py:581-582` and `gateway/session.py:921-923` set `thread_sessions_per_user=False`, so Telegram topics and Slack/Discord threads are shared. Plain groups are per-user by default (`group_sessions_per_user=True`).

**Impact**
- Who can exploit: any user on the platform allowlist who is in the same shared session (a thread by default, or a group with `group_sessions_per_user: false`) while another participant's turn is waiting on a dangerous-command prompt. They can reply `yes` or `always`, or send `/approve all always`, and the other person's command runs without that person's consent.
- `always` adds the pattern to the global `command_allowlist`, so later commands of that kind skip approval in every session.
- Where an operator has turned on slash gating (`allow_admin_from`), a non-admin can still approve, including `always`, by typing plain text. This defeats the intended admin-only control.
- Limits: the attacker must already be authorised, and without slash gating every authorised user can already steer a shared thread and approve their own commands. So the gain is mostly cross-user consent bypass and changing the global allowlist, not new access. The documented production setup uses WhatsApp bridges (no threads, groups isolated per user by default). Exposure there depends on runtime config that was not reviewed, which is why this is Medium rather than High.

**Fix**
- Record the requester on each entry: add `requester_id` (platform user_id plus internal principal) to `_ApprovalEntry`, populated from the session context in `_await_gateway_decision`.
- Change the signature to `resolve_gateway_approval(session_key, choice, resolve_all=False, approver_id=None, approver_is_admin=False)`. Resolve only entries where `entry.requester_id == approver_id`, unless `approver_is_admin`. Reject `resolve_all` and `always` from non-admins in shared sessions (`is_shared_multi_user_session`).
- Pass `event.source.user_id` and the `SlashAccessPolicy.is_admin` result from `_handle_approve_command`, `_handle_deny_command`, and every platform button handler (Telegram, Slack, Discord, Matrix, Feishu, Teams, QQ, WhatsApp Cloud).
- In `_handle_active_session_busy_message`, call `self._check_slash_access(event.source, "approve"|"deny")` before rewriting plain-text approval words.

Effort: M · Confidence: high

### F23 — Gateway expands @file:/@folder:/@diff/@url in untrusted chat text before the model runs — Medium

_Verification: partially-confirmed._

**Evidence**
- `gateway/run.py:10221-10263`: when the inbound text contains `@`, `_prepare_inbound_message_text` calls `preprocess_context_references_async(message_text, cwd=_msg_cwd, allowed_root=_msg_cwd)`. `_msg_cwd` is `TERMINAL_CWD`, falling back to `~`. It then replaces the user turn with the expanded text. There is no check on platform, sender role, principal, or enabled toolsets.
- `gateway/run.py:10205-10219`: the quoted `reply_to_text` (up to 500 chars of *someone else's* message) is prepended before expansion. In a group, an `@file:` written by a non-allowlisted participant is expanded when an authorised user replies to it.
- `gateway/platforms/webhook.py:552-556,695-713` with `gateway/authz_mixin.py:262`: webhook events are always authorised, and the rendered prompt contains third-party payload fields (for example an issue or PR body). That text goes through `handle_message`, so `@file:` in the payload is expanded too.
- `gateway/run.py:1651-1654`: with `terminal.cwd` unset or `.` (the default at `hermes_cli/config.py:1151`), `TERMINAL_CWD` becomes `MESSAGING_CWD` or `Path.home()`. In the default layout, `~/.hermes` (config.yaml, memories/, sessions/, logs/, profiles/) is inside the allowed root.
- `agent/context_references.py:351-361` only enforces `relative_to(allowed_root)`. `agent/context_references.py:364-413` adds the `_SENSITIVE_HOME_*` list, `$HERMES_HOME/.env` and `file_safety.get_read_block_error` (`agent/file_safety.py:168-346`: auth.json, .env*, mcp-tokens/, credentials*/, google-workspace*/, skills/.hub). `config.yaml`, `state.db`, `memories/`, `sessions/`, `logs/` and `profiles/` are not blocked.
- `agent/context_references.py:248-279` inlines the file text (with line ranges) under `--- Attached Context ---`. `:282-296` lists folders. `:299-324` runs `git diff`/`git log -p` in the cwd. `:327-348` fetches `@url:` server-side through `web_extract_tool`.
- `website/docs/user-guide/features/context-references.md:102` says that on messaging platforms "the `@` syntax is not expanded by the gateway". The code does the opposite, so operators have no reason to set a narrow `terminal.cwd`.

**Impact**
- Mitigations that limit this: the gateway still requires an authorised sender (`_is_user_authorized`, except webhook and HA). The default `hermes-<platform>` toolsets already give every authorised sender `read_file` (no root restriction, same deny-list) and `terminal`. File and terminal tools are not gated by principal role. So for a default-toolset owner or member, this adds no new read capability.
- The real additions are:
  - (a) Senders on a platform locked to a restricted toolset (for example `safe`, `toolsets.py:336`, which has no file or terminal tools) can still read files under the root.
  - (b) Text written by third parties (quoted group replies, webhook payloads) causes a deterministic read before the model runs. This does not depend on a prompt injection persuading the model, and the content reaches a model whose reply goes back to that chat or webhook destination.
  - (c) It goes around per-platform hardening that operators believe applies, because the docs say expansion does not happen.
- In this deployment, `HERMES_HOME=/opt/data/hermes-home-staging` and the service user's home is `/opt/data/hermes-user` (`docs/deployment/README.md:70-73`, `.agents/skills/testing-hermes-systest-box/SKILL.md:90`). With `terminal.cwd` unset, the allowed root is `/opt/data/hermes-user`. The live `config.yaml` (which holds `dashboard.basic_auth.secret`), memories and logs are then outside the root, and only the dummy `~/.hermes` and other home files are exposed. If the private deploy-state config sets `terminal.cwd` to `/opt/data` or the HERMES_HOME tree, the live config and signing key become readable by @-reference. I could not verify this setting from the public repo.

**Fix**
- Turn off @-reference expansion in `_prepare_inbound_message_text` by default, behind a `gateway.context_references` config flag that defaults to `false`. When the flag is on, expand only if the sender resolves to the owner principal (`source.internal_user_role == "owner"`) and the platform's `_get_platform_tools(...)` includes `file` (and `web` for `@url`).
- Run expansion on the sender's own `event.text`, before the `reply_to_text` or webhook template content is added, and never for `Platform.WEBHOOK`.
- Pass a narrow `allowed_root` (a per-session workspace, not `$HOME`). Extend `_ensure_reference_path_allowed` to refuse `config.yaml`, `state.db*`, `memories/`, `sessions/`, `logs/` and `profiles/` under both the HERMES_HOME and root dirs.
- Correct `context-references.md:102`, or make the code match it.

Effort: S · Confidence: high

### F24 — Caddy admin API on localhost:2019 is unauthenticated, so any local user can rewrite the public TLS edge — Medium

_Verification: confirmed._

**Evidence**
- `docs/deployment/PRODUCTION.md:90-91`: the runbook says to reload with `caddy validate` + `caddy reload` ("admin API on :2019, no root needed"). This means the admin endpoint is enabled and any local UID can use it, not only root.
- `docs/deployment/hetzner-migration-runbook.md:30`: the port inventory lists `2019 (caddy admin)` as listening.
- `docs/deployment/PRODUCTION.md:81`: `caddy` terminates 80/443 for both public hostnames, using config `/etc/caddy/Caddyfile`. Lines 40-42 say Cloudflare is DNS-only, so Caddy is the only TLS edge and holds the Let's Encrypt certs.
- `HANDOFF.md:68-71`: the production Caddyfile is "hand-maintained, not rendered from the repo". The only checked-in fragment, `agent-home/deploy/Caddyfile.agent-home:26-37`, has no global options block, so nothing in the repo sets `admin off` or a socket address. Caddy's default is an unauthenticated HTTP API on `localhost:2019`. Its Host/Origin checks only stop browser DNS rebinding, not local `curl`.
- Local principals other than root/caddy: the `hermes` service user, whose agent has terminal tools (`tools/approval.py` patterns flag `curl|sh`, but not a plain `curl -X POST localhost:2019/load`), and the unrelated `aicoder` Qoder fleet (`PRODUCTION.md:219`). That fleet includes `qoder-ttyd.service`, a remote web terminal (`docs/deployment/qoder-remote-control-daemons.md:20-24,41`).
- No compensating control in the repo: the hermes/agent-home units add `ProtectSystem`/`NoNewPrivileges` but no loopback egress restriction. Only `hermes-embed.service:49` sets `RestrictAddressFamilies`, and that still allows AF_INET.

**Impact**
- Precondition: code execution as any local user, for example the `aicoder` Qoder daemons or their ttyd web terminal, or the hermes agent under prompt injection.
- One `POST /load` or `PATCH /config/...` swaps the live config with no restart and no root. An attacker can:
  - proxy `home.leolau.ai-and-i.io/api/session/*` or the dashboard host to a capture listener, harvesting passwords and session cookies under valid TLS;
  - publish loopback-only services on the public hostnames: Supabase Kong/Studio `:8000`, WA bridges `:3000/:3001` (`/send`, `/send-media`), embed `:8791`, app-mcp `:9221`;
  - use `file_server` to serve anything readable by the `caddy` user, including its ACME account and cert private keys.
- This crosses a real boundary for `aicoder`, which otherwise has no access to Hermes traffic or secrets. For `hermes` the gain is smaller: it already owns the checkout and env files. But it adds credential capture of other principals and public exposure of internal services.
- Changes are in-memory and survive until the next reload/restart. Because the Caddyfile is hand-maintained, tampering is hard to spot.

**Fix**
- In the global options block of `/etc/caddy/Caddyfile`, bind the admin API to a socket only caddy can reach, e.g. `{ admin unix//run/caddy/admin.sock }`, with `RuntimeDirectory=caddy` / `RuntimeDirectoryMode=0700` in a systemd drop-in. Alternatively use `admin off` and replace reloads with `systemctl restart caddy`. Note that `admin off` breaks `caddy reload` and the package's `ExecReload`.
- Update `PRODUCTION.md:90-91` to `sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`, and remove "no root needed".
- Defense in depth: an nftables output rule dropping `tcp dport 2019` on `lo` for `meta skuid != { root, caddy }`, or `IPAddressDeny=localhost` plus explicit allows on the `aicoder`/Qoder units.
- Commit the production Caddyfile (or a rendered template) to the repo, and diff it against `/etc/caddy/Caddyfile` during deploy so drift and tampering can be reviewed.

Effort: S · Confidence: medium

### F25 — tirith auto-install fetches an unpinned 'releases/latest' binary from a third-party repo and runs it as the gateway user — Medium

_Verification: partially-confirmed._

**Evidence**
- `tools/tirith_security.py:41` `_REPO = "sheeki03/tirith"`. This is a third-party, single-maintainer repo, not under the Hermes/Nous org.
- `tools/tirith_security.py:68-87` (also `hermes_cli/config.py:2577-2580`) sets `tirith_enabled` to `True` by default, so auto-install is on unless an operator turns it off. The only way to opt out is `tirith_enabled: false` or an explicit `tirith_path`. An explicit path never auto-downloads (`:665-676`).
- `gateway/run.py:2809-2814` calls `ensure_installed(log_failures=False)` at gateway startup. `cli.py:5994-6002` does the same for the CLI. If the binary is missing, `tools/tirith_security.py:632-721` starts `_background_install` in a background thread. `_resolve_tirith_path` (`:530-600`) can also install on the first `check_command_security` call.
- `tools/tirith_security.py:402` `base_url = https://github.com/{_REPO}/releases/latest/download` has no version pin. `:418-419` downloads the archive and `checksums.txt` from that same release.
- `tools/tirith_security.py:334-354` `_verify_checksum` checks the archive only against the `checksums.txt` it just downloaded. No expected digest is embedded in Hermes, so anyone who can publish a release controls both the binary and its hash.
- `tools/tirith_security.py:430-451` runs cosign only `if shutil.which("cosign")`. If cosign is missing it logs at info level and continues with SHA-256 only. If cosign raises or times out (`None`) it also falls back (`:444-447`). Even when cosign runs, `_COSIGN_IDENTITY_REGEXP` (`:44`) only proves the file came from the same repo's `release.yml`, so it does not protect against a compromised maintainer account or repo.
- `tools/tirith_security.py:461-477` moves the binary to `$HERMES_HOME/bin/tirith` and makes it executable. `:775-781` then runs it on every terminal command, passing the full command string.
- Minor: `tools/tirith_security.py:283-290` attaches `GITHUB_TOKEN` (if it is in the environment) to the request. Python `urllib` forwards that `Authorization` header when following the redirect to the release-asset CDN host.

**Impact**
- Precondition: the upstream repo, its maintainer account or its release pipeline is compromised, and the box has no tirith binary yet (fresh install, new `HERMES_HOME`, or the binary was deleted). The installer runs only when neither `PATH` nor `$HERMES_HOME/bin/tirith` has a binary, and it never updates an existing one. So a box that already has a good binary is not exposed to a later bad release unless the binary is removed.
- Result: arbitrary code runs as the `hermes` service user (PRODUCTION.md 4.2). That user can read everything in `HERMES_HOME`, i.e. `/opt/data/hermes-home-staging` (`.env` provider keys, WhatsApp sessions, memory). Because the binary sees every agent command, it can also persist and tamper with or exfiltrate commands. The agent already has a shell as this user, so this mainly adds a supply-chain entry point that needs no operator action.
- Likelihood is low, because it depends on a third party being compromised. That is why this is Medium and not High. The `tirith_fail_open=True` point raised in the original finding is already covered by F12 and is not counted again here.

**Fix**
- In `_install_tirith`, pin the release: `_TIRITH_VERSION = "vX.Y.Z"`, `base_url = f"https://github.com/{_REPO}/releases/download/{_TIRITH_VERSION}"`. Embed `_TIRITH_SHA256 = {target: digest}` in Hermes and compare the archive against that map. Keep `checksums.txt` only as an extra check, not the trust anchor.
- Make auto-download opt-in, e.g. `security.tirith_auto_install: false` by default or `TIRITH_AUTO_INSTALL=1`. Otherwise install from a package manager or a vendored artifact during deploy (`deploy/hermes-deploy.sh`) and set an explicit `tirith_path`.
- If downloading remains, treat missing cosign or a cosign execution error as an abort (`return None, "cosign_missing"`; the retry logic at `:64` and `:563` already expects this reason). Also pin `--certificate-identity` to the exact tag of the pinned version.
- Do not send `GITHUB_TOKEN` to release downloads. These are public assets, so drop the `Authorization` header in `_download_file`.

Effort: S · Confidence: high

---

## Remediation roadmap
1. **Immediately (days):**
   - F1: loopback bind, token, remove CORS.
   - F2: role dependency on admin-grade routes plus the BFF.
   - F3: `nosniff`, sandbox CSP, attachment by default.
2. **Short term (1-2 weeks):**
   - F4: guard resolves the real element, hub bound to the principal, redact password values.
   - F7 `/yolo` role check.
   - F8 `execFile` plus path allowlist.
   - F9 ref validation.
   - F5 no auto-merge from message content.
3. **Medium term:**
   - F6: untrusted memory tier.
   - F10: scanner ignores `.skillignore` for untrusted skills, fail-closed gate.
   - F11: isolate embeds, renderer CSP.
   - Write a route-level authorisation test that lists every FastAPI/Next route and asserts its role requirement.
4. **Hygiene:** F12 items; run dependency scanning (`pip-audit`, `npm audit`) in CI.

### Additions from the second pass
1. **Immediately:**
   - F13/F15: stop `chown -R hermes` over the whole checkout. Keep code root-owned and give `hermes` write access only to the data dirs.
   - Run the deploy with `git -c core.hooksPath=/dev/null -c core.fsmonitor=false`, `python -I`, and `npm ci --ignore-scripts` where possible.
   - F14: replace the `bash <checkout path>` sudoers rule with a root-owned copy in `/usr/local/sbin`, or a templated unit.
   - Audit `/etc/sudoers.d/` on the box for any path under `/opt/data/hermes-agent`.
2. **Short term:**
   - F16: move `EnvironmentFile`s outside the checkout as root-owned `0600`. Add `DATABASE_URL` and the other secrets to the terminal env blocklist. Rotate `AGENT_HOME_SESSION_SECRET` and the privileged DSN afterwards.
   - F17/F18: approval and trust controls must not be actionable through `app_act`, for example a `data-appmcp-deny` attribute plus a server-side deny-list.
   - F19: require a token on the loopback MCP endpoint.
   - F20: filter session search and reads by principal.
3. **Medium term:**
   - F21: strict media delivery by default.
   - F22: bind approvals to the requester.
   - F23: don't expand `@file:` refs from untrusted senders.
   - F24: `admin off` or a unix-socket admin endpoint for Caddy.
   - F25: pin the tirith version and its digest.

## Strengths observed
- **WhatsApp bridge:** binds to loopback only and validates the Host header against DNS rebinding (`bridge.js:580-603, 833`).
- **Credential mounts:** `credential_files.register_credential_file` rejects absolute and traversal paths and checks containment after resolving (`tools/credential_files.py:57-104`).
- **Database:** Postgres RLS uses `FORCE ROW LEVEL SECURITY` with per-principal read policies (`hermes_cli/access.py:655-666`).
- **agent-home session cookie:** HMAC-signed and compared in constant time (`session.ts:59-93`); `httpOnly`, `SameSite=Lax`, `Secure` in production.
- **Electron windows:** `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`.
- **Gateway:** group sessions are isolated per participant by default (`gateway/config.py:581`).
- **Inbound document cache:** strips directory components (`base.py:1556-1560`).
- **LLM proxy:** defaults to `127.0.0.1` and forwards only allowlisted paths (`hermes_cli/proxy/server.py:52, 112-119`).
- **Folder-bridge imports:** require a trusted folder or explicit approval (`folder-bridge/transport.ts:165-177`).
- **Skill scanner:** checks that symlinks stay inside the skill directory.
- **tirith binary:** checksum verified.
- **Activation/survey links:** sent with `no-referrer`/`no-store`.

## Appendix — coverage by area
| Area | Result |
|---|---|
| Gateway core / slash commands | F7; session isolation OK |
| Gateway platforms / inbound files | F3 (owner attribution, declared MIME) |
| WhatsApp bridge | F8; Host check OK |
| Dashboard API / auth | F2, F9, F12 (host check) |
| agent-home BFF | F2 (provider-key), F3, F12 (session, uploads) |
| app-mcp | F4 |
| custom/ triage services | F1, F5, F6, F12 (Telegram HTML) |
| Memory (builtin + supabase_pgvector) | F6, F12 (visibility) |
| Skills / hub | F10 |
| Plugins (image_gen etc.) | F12 |
| Desktop (Electron) | F11 |
| Security tooling (tirith, approvals, oneshot) | F12 |
| Proxy / credential files | No issues; strengths noted |
| Deploy scripts / systemd / sudoers | F13, F14, F15, F16, F24 |
| app-mcp / agent-home approvals | F17, F18, F19 |
| Session DB / search | F20 |
| ACP adapter / cron | No reportable issues. ACP V4A multi-file patches show the full patch for approval. Cron toolset fallback logs a warning (informational). |
