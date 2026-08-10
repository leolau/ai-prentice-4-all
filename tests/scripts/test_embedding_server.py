"""Request-boundary invariants for scripts/embedding_server.py (FG-21 P1).

The service is what makes memory recall semantic, and its dangerous failures are
not crashes — they are requests it *should* refuse but quietly serves:

  1. **A model mismatch.** The caller's `config.yaml` says `bge-m3`, the loaded
     model is something else. Serving that request writes vectors from another
     embedding space into a column of 1,024-dim `bge-m3` vectors, and nothing
     about the rows looks wrong afterwards — cosine ranking just degrades
     permanently.
  2. **Silent truncation.** A document longer than the model's window embeds to
     its first N characters. The row then claims to represent text it does not.
  3. **An unbounded batch** pinning all four shared vCPUs while the gateway is
     trying to answer a message.

The model is never loaded here — a stub holder stands in — so these run in
milliseconds without torch, while still driving the real HTTP handler over a
real socket.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "embedding_server.py"
_spec = importlib.util.spec_from_file_location("embedding_server", _MODULE_PATH)
assert _spec and _spec.loader
embedding_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embedding_server)


class _StubHolder:
    """A loaded model, without a model."""

    def __init__(self, *, model_id: str = "BAAI/bge-m3", dim: int = 8,
                 revision: Optional[str] = "abc123", loaded: bool = True) -> None:
        self.model_id = model_id
        self.dim = dim
        self.revision = revision
        self.loaded = loaded
        self.calls: List[Tuple[List[str], int]] = []

    def embed(self, texts: List[str], batch_size: int) -> List[List[float]]:
        self.calls.append((list(texts), batch_size))
        return [[0.5] * self.dim for _ in texts]


@pytest.fixture
def server(request):
    holder = getattr(request, "param", None) or _StubHolder()

    class Handler(embedding_server.Handler):
        pass

    Handler.holder = holder
    Handler.batch_size = 16
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}", holder
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(base: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    request = urllib.request.Request(
        f"{base}/embed",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(base: str, path: str) -> Tuple[int, Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_health_reports_the_pinned_model_and_width(server) -> None:
    base, _ = server
    status, body = _get(base, "/health")
    assert status == 200
    # The revision is what ties a stored vector to the weights that made it.
    assert body == {
        "ok": True,
        "model": "BAAI/bge-m3",
        "revision": "abc123",
        "dim": 8,
        "loaded": True,
    }


def test_embeds_and_echoes_the_model_it_used(server) -> None:
    base, holder = server
    status, body = _post(base, {"texts": ["招標截止日期", "tender deadline"]})
    assert status == 200
    assert [len(v) for v in body["embeddings"]] == [8, 8]
    assert body["model"] == "BAAI/bge-m3"
    assert body["revision"] == "abc123"
    assert holder.calls == [(["招標截止日期", "tender deadline"], 16)]


def test_a_mismatched_model_is_refused(server) -> None:
    """The corruption case. 409 tells the caller its config and the service
    disagree, instead of handing back vectors from the wrong space."""
    base, holder = server
    status, body = _post(base, {"texts": ["x"], "model": "intfloat/multilingual-e5-base"})
    assert status == 409
    assert "refusing to embed into a different space" in body["error"]
    assert body["model"] == "BAAI/bge-m3"
    assert holder.calls == [], "nothing may be embedded on a mismatch"


def test_matching_model_and_omitted_model_both_embed(server) -> None:
    base, _ = server
    assert _post(base, {"texts": ["x"], "model": "BAAI/bge-m3"})[0] == 200
    assert _post(base, {"texts": ["x"]})[0] == 200


def test_an_oversized_batch_is_refused_rather_than_pinning_the_cpus(server) -> None:
    base, holder = server
    status, body = _post(base, {"texts": ["x"] * (embedding_server.MAX_BATCH + 1)})
    assert status == 413
    assert str(embedding_server.MAX_BATCH) in body["error"]
    assert holder.calls == []


def test_an_overlong_text_is_refused_rather_than_truncated(server) -> None:
    base, holder = server
    long_text = "x" * (embedding_server.MAX_CHARS_PER_TEXT + 1)
    status, body = _post(base, {"texts": ["fine", long_text]})
    assert status == 413
    assert "[1]" in body["error"]  # names which text, so the caller can chunk it
    assert holder.calls == []


def test_an_empty_batch_is_a_no_op_not_an_error(server) -> None:
    base, holder = server
    status, body = _post(base, {"texts": []})
    assert status == 200
    assert body["embeddings"] == []
    assert holder.calls == []


def test_bad_input_shapes_are_rejected(server) -> None:
    base, _ = server
    assert _post(base, {"texts": "not a list"})[0] == 400
    assert _post(base, {"texts": [1, 2]})[0] == 400
    assert _post(base, {})[0] == 400


def test_unknown_paths_404(server) -> None:
    base, _ = server
    assert _get(base, "/metrics")[0] == 404
    request = urllib.request.Request(
        f"{base}/v1/embeddings",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 404


@pytest.mark.parametrize("server", [_StubHolder(loaded=False)], indirect=True)
def test_requests_during_model_load_get_503_not_a_wrong_answer(server) -> None:
    base, holder = server
    status, body = _post(base, {"texts": ["x"]})
    assert status == 503
    assert "still loading" in body["error"]
    assert holder.calls == []
