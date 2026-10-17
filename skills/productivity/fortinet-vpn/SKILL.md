---
name: fortinet-vpn
description: "hermes vpn CLI: connect a configured Fortinet VPN and read files from the office file share (read-only) under /opt/data/vpn-files; network folders through folder_bridge_* tools."
version: 1.1.0
author: core
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [VPN, Fortinet, Files, Network, CLI]
---

# Fortinet VPN (Private networks)

Some of the user's files live on an office file server that is only reachable
over their Fortinet VPN. The user sets each VPN up in agent-home
**Settings → In&Out → Private networks (VPN)**. Once a VPN is connected, its
file share is mounted **read-only** at `/opt/data/vpn-files/<slug>` and you read
it with the normal file tools (`read_file`, `search_files`, `terminal` with
`ls`/`find`/`cat`).

## When to use

- The user asks about a file or folder that is "on the office server", "on the
  share", "on the S: drive", "in the company files", or under a path such as
  `\\fileserver01\Shared\...`.
- A task, card or cron job says the input files are behind a VPN.

## Network folders (Folder Bridge)

The user may also have saved **network folders** in Files → Bridge: one
folder on an office file server, reached through one of their VPNs. Use the
Folder Bridge tools for those, not `hermes vpn`:

- `folder_bridge_list_folders` lists them with `kind: "network"` and ids
  `net-…` (they work with no browser connected).
- `folder_bridge_list_directory`, `folder_bridge_search_files`,
  `folder_bridge_read_file`, `folder_bridge_get_file_metadata` with that id
  connect the VPN and mount the folder on first use (read-only).
- `folder_bridge_import_file` copies a file into Files when the folder is
  "trusted for import"; otherwise use `folder_bridge_import_file_approved` so
  the user approves it in chat.

An error such as `VPN connect failed: …`, `File share login failed …` or
`… is outside the VPN routes …` means the folder or VPN settings need fixing
in Files → Bridge or Settings → In&Out; tell the user, don't retry.

## Commands

```
hermes vpn list                 # every configured VPN: name, state, mount path
hermes vpn status ["<name>"]    # one VPN, with the last file-share check
hermes vpn up "<name>"          # connect (if the VPN allows agents to connect)
hermes vpn test "<name>"        # connect if needed, then list the share
hermes vpn down "<name>"        # disconnect and unmount
```

Add `--json` for machine-readable output. Each command prints one line, e.g.

```
Office files: connected — files at /opt/data/vpn-files/office-files (read-only)
Office files: error — Login failed: wrong username or password
```

`up` and `test` exit non-zero when the VPN is not connected afterwards.

## Workflow

1. `hermes vpn list` to find the VPN name (quote names with spaces).
2. `hermes vpn up "<name>"`. If the line says `connected`, the files are under
   the printed mount path.
3. Map the user's path onto the mount: `\\fileserver01\Shared\Reports\Q3.xlsx`
   with share `//fileserver01/Shared` becomes
   `/opt/data/vpn-files/<slug>/Reports/Q3.xlsx`. List the folder first when
   unsure of the exact name.
4. Read what you need. Copy a file to your workspace if you have to convert or
   edit it; never try to write to the mount (it is read-only by design).
5. `hermes vpn down "<name>"` when you are finished. If you forget, it
   disconnects itself after the configured idle time (default 30 minutes).

## Rules

- Never try to read, print or guess the VPN or file-share password. They are
  not available to your terminal, and searching credential stores, `/run`,
  `/etc` or process lists for them is off limits.
- Don't add routes, edit network settings, or run `openfortivpn`, `mount` or
  `sudo` yourself. `hermes vpn` is the only supported way in.
- Don't connect a VPN that the task doesn't need.

## When something goes wrong

Tell the user plainly what happened, and what they can do in
Settings → In&Out. Don't keep retrying.

| Output | Tell the user |
|---|---|
| `Connect '<name>' in Settings → In&Out, then tell me.` | This VPN is set so only the user can connect it. Ask them to connect it, then continue. |
| `needs_cert — … certificate is not trusted yet` | The VPN server's certificate needs their approval. Ask them to review and trust it in Settings → In&Out. |
| `error — Login failed: wrong username or password` | The saved VPN login was rejected; they should update it in Settings → In&Out. |
| `error — … one-time code (FortiToken) …` | This VPN needs a FortiToken code, which Hermes can't enter yet. |
| `error — Can't reach the VPN server …` / `Can't find the VPN server …` | The VPN server is offline or the address is wrong. |
| `error — File share login failed …` / `Share … was not found` | The VPN works but the file-share login or path is wrong; they can fix it in the VPN's settings. |
| `error — VPN support is not installed on this server …` | VPN support hasn't been set up on this server yet; the operator must install it. |
| `No VPNs are configured.` | Ask the user to add the VPN in Settings → In&Out. |
