# Local semantic embeddings — `hermes-embed.service`

FG-21 layer 4 (the pgvector memory tier) ships with a **hashing** embedder: it
hashes `[a-z0-9]+` tokens into a 256-dim bag-of-words vector. That is the right
default — no credentials, no model, no network, hermetic tests — but it is not
semantic, and on this deployment's material it is worse than it looks. Measured
on the box (see "Benchmark" below):

```
                              recall@1   Chinese queries
hashing (incumbent)             0.167    zero vector — no ranking at all
BAAI/bge-m3                     1.000    ranked first
intfloat/multilingual-e5-base   1.000    ranked first
```

The Chinese row is the important one. `[a-z0-9]+` matches nothing in
`招標截止日期`, so the query embeds to all zeros, every cosine distance is 0, and
the "nearest neighbours" are whatever order the rows came back in. Any apparent
hit is the corpus ordering, not retrieval.

This service replaces that with a real model **running on the deployment**, so
memory text is never sent to a third party to be embedded.

## What is installed

```
/opt/data/hermes-embed/venv        torch (CPU) + sentence-transformers
/opt/data/hermes-embed/models      the weight cache (~4.3 GB for both candidates)
/etc/systemd/system/hermes-embed.service
```

The service code itself is **in git** — `scripts/embedding_server.py` — so it is
deployed by the normal deploy path rather than living only on the disk. The venv
and weights are not in git: they are 4 GB of third-party binaries, and a cold
rebuild re-creates them with the documented commands below.

## The unit

The unit is a tracked file — `deploy/hermes-embed.service` — installed from git
rather than typed into `/etc/systemd/system/` by hand:

```bash
install -m 644 deploy/hermes-embed.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now hermes-embed
```

```ini
[Unit]
Description=Hermes local embedding service (loopback only)
After=network.target

[Service]
Type=simple
User=hermes
Group=hermes
WorkingDirectory=/opt/data/hermes-embed
ExecStart=/opt/data/hermes-embed/venv/bin/python \
    /opt/data/hermes-agent/scripts/embedding_server.py \
    --model BAAI/bge-m3 \
    --revision 5617a9f61b028005a4858fdac845db406aefb181 \
    --cache-dir /opt/data/hermes-embed/models \
    --host 127.0.0.1 --port 8791 --batch-size 16 --offline
Restart=on-failure
RestartSec=5

# Hardening. The model is third-party code executing on the box; it gets a
# writable model cache and nothing else.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/data/hermes-embed
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictAddressFamilies=AF_INET AF_UNIX
MemoryMax=3G
CPUWeight=50

[Install]
WantedBy=multi-user.target
```

Each of those lines is doing a job:

- **`--host 127.0.0.1`** — the listener is loopback, so there is no remote
  surface to authenticate. That is why the service has no auth: adding a token
  would imply it is reachable, which it must not be.
- **`--revision <sha>`** — the weights are pinned. An unpinned model can change
  under a restart, and every vector already in the column was produced by the
  old weights; comparing across them silently degrades ranking. A model change
  must be a deliberate act with a re-embed.
- **`--offline`** — sets `HF_HUB_OFFLINE`, so once the weights are cached the
  process needs no egress and cannot fetch different ones.
- **`User=hermes`** — same unprivileged user as everything else Hermes runs.
- **`MemoryMax=3G`** — the service's measured RSS is **2.0 GB** (the model's
  1.6 GB of weights plus torch itself), so the cap is deliberately close: it
  stops a runaway from taking the box down, and the gateway plus Supabase share
  these 16 GB. Raise it only with a measurement, not a guess.
- **`CPUWeight=50`** — a large ingestion (P4, all of Drive) must lose to live
  chat on the 4 shared vCPUs, not compete evenly with it.
- **`ProtectSystem=strict` + `ReadWritePaths`** — the only writable path is the
  model cache.

`deploy_state.py` captures it automatically: its unit glob is `hermes-*`, so the
unit and its hash land in the state repo, and a hand-edit shows up in the weekly
drift check like any other.

## Turning it on

The service being installed changes nothing by itself — the memory tier keeps
using the hashing embedder until `config.yaml` says otherwise:

```yaml
memory:
  embedding:
    provider: local_http          # 'hashing' (default) or 'local_http'
    endpoint: http://127.0.0.1:8791
    model: BAAI/bge-m3
    dim: 1024
    timeout_seconds: 120
```

`dim` must match both the service and the `vector(N)` column. The client sends
its configured model with every request and the service **refuses** (409) a
request naming a different model, and the client refuses a response of the wrong
width — because the failure that must never happen quietly is two embedding
spaces in one column, which makes cosine distance meaningless for every row and
looks like nothing at all in the data.

**Flipping this switch does not migrate the existing rows** — and it will not
pretend to. Every row records the model that embedded it, so:

- rows from another model are **excluded from recall**, never ranked against the
  current model's vectors;
- a *dimension* change is **refused at startup** (`EmbeddingSpaceMismatch`, with
  the remedy in the message) rather than failing later as an opaque Postgres
  type error on every write.

So the cutover is two steps, in this order:

```bash
hermes memory vectors status          # what's in the column right now
# ... edit config.yaml as above ...
hermes memory vectors reembed --mode dev
hermes memory vectors status          # every row in the new space
```

`reembed` rewrites every row in **one transaction**, replacing the `vector(N)`
column (pgvector cannot widen one in place) and rebuilding the HNSW index. If it
fails part-way — the embedding service dies, the box reboots — nothing moved:
the old column, vectors and provenance are intact, the previous config still
works, and the migration can simply be re-run. Embedding happens *before* the
transaction opens, so a 300 ms-per-row model does not hold a write lock across
minutes of CPU while live sessions block behind it.

Take a dump first anyway (`pg_dump -t app_dev.memories`). The transaction
protects against a failed migration; it does not protect against a *successful*
migration you did not want.

### Size the timeout for a batch, not a write

The first attempt on this deployment failed on the first batch:

```
✗ embedding service at http://127.0.0.1:8791 is unreachable or returned a
  non-JSON body: timed out
```

The service was healthy. `timeout_seconds` covers one whole request, and
`reembed` sends `--batch-size` texts per request: **16 texts took 16.3 s** on
these 4 shared vCPUs, against **0.2 s** for the single text a live memory write
embeds. So the 20 s that is generous for every runtime path is marginal for the
one path that batches — the migration fails while nothing else shows a symptom.
Hence `timeout_seconds: 120`, and `--batch-size 8` if the box is busy. Nothing
moved on the failed attempt, exactly as the transaction promises.

### Performed on hermes-systest, 2026-08-04

```
before   vector(256)   28 rows   hashing
after    vector(1024)  28 rows   BAAI/bge-m3   HNSW index rebuilt
```

Cross-lingual recall, which the hashing embedder could not do at all (a Chinese
query embedded to the zero vector):

```
招標截止日期是幾時        0.722  "find out when the next tender is due"
when is the bid submission deadline
                          0.698  "find out when the next tender is due"
```

The query and the row share no character. That is the whole point of the
migration.

## Automatic recall

Until P2 the tier was write-only: rows accumulated and the only reader was a
deliberate `memory_query` tool call, which in production happened **zero** times.
Recall now runs on every turn, budgeted:

```yaml
memory:
  recall:
    auto: true
    top_k: 5
    min_score: 0.65      # calibrated for bge-m3 on this corpus — see below
    max_chars: 1200
    min_query_chars: 8
    dedup_threshold: 0.97
```

The recalled rows are appended to that turn's user message **at API-call time**,
from a copy — the system prompt is untouched and the stored conversation is
unchanged, so the cached prefix survives and nothing leaks into session history.

`min_score` is not a tuning nicety: an HNSW search always returns `top_k` rows,
so without a floor every turn recalls its least-unrelated memories.

**And the floor does not survive a model change.** The 0.35 default was measured
against hashing vectors, where an unrelated question scores ~0.2 on an
incidental shared token. bge-m3 packs everything much higher — measured on this
deployment's 28 rows, six deliberately unrelated questions and six on-topic
ones:

```
unrelated  0.358 - 0.614      ("recommend a film for tonight" -> 0.614)
related    0.600 - 0.722      ("which amazon cloud account are we using" -> 0.600)
```

At 0.35 every turn recalls something; the two bands even **overlap**, so no
floor separates them perfectly on a corpus this small. 0.65 is set above the
highest unrelated score, which costs the weakest true match and keeps the model
from being handed a memory about films while answering about tenders. Recall
that misfires is worse than recall that stays quiet: a wrong memory is asserted
as fact every turn, a missing one is one question away.

Re-measure after any model change — the probe is a handful of on- and off-topic
queries through `store.query(..., min_score=0.0)` and reading the top score.

## Health

```bash
curl -s http://127.0.0.1:8791/health
# {"ok":true,"model":"BAAI/bge-m3","revision":"5617a9...","dim":1024,"loaded":true}
```

Verified on the box against this exact server code, running as `hermes`:

```
health        {"ok": true, "model": "BAAI/bge-m3", "revision": "5617a9f6...", "dim": 1024}
embed         2 texts (English + Chinese) -> 2 x 1024, both L2 norm 1.0, 0.42 s
mismatch      model="intfloat/multilingual-e5-base" -> HTTP 409, nothing embedded
listener      LISTEN 127.0.0.1:8791 only
RSS           2.0 GB
```

`ok` is false while the model loads (~10 s from a warm page cache); requests
during that window get 503 rather than a wrong answer.

## Benchmark (run on the box, 2026-08-04)

`ecs.e-c1m4.xlarge`, 4 vCPU shared with the gateway and Supabase, CPU only:

```
model                          dim  load_s  RSS_MB  1x_p50  1x_p95  16x_ms  R@1  R@3
hashing (incumbent)            256     0.0       0    0.04    0.26     0.5  .167  .667
BAAI/bge-m3                   1024     9.8    1577  296.71  408.61  1421.3  1.0   1.0
intfloat/multilingual-e5-base   768     9.7     742   78.22   83.48   466.6  1.0   1.0
```

Both candidates scored 1.0 on the bilingual probe set, so **recall did not
decide this** — 10 documents is too easy to separate them. What decided it was a
second probe: one ~11,900-character document with the answer at the very end,
against a decoy document and a padding-only document.

```
query: "when is the tender submission deadline"

BAAI/bge-m3            (window 8192)      e5-base            (window 512)
  0.7186  fact at the START                 0.9150  fact at the START
  0.5645  fact at the END      <-- found    0.7824  padding only
  0.4817  decoy fact at the END             0.7824  fact at the END
  0.4765  padding only                      0.7824  decoy fact at the END
```

Look at e5-base's last three scores: **identical to four decimals**. It never
read past its 512-token window, so a document containing the answer and a
document containing only boilerplate are the same vector to it. For
all-of-Drive RAG (P4) that is a correctness limit, not a speed one — the answer
is unretrievable rather than merely ranked lower.

The cost of choosing `bge-m3` is real and worth stating plainly: ~300 ms per
single embed against ~78 ms, and 3× the resident memory. Chunking below 512
tokens would let e5-base compete, which is why this is a *decision* and not a
verdict — but it would make chunk-boundary placement load-bearing for
correctness, and boundaries are exactly what an ingestion pipeline gets wrong.
Both probes are in git — `scripts/benchmark_embedders.py` — so the numbers are
reproducible rather than asserted. Run it in the service's venv (Hermes itself
does not depend on `sentence-transformers`):

```bash
cd /opt/data/hermes-embed
for t in hashing bge e5; do
  ./venv/bin/python /opt/data/hermes-agent/scripts/benchmark_embedders.py \
      recall "$t" --cache-dir /opt/data/hermes-embed/models
done
./venv/bin/python /opt/data/hermes-agent/scripts/benchmark_embedders.py \
    longdoc e5 --cache-dir /opt/data/hermes-embed/models
```

## Cold rebuild

```bash
mkdir -p /opt/data/hermes-embed && cd /opt/data/hermes-embed
python3.11 -m venv venv
./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
./venv/bin/pip install "sentence-transformers>=3,<6"
./venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("BAAI/bge-m3", revision="5617a9f61b028005a4858fdac845db406aefb181",
                  cache_dir="/opt/data/hermes-embed/models",
                  ignore_patterns=["onnx/*","*.onnx","*openvino*","colbert*","sparse*","imgs/*"])
PY
chown -R hermes:hermes /opt/data/hermes-embed
systemctl daemon-reload && systemctl enable --now hermes-embed
```

The `ignore_patterns` matter: without them the download also pulls ONNX and
OpenVINO copies of the same weights, roughly tripling the disk cost for runtimes
this service does not use.
