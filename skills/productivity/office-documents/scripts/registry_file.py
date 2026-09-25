#!/usr/bin/env python3
"""Move files between the Hermes box and the Files registry (file_assets).

Run from the hermes-agent checkout with its venv, e.g.
    cd /opt/data/hermes-agent && .venv/bin/python skills/productivity/office-documents/scripts/registry_file.py ...

    registry_file.py list  --as leo_owner [--query invoice] [--surface agent_home] [--limit 50]
    registry_file.py get   --as leo_owner ASSET_ID [--out DIR_OR_FILE]      # download bytes, verify sha256
    registry_file.py put   --as leo_owner PATH [--conversation "label"]     # upload + register, verify sha256

`get` refuses to keep a download whose sha256 differs from the registry row.
`put` registers under surface "agent_home" so the file shows up on /files,
and re-downloads the object to prove the bytes landed intact.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SURFACE = "agent_home"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _context(args: argparse.Namespace):
    from hermes_cli.access import PrincipalStore
    from hermes_cli.config import load_config
    from hermes_cli.datastore import get_store
    from hermes_cli.file_registry import default_registry
    from hermes_cli.filestore import SupabaseStorage

    config = load_config() or {}
    mode = args.mode
    app_store = get_store("supabase-app", mode, config=config) if mode else get_store("supabase-app")
    principal = await PrincipalStore(app_store).get(args.acting_as)
    if principal is None:
        raise SystemExit(f"No principal {args.acting_as!r}. Run 'hermes member list'.")
    registry = default_registry(mode)
    await registry.initialize()
    return principal, registry, SupabaseStorage.from_env()


async def cmd_list(args: argparse.Namespace) -> None:
    principal, registry, _ = await _context(args)
    surfaces = (args.surface,) if args.surface else ()
    assets, total = await registry.list(
        principal, query=args.query or "", surfaces=surfaces, limit=args.limit
    )
    rows = [
        {
            "id": a.id,
            "filename": a.filename,
            "bytes": a.byte_size,
            "content_type": a.content_type,
            "surface": a.surface,
            "conversation": a.conversation,
            "received_at": a.received_at.isoformat() if a.received_at else None,
            "sha256": a.sha256,
            "remembered": a.remembered,
        }
        for a in assets
    ]
    print(json.dumps({"total": total, "files": rows}, indent=2, ensure_ascii=False))


async def cmd_get(args: argparse.Namespace) -> None:
    principal, registry, storage = await _context(args)
    asset = await registry.get(principal, args.asset_id)
    if asset is None:
        raise SystemExit(f"No file asset {args.asset_id} visible to {args.acting_as}.")
    data = await storage.download(asset.storage_path)
    digest = _sha256(data)
    if digest != asset.sha256:
        raise SystemExit(
            f"sha256 mismatch for {asset.filename}: registry {asset.sha256}, downloaded {digest}"
        )
    out = Path(args.out) if args.out else Path.cwd()
    if out.is_dir() or args.out is None or args.out.endswith(os.sep):
        out.mkdir(parents=True, exist_ok=True)
        out = out / asset.filename
    out.write_bytes(data)
    print(
        json.dumps(
            {"asset_id": asset.id, "path": str(out), "bytes": len(data), "sha256": digest, "verified": True}
        )
    )


async def cmd_put(args: argparse.Namespace) -> None:
    from hermes_cli.file_registry import MAX_REGISTER_BYTES, store_and_register

    principal, registry, storage = await _context(args)
    path = Path(args.path)
    data = path.read_bytes()
    if not data:
        raise SystemExit(f"{path} is empty")
    if len(data) > MAX_REGISTER_BYTES:
        raise SystemExit(f"{path} is {len(data)} bytes; the registry cap is {MAX_REGISTER_BYTES}")
    content_type = args.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    asset = await store_and_register(
        principal,
        data,
        surface=SURFACE,
        filename=path.name,
        content_type=content_type,
        conversation=args.conversation,
        sender_id=principal.user_id,
        sender_name="hermes",
        registry=registry,
        storage=storage,
    )
    if asset is None:
        raise SystemExit("upload/registration failed (see agent log for the warning)")
    echoed = await storage.download(asset.storage_path)
    verified = _sha256(echoed) == asset.sha256 == _sha256(data)
    print(
        json.dumps(
            {
                "asset_id": asset.id,
                "filename": asset.filename,
                "bytes": asset.byte_size,
                "sha256": asset.sha256,
                "storage_path": asset.storage_path,
                "verified": verified,
            }
        )
    )
    if not verified:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as", dest="acting_as", required=True, help="principal user_id, e.g. leo_owner")
    p.add_argument("--mode", choices=["dev", "prod"], default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list")
    s.add_argument("--query"); s.add_argument("--surface"); s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("get"); s.add_argument("asset_id"); s.add_argument("--out")
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("put"); s.add_argument("path")
    s.add_argument("--conversation", help="label shown on /files (e.g. 'invoices/2026-09 summary')")
    s.add_argument("--content-type")
    s.set_defaults(fn=cmd_put)

    args = p.parse_args(argv)
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
