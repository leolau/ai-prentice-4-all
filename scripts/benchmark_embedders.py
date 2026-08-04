#!/usr/bin/env python3
"""Benchmark candidate embedders for the pgvector memory tier (FG-21 P1).

The model choice for a semantic memory layer is a latency/memory/quality trade
on the *specific machine* it will run on, so it should be measured there rather
than argued from model cards. This produces the numbers recorded in
`docs/deployment/local-embeddings.md` and FG-21 §7.5.

Two probes, because they answer different questions:

`recall` — a small bilingual corpus of tender/RFP material with decoys that
    share vocabulary but not meaning. Scores **recall@1** as well as recall@3,
    and flags *degenerate* queries whose every cosine distance is zero. That
    flag is the point: the incumbent hashing embedder tokenises `[a-z0-9]+`, so
    Chinese text embeds to the zero vector, every distance ties, and the
    "ranking" is just row order — which a recall@3 score on a small corpus
    reports as a hit.

`longdoc` — one long document with the answer at the very end, against a decoy
    and a padding-only document. This is what separates models whose recall
    scores tie: a model whose input window is shorter than the chunk cannot see
    the answer at all, and gives the padding-only document an identical score.

Usage (requires `sentence-transformers`; not a dependency of Hermes itself —
this is an operator tool run in the embedding service's own venv):

    benchmark_embedders.py recall  {hashing|bge|e5} [--cache-dir DIR]
    benchmark_embedders.py longdoc {bge|e5}         [--cache-dir DIR]

One model per process, so the reported RSS is that model's own footprint rather
than a peak shared with a previously loaded one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import resource
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

CANDIDATES = {
    "bge": ("BAAI/bge-m3", "", ""),
    # e5 REQUIRES the query:/passage: prefixes; omitting them measurably hurts
    # its recall, so benchmarking without them would be a rigged comparison.
    "e5": ("intfloat/multilingual-e5-base", "query: ", "passage: "),
}

# Realistic for this deployment: tender/RFP material in both languages, plus
# decoys that share vocabulary with the queries but not meaning.
CORPUS: List[str] = [
    "The Hospital Authority tender for imaging equipment closes on 30 September 2026.",
    "招標截止日期為二零二六年九月三十日，逾期投標不予受理。",
    "RFP response for the MTR signalling contract must be submitted by 15 October.",
    "政府採購投標書須於十月十五日前遞交，並附上公司註冊證明。",
    "Leo prefers proposals reviewed two weeks before any submission deadline.",
    "The office coffee machine tender was a joke someone made in a meeting.",
    "Quarterly revenue for the Snappop app grew 12% after the July release.",
    "強積金供款截止日期為每月十日。",
    "Invoice payment terms are net 30 from the date of the invoice.",
    "Kubernetes cluster upgrade is scheduled for the first weekend of November.",
]

#: (query, indices in CORPUS a correct semantic embedder must surface)
PROBES: List[Tuple[str, List[int]]] = [
    ("when is the tender due", [0, 1]),
    ("tender deadline", [0, 1]),
    ("招標截止日期", [1, 0]),
    ("RFP deadline", [2, 3]),
    ("投標書幾時要交", [3, 2]),
    ("how long do I have to pay an invoice", [8]),
]

LATENCY_TEXT = "find out when the next tender is due and remind me two weeks before"

_PAD = (
    "This document sets out general terms and conditions applicable to the "
    "procurement process, including definitions, interpretation, governing law, "
    "notices, assignment, severability, waiver, entire agreement, counterparts, "
    "confidentiality undertakings, and the parties' respective representations. "
)
_FACT = "The tender submission deadline is 30 September 2026 at 12:00 noon."
_DECOY = "The contract value cap is HK$4,500,000 excluding taxes."
LONGDOC_QUERY = "when is the tender submission deadline"
LONGDOC_CORPUS: List[Tuple[str, str]] = [
    ("long doc, fact at the END", (_PAD * 40) + _FACT),
    ("long doc, decoy fact at the END", (_PAD * 40) + _DECOY),
    ("padding only", _PAD * 40),
    ("short doc, fact at the START", _FACT + " " + (_PAD * 2)),
]


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _rank(
    query_vec: Sequence[float],
    corpus_vecs: Sequence[Sequence[float]],
    wanted: Sequence[int],
    k: int = 3,
) -> Tuple[float, float, List[int], bool]:
    """Return (hit@1, hit@k, top-k, degenerate).

    ``degenerate`` means every cosine distance was zero, so the order is
    arbitrary and any apparent hit is an artifact of row order rather than
    retrieval. Such a probe is scored as a miss, which is the honest reading.
    """
    scores = [_cosine(query_vec, vector) for vector in corpus_vecs]
    ranked = sorted(range(len(corpus_vecs)), key=lambda i: scores[i], reverse=True)
    top = ranked[:k]
    degenerate = all(abs(score) < 1e-12 for score in scores)
    hit1 = 1.0 if (ranked[0] in wanted and not degenerate) else 0.0
    hitk = 1.0 if (any(w in top for w in wanted) and not degenerate) else 0.0
    return hit1, hitk, top, degenerate


def _score(
    name: str,
    dim: int,
    load_s: float,
    rss_mb: float,
    singles_ms: List[float],
    batch16_ms: float,
    embed_query: Callable[[str], List[float]],
    corpus_vecs: Sequence[Sequence[float]],
) -> Dict[str, object]:
    at1: List[float] = []
    at3: List[float] = []
    detail: List[Dict[str, object]] = []
    for query, wanted in PROBES:
        hit1, hit3, top, degenerate = _rank(embed_query(query), corpus_vecs, wanted)
        at1.append(hit1)
        at3.append(hit3)
        detail.append(
            {
                "query": query,
                "hit_at_1": hit1,
                "hit_at_3": hit3,
                "top3": top,
                "wanted": list(wanted),
                "degenerate": degenerate,
            }
        )
    return {
        "model": name,
        "dim": dim,
        "load_s": round(load_s, 1),
        "rss_mb": round(rss_mb),
        "single_ms_p50": round(statistics.median(singles_ms), 2),
        "single_ms_p95": round(sorted(singles_ms)[-1], 2),
        "batch16_ms": round(batch16_ms, 1),
        "recall_at_1": round(sum(at1) / len(at1), 3),
        "recall_at_3": round(sum(at3) / len(at3), 3),
        "degenerate_queries": sum(1 for d in detail if d["degenerate"]),
        "probes": detail,
    }


def bench_hashing(repo_root: Path) -> Dict[str, object]:
    """The incumbent, loaded by path.

    Importing it as a package member drags in the provider's asyncpg/datastore
    imports, which the embedding service's venv deliberately does not have.
    """
    path = repo_root / "plugins/memory/supabase_pgvector/embedding.py"
    spec = importlib.util.spec_from_file_location("hashing_embedding", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    embedder = module.HashingEmbedder(dim=module.DEFAULT_DIM)

    singles: List[float] = []
    for _ in range(20):
        start = time.perf_counter()
        embedder.embed(LATENCY_TEXT)
        singles.append((time.perf_counter() - start) * 1000)
    start = time.perf_counter()
    for text in (CORPUS * 2)[:16]:
        embedder.embed(text)
    batch_ms = (time.perf_counter() - start) * 1000

    return _score(
        "hashing (incumbent)",
        embedder.dim,
        0.0,
        0.0,
        singles,
        batch_ms,
        embedder.embed,
        [embedder.embed(text) for text in CORPUS],
    )


def _load(repo: str, cache_dir: str | None):
    from sentence_transformers import SentenceTransformer

    kwargs: Dict[str, object] = {"device": "cpu"}
    if cache_dir:
        kwargs["cache_folder"] = cache_dir
    return SentenceTransformer(repo, **kwargs)


def bench_model(key: str, cache_dir: str | None) -> Dict[str, object]:
    repo, prefix_query, prefix_doc = CANDIDATES[key]
    base_rss = _rss_mb()
    start = time.perf_counter()
    model = _load(repo, cache_dir)
    load_s = time.perf_counter() - start
    dim = int(model.get_sentence_embedding_dimension())

    model.encode([LATENCY_TEXT])  # warm up: exclude first-pass kernel allocation

    singles: List[float] = []
    for _ in range(10):
        started = time.perf_counter()
        model.encode([prefix_query + LATENCY_TEXT])
        singles.append((time.perf_counter() - started) * 1000)

    batch = [prefix_doc + text for text in (CORPUS * 2)[:16]]
    started = time.perf_counter()
    model.encode(batch, batch_size=16)
    batch_ms = (time.perf_counter() - started) * 1000

    corpus_vecs = model.encode([prefix_doc + t for t in CORPUS]).tolist()

    def embed_query(text: str) -> List[float]:
        return model.encode([prefix_query + text])[0].tolist()

    return _score(repo, dim, load_s, _rss_mb() - base_rss, singles, batch_ms,
                  embed_query, corpus_vecs)


def bench_longdoc(key: str, cache_dir: str | None) -> Dict[str, object]:
    repo, prefix_query, prefix_doc = CANDIDATES[key]
    model = _load(repo, cache_dir)
    vectors = model.encode(
        [prefix_doc + text for _, text in LONGDOC_CORPUS],
        batch_size=4,
        normalize_embeddings=True,
    ).tolist()
    query_vec = model.encode(
        [prefix_query + LONGDOC_QUERY], normalize_embeddings=True
    )[0].tolist()
    ranking = sorted(
        (
            (round(_cosine(query_vec, vector), 4), LONGDOC_CORPUS[i][0])
            for i, vector in enumerate(vectors)
        ),
        reverse=True,
    )
    labels = [label for _, label in ranking]
    return {
        "model": repo,
        "max_seq_length": int(getattr(model, "max_seq_length", -1)),
        "chunk_chars": len(LONGDOC_CORPUS[0][1]),
        "ranking": ranking,
        "fact_at_end_rank": labels.index("long doc, fact at the END") + 1,
        # Identical scores across these three mean the model truncated before
        # reaching the answer: boilerplate and answer are the same vector to it.
        "truncation_detected": (
            ranking[1][0] == ranking[2][0] == ranking[3][0]
        ),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", choices=["recall", "longdoc"])
    parser.add_argument("target", choices=["hashing", *CANDIDATES])
    parser.add_argument("--cache-dir", default=None, help="Model cache directory.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Hermes checkout, for loading the incumbent hashing embedder.",
    )
    args = parser.parse_args(argv)

    if args.probe == "longdoc":
        if args.target == "hashing":
            parser.error("the longdoc probe measures input windows; hashing has none")
        result = bench_longdoc(args.target, args.cache_dir)
    elif args.target == "hashing":
        result = bench_hashing(args.repo_root)
    else:
        result = bench_model(args.target, args.cache_dir)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
