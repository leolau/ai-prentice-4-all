#!/usr/bin/env python3
"""Loopback embedding service — the semantic half of FG-21's memory layer 4.

The pgvector memory tier's default embedder hashes tokens into a bag-of-words
vector, so nearest-neighbour recall is vocabulary overlap, not meaning: "when is
the tender due" retrieves neither "RFP deadline" nor 招標截止日期. This service
runs a real sentence-embedding model **on the deployment**, so memory text is
never sent to a third party to be embedded.

It is deliberately tiny and dependency-light (stdlib HTTP + one model library):

    POST /embed   {"texts": ["..."], "model": "<optional, must match>"}
    ->            {"embeddings": [[...]], "dim": 1024, "model": "BAAI/bge-m3"}
    GET  /health  {"ok": true, "model": ..., "dim": ..., "loaded": true}

Design constraints that are not incidental:

* **Loopback by default.** Binding 127.0.0.1 keeps the model off the network
  even if a firewall rule is wrong; there is no auth here precisely because
  there is no remote listener to authenticate.
* **The model is pinned by revision.** ``--revision`` is passed to the loader
  and reported by ``/health`` and ``/embed``, so a vector column can be tied to
  the exact weights that produced it. A model swap must be a deliberate act
  with a re-embed, never a silent consequence of a restart pulling new weights.
* **A mismatched ``model`` in the request is refused**, not quietly honoured.
  The client's configured model and the loaded model disagreeing means one of
  them is wrong, and writing vectors from the wrong space into a shared column
  corrupts ranking for every row that follows.
* **Offline after the first load.** With the weights already in the cache the
  process needs no egress, and ``--offline`` enforces that (set
  ``HF_HUB_OFFLINE``) so a service restart can never fetch new weights.
* **Single worker, bounded batch.** The box shares 4 vCPUs with the gateway;
  serialising forward passes and capping batch size keeps a large ingestion
  from starving live chat.

Usage:

    embedding_server.py --model BAAI/bge-m3 --revision <sha> --port 8791
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.embed")

#: Hard ceiling on one request, independent of the client's configured batch
#: size: a runaway caller must not be able to pin all four cores for minutes.
MAX_BATCH = 64

#: Requests that exceed this are rejected rather than truncated — a silently
#: truncated document embeds to something that is not what the caller stored.
MAX_CHARS_PER_TEXT = 8192


class ModelHolder:
    """Loads the model once and serialises access to it.

    ``SentenceTransformer.encode`` is not guaranteed thread-safe and, on a
    4-vCPU box shared with the gateway, parallel forward passes would fight for
    the same cores anyway. One lock keeps latency predictable.
    """

    def __init__(self, model_id: str, revision: Optional[str], cache_dir: Optional[str]) -> None:
        self._model_id = model_id
        self._revision = revision
        self._cache_dir = cache_dir
        self._lock = threading.Lock()
        self._model: Any = None
        self._dim = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def revision(self) -> Optional[str]:
        return self._revision

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        from sentence_transformers import SentenceTransformer

        kwargs: Dict[str, Any] = {"device": "cpu"}
        if self._cache_dir:
            kwargs["cache_folder"] = self._cache_dir
        if self._revision:
            kwargs["revision"] = self._revision
        logger.info("loading %s (revision=%s)", self._model_id, self._revision or "default")
        self._model = SentenceTransformer(self._model_id, **kwargs)
        self._dim = int(self._model.get_sentence_embedding_dimension())
        logger.info("loaded %s dim=%d", self._model_id, self._dim)

    def embed(self, texts: List[str], batch_size: int) -> List[List[float]]:
        with self._lock:
            vectors = self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return [[float(component) for component in vector] for vector in vectors]


class Handler(BaseHTTPRequestHandler):
    server_version = "hermes-embed/1.0"
    holder: ModelHolder
    batch_size: int = 16

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Route through logging so systemd's StandardOutput captures it in the
        # same file as everything else, instead of stderr's own format.
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", ""):
            self._send(
                200,
                {
                    "ok": self.holder.loaded,
                    "model": self.holder.model_id,
                    "revision": self.holder.revision,
                    "dim": self.holder.dim,
                    "loaded": self.holder.loaded,
                },
            )
            return
        self._send(404, {"error": f"no such path: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/embed":
            self._send(404, {"error": f"no such path: {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError) as exc:
            self._send(400, {"error": f"invalid JSON body: {exc}"})
            return

        texts = body.get("texts")
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            self._send(400, {"error": "'texts' must be a list of strings"})
            return
        if not texts:
            self._send(200, {"embeddings": [], "dim": self.holder.dim,
                             "model": self.holder.model_id})
            return
        if len(texts) > MAX_BATCH:
            self._send(413, {"error": f"{len(texts)} texts exceeds the {MAX_BATCH} limit"})
            return
        too_long = [i for i, t in enumerate(texts) if len(t) > MAX_CHARS_PER_TEXT]
        if too_long:
            self._send(
                413,
                {
                    "error": (
                        f"texts at {too_long} exceed {MAX_CHARS_PER_TEXT} chars; "
                        "chunk before embedding rather than letting the model "
                        "silently truncate"
                    )
                },
            )
            return

        requested = str(body.get("model") or "").strip()
        if requested and requested != self.holder.model_id:
            # Refusing is the whole point: honouring it would write vectors from
            # a different embedding space into the caller's column.
            self._send(
                409,
                {
                    "error": (
                        f"this service is running {self.holder.model_id!r}, but the "
                        f"caller asked for {requested!r} — refusing to embed into a "
                        "different space"
                    ),
                    "model": self.holder.model_id,
                },
            )
            return

        if not self.holder.loaded:
            self._send(503, {"error": "model is still loading"})
            return
        try:
            vectors = self.holder.embed(texts, self.batch_size)
        except Exception as exc:  # pragma: no cover - model/runtime dependent
            logger.exception("embedding failed")
            self._send(500, {"error": f"embedding failed: {exc}"})
            return
        self._send(
            200,
            {
                "embeddings": vectors,
                "dim": self.holder.dim,
                "model": self.holder.model_id,
                "revision": self.holder.revision,
            },
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument(
        "--revision",
        default=None,
        help="Pin the model revision (commit sha). Strongly recommended: an "
             "unpinned model can change under a restart, invalidating every "
             "stored vector's comparability.",
    )
    parser.add_argument("--cache-dir", default=None, help="Model cache directory.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Refuse any hub network access (sets HF_HUB_OFFLINE). Use once the "
             "weights are cached, so a restart cannot pull different weights.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    holder = ModelHolder(args.model, args.revision, args.cache_dir)
    try:
        holder.load()
    except Exception as exc:
        logger.error("could not load %s: %s", args.model, exc)
        return 1

    Handler.holder = holder
    Handler.batch_size = max(1, min(args.batch_size, MAX_BATCH))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("serving on http://%s:%d (batch_size=%d)", args.host, args.port,
                Handler.batch_size)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
