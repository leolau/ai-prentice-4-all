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
    timeout_seconds: 20
```

`dim` must match both the service and the `vector(N)` column. The client sends
its configured model with every request and the service **refuses** (409) a
request naming a different model, and the client refuses a response of the wrong
width — because the failure that must never happen quietly is two embedding
spaces in one column, which makes cosine distance meaningless for every row and
looks like nothing at all in the data.

**Existing rows are not re-embedded by flipping this switch.** The 28 rows
currently in `app_dev.memories` are 256-dim hashing vectors; the re-embed
command and the per-row model/dim metadata are FG-21 P2. Until then, treat
`local_http` as configured-but-not-cut-over.

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
