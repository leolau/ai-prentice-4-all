"""Deterministic, dependency-free text embeddings for the live memory tier.

The Supabase/pgvector provider stores an embedding vector alongside every
memory row so recall can be *semantic* (nearest-neighbour by meaning) rather
than a substring match. Embedding quality is pluggable, but the default must
work with **zero credentials and no network** so the provider is usable out of
the box and its tests are hermetic.

:class:`HashingEmbedder` hashes tokens into a fixed-dimension bag-of-words
vector and L2-normalises it. Two texts that share vocabulary land close under
cosine distance; disjoint texts are near-orthogonal. It is fully deterministic
(``sha256`` per token, not an RNG) so the same text always embeds identically
across processes and platforms — a property the round-trip and concurrency
tests rely on.

:class:`LocalHttpEmbedder` is the semantic option: it POSTs to an embedding
service on loopback (FG-21's ``hermes-embed.service``), so the text is embedded
by a real model without leaving the deployment. Select it from ``config.yaml``::

    memory:
      embedding:
        provider: local_http
        endpoint: http://127.0.0.1:8791
        model: BAAI/bge-m3
        dim: 1024

Nothing else in the store changes, because everything speaks ``list[float]``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

#: Embedding width. Small enough to keep rows cheap, wide enough that hashed
#: tokens rarely collide for realistic memory entries.
DEFAULT_DIM = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """Turns text into a fixed-length embedding vector."""

    @property
    def dim(self) -> int:
        """Dimension of every vector this embedder produces."""
        ...

    def embed(self, text: str) -> List[float]:
        """Return the embedding for ``text`` (length == :attr:`dim`)."""
        ...


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class HashingEmbedder:
    """Deterministic hashing embedder — the credential-free default.

    Tokens are lower-cased alphanumerics; each hashes to one dimension with a
    sign bit, the accumulated vector is L2-normalised. Empty / token-less text
    yields the zero vector (cosine-undefined but stored harmlessly).
    """

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if dim <= 0:
            raise ValueError(f"Embedding dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self._dim
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(component * component for component in vec))
        if norm > 0.0:
            vec = [component / norm for component in vec]
        return vec


class EmbeddingServiceError(RuntimeError):
    """The configured embedding service could not produce a vector.

    Raised rather than silently substituting a different embedder: two
    embedding spaces in one column make cosine distance meaningless, so recall
    would degrade quietly and permanently. A failed write is recoverable; a
    corrupted vector column is not.
    """


class LocalHttpEmbedder:
    """Embeds text via an HTTP embedding service, normally on loopback.

    Wire format (kept deliberately trivial so any small server satisfies it)::

        POST <endpoint>/embed  {"texts": ["..."], "model": "..."}
        200                    {"embeddings": [[0.01, ...]], "dim": 1024}

    The configured ``dim`` is enforced on every response. A service that was
    restarted onto a different model returns vectors of a different width, and
    catching that here turns a silent ranking corruption into a loud, logged
    failure at the moment it starts.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        dim: int,
        timeout_seconds: float = 20.0,
    ) -> None:
        if dim <= 0:
            raise ValueError(f"Embedding dim must be positive, got {dim}")
        if not str(endpoint or "").strip():
            raise ValueError("Embedding endpoint is required for local_http")
        self._endpoint = str(endpoint).rstrip("/")
        self._model = str(model or "")
        self._dim = dim
        self._timeout = float(timeout_seconds)

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model(self) -> str:
        return self._model

    def embed(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed several texts in one request (one model forward pass)."""
        if not texts:
            return []
        payload = json.dumps(
            {"texts": list(texts), "model": self._model}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._endpoint}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EmbeddingServiceError(
                f"embedding service at {self._endpoint} is unreachable or "
                f"returned a non-JSON body: {exc}"
            ) from exc

        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingServiceError(
                f"embedding service returned {type(vectors).__name__} for "
                f"{len(texts)} texts; expected a list of {len(texts)} vectors"
            )
        out: List[List[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self._dim:
                got = len(vector) if isinstance(vector, list) else "non-list"
                raise EmbeddingServiceError(
                    f"embedding service returned width {got}, but this store's "
                    f"column is vector({self._dim}) — refusing to mix embedding "
                    f"spaces (is the service running a different model?)"
                )
            out.append([float(component) for component in vector])
        return out


def _embedding_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    memory = (config or {}).get("memory")
    if not isinstance(memory, dict):
        return {}
    settings = memory.get("embedding")
    return settings if isinstance(settings, dict) else {}


def get_embedder(
    dim: int = DEFAULT_DIM,
    config: Optional[Dict[str, Any]] = None,
) -> Embedder:
    """Return the embedder selected by ``memory.embedding`` in ``config.yaml``.

    Defaults to :class:`HashingEmbedder` so a fresh install and the test suite
    need no service, no model download and no credentials. ``provider:
    local_http`` switches to a real model over loopback; an unknown provider is
    a configuration error rather than a silent downgrade, because a deployment
    that believes it has semantic recall and does not is worse than one that
    fails at startup.
    """
    settings = _embedding_settings(config)
    provider = str(settings.get("provider") or "hashing").strip().lower()
    if provider in ("", "hashing", "none"):
        return HashingEmbedder(dim=int(settings.get("dim") or dim))
    if provider == "local_http":
        return LocalHttpEmbedder(
            endpoint=str(settings.get("endpoint") or "http://127.0.0.1:8791"),
            model=str(settings.get("model") or ""),
            dim=int(settings.get("dim") or 0),
            timeout_seconds=float(settings.get("timeout_seconds") or 20.0),
        )
    raise ValueError(
        f"Unknown memory.embedding.provider {provider!r}. "
        "Expected 'hashing' or 'local_http'."
    )
