"""Config-selected embedders for the pgvector memory tier (FG-21 P1).

These run against a **real** loopback HTTP server rather than a mocked
``urlopen``: the failure modes that matter here are wire-level (a service that
returns the wrong vector width because it was restarted onto another model, a
service that is simply down), and a mock that returns whatever the test says it
returns cannot demonstrate that the client detects them.

No model is loaded — the stub server returns fixed-width vectors, so the suite
stays hermetic and fast while still exercising the real HTTP path.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from plugins.memory.supabase_pgvector.embedding import (
    EmbeddingServiceError,
    HashingEmbedder,
    LocalHttpEmbedder,
    get_embedder,
)


class _StubEmbedServer:
    """Minimal stand-in for ``hermes-embed.service``."""

    def __init__(self, *, dim: int, status: int = 200, body: Optional[Dict[str, Any]] = None):
        self.dim = dim
        self.status = status
        self.body = body
        self.requests: List[Dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                stub.requests.append(payload)
                if stub.body is not None:
                    out = stub.body
                else:
                    texts = payload.get("texts") or []
                    out = {
                        "embeddings": [[0.1] * stub.dim for _ in texts],
                        "dim": stub.dim,
                    }
                data = json.dumps(out).encode("utf-8")
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "_StubEmbedServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---------------------------------------------------------------------------
# Provider selection from config.yaml
# ---------------------------------------------------------------------------

def test_default_is_the_credential_free_hashing_embedder() -> None:
    """A fresh install must work with no service, no model and no network."""
    for config in ({}, {"memory": {}}, {"memory": {"embedding": {}}}):
        embedder = get_embedder(config=config)
        assert isinstance(embedder, HashingEmbedder)
        assert embedder.dim == 256


def test_local_http_is_selected_and_configured_from_config() -> None:
    embedder = get_embedder(
        config={
            "memory": {
                "embedding": {
                    "provider": "local_http",
                    "endpoint": "http://127.0.0.1:9999/",
                    "model": "BAAI/bge-m3",
                    "dim": 1024,
                    "timeout_seconds": 5,
                }
            }
        }
    )
    assert isinstance(embedder, LocalHttpEmbedder)
    assert embedder.dim == 1024
    assert embedder.model == "BAAI/bge-m3"


def test_unknown_provider_fails_loudly_rather_than_downgrading() -> None:
    """Silently falling back would leave a deployment believing it has
    semantic recall while it is still hashing bag-of-words."""
    with pytest.raises(ValueError, match="Unknown memory.embedding.provider"):
        get_embedder(config={"memory": {"embedding": {"provider": "openai"}}})


def test_local_http_requires_a_positive_dim_and_an_endpoint() -> None:
    with pytest.raises(ValueError, match="dim must be positive"):
        get_embedder(
            config={"memory": {"embedding": {"provider": "local_http", "dim": 0}}}
        )
    with pytest.raises(ValueError, match="endpoint is required"):
        LocalHttpEmbedder(endpoint="  ", model="m", dim=8)


# ---------------------------------------------------------------------------
# Real HTTP behaviour
# ---------------------------------------------------------------------------

def test_embeds_over_http_and_sends_the_configured_model() -> None:
    with _StubEmbedServer(dim=8) as server:
        embedder = LocalHttpEmbedder(
            endpoint=server.endpoint, model="BAAI/bge-m3", dim=8
        )
        vector = embedder.embed("when is the tender due")
        assert len(vector) == 8
        assert all(isinstance(component, float) for component in vector)
        # The model travels with the request so the service can refuse to embed
        # into a space the caller did not ask for.
        assert server.requests[-1]["model"] == "BAAI/bge-m3"
        assert server.requests[-1]["texts"] == ["when is the tender due"]


def test_batch_embeds_in_one_request() -> None:
    """Ingestion is the expensive path; N texts must cost one forward pass."""
    with _StubEmbedServer(dim=4) as server:
        embedder = LocalHttpEmbedder(endpoint=server.endpoint, model="m", dim=4)
        vectors = embedder.embed_batch(["a", "b", "c"])
        assert [len(v) for v in vectors] == [4, 4, 4]
        assert len(server.requests) == 1

        assert embedder.embed_batch([]) == []
        assert len(server.requests) == 1, "an empty batch must not hit the network"


def test_a_different_vector_width_is_refused_not_stored() -> None:
    """The silent-corruption case: the service came back on another model.

    Mixing two embedding spaces in one column makes cosine distance meaningless
    for every row, and nothing about the data looks wrong afterwards — so this
    has to fail at the boundary, loudly.
    """
    with _StubEmbedServer(dim=768) as server:
        embedder = LocalHttpEmbedder(endpoint=server.endpoint, model="m", dim=1024)
        with pytest.raises(EmbeddingServiceError, match="refusing to mix embedding"):
            embedder.embed("hello")


def test_a_short_response_is_refused() -> None:
    with _StubEmbedServer(dim=4, body={"embeddings": [[0.1, 0.2, 0.3, 0.4]]}) as server:
        embedder = LocalHttpEmbedder(endpoint=server.endpoint, model="m", dim=4)
        with pytest.raises(EmbeddingServiceError, match="expected a list of 2 vectors"):
            embedder.embed_batch(["one", "two"])


def test_an_unreachable_service_raises_instead_of_substituting_vectors() -> None:
    """Down service => failed write, never a hashing vector in a model column."""
    with _StubEmbedServer(dim=8) as server:
        endpoint = server.endpoint  # captured, then the server is shut down
    embedder = LocalHttpEmbedder(
        endpoint=endpoint, model="m", dim=8, timeout_seconds=2
    )
    with pytest.raises(EmbeddingServiceError, match="unreachable"):
        embedder.embed("hello")


def test_a_non_json_error_body_is_reported_as_a_service_error() -> None:
    with _StubEmbedServer(dim=8, status=500, body={"error": "boom"}) as server:
        embedder = LocalHttpEmbedder(endpoint=server.endpoint, model="m", dim=8)
        with pytest.raises(EmbeddingServiceError):
            embedder.embed("hello")
