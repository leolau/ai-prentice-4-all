# AWS Cost Reporting and Cleanup — Handoff

Session date: 2026-07-29. Written for the next agent picking up AWS work on
Leo's accounts. Everything below was verified against live AWS APIs, not
inferred from documentation.

Related skill: `optional-skills/devops/aws-cost-report/` (script, `SKILL.md`,
and `references/enabling-per-instance-data.md`).

---

## 1. The three accounts

| Name | Account ID | Live regions | Notes |
|---|---|---|---|
| general (Joyaether) | `454267863464` | `ap-east-1` | PolyU / DIY-learn, buddhist, mpf, cionex, robocore |
| egobid (HK Production) | `444643374336` | `ap-east-1` | eGoBid, HKSTP, HKMOA, Pastec |
| storytellar (Snappop) | `520520953087` | `ap-southeast-1` (+ stragglers in `us-east-1`, `eu-central-1`) | Member of org payer `544207354851` |

`storytellar` is in an organization; `general` and `egobid` behave as
standalone accounts for billing purposes. This matters constantly — see §3.

Cross-account oddity worth remembering: an RDS instance named
`storytellar-prod` runs in the **egobid** account, not in storytellar.

## 2. Credentials

Credentials arrive as environment variables, not profiles:

| Account | Env prefix |
|---|---|
| general | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (ambient, no prefix) |
| egobid | `EGOBID_AWS_ACCESS_KEY_ID` / `EGOBID_AWS_SECRET_ACCESS_KEY` |
| storytellar | `STORYTELLAR_AWS_ACCESS_KEY_ID` / `STORYTELLAR_AWS_SECRET_ACCESS_KEY` |

SSH access to EC2 instances using the `microkey` key pair is in the saved
secret `EGOBID_MICROKEY_PEM`. **It arrives with newlines stripped** — OpenSSH
rejects it as "not a key file" until the base64 body is re-wrapped at 64
columns between the BEGIN/END lines. Re-wrap it before use; never log it.

Two environment gotchas:

- The preinstalled `aws` CLI is broken here: it raises `KeyError: 'opsworkscm'`
  while loading command aliases. **Use boto3 directly**; do not spend time
  fixing the CLI.
- boto3 is not in the system Python. A working venv already exists at
  `/home/ubuntu/awscost-venv` (boto3 1.43.58).

## 3. Per-instance cost data: what actually works

This was the single biggest source of wasted effort, so it is worth stating
plainly. There are three ways to get resource-level cost, and only one of them
gives full historical months:

1. **Cost Explorer `GetCostAndUsage`** — account and service totals, monthly
   granularity, any historical month. Always works. This is the reliable layer.
2. **Cost Explorer `GetCostAndUsageWithResources`** — per-resource, but only
   the **last ~14 days**, and it needs *two* separate enablements: the IAM
   permission **and** a resource-level opt-in performed **in the payer
   account's Cost Explorer settings**. Granting the IAM action alone still
   returns `Resource-level data granularity is an opt-in only feature`. It never
   backfills. Treat it as a labelled approximation, never as the monthly number.
3. **Cost and Usage Report (CUR) in S3** — hourly rows with `lineItem/ResourceId`,
   full months, no query charges if the CSVs are read directly. **This is the
   only real answer** for "what did instance X cost last month".

CUR does not backfill either: a report created today produces data from this
month forward, never for earlier months. So a newly created CUR cannot answer
questions about the past — say so rather than promising a later run will fill
it in.

### CUR locations

| Account | Bucket / prefix | Report name | Notes |
|---|---|---|---|
| egobid | `s3://my-cost-report-bucket-2023/cost-report` | `cost-report` | Pre-existing, hourly, GZIP CSV, includes `RESOURCES`. Verified working. |
| storytellar | `s3://cost-report-520520953087/cost-report` | `cost-report` | **Created during this session.** Data starts 2026-07; June and earlier do not exist. |
| general | none | — | No CUR. Per-instance history is unavailable for this account. |

Object keys look doubled — `cost-report/cost-report/<period>/cost-report-Manifest.json`
— because the S3 prefix and the report name are both `cost-report`. That is
correct, not a bug.

Reading the CUR needs two IAM statements with *different* ARN shapes, which is
the usual failure:

```json
{"Effect": "Allow", "Action": "s3:ListBucket",
 "Resource": "arn:aws:s3:::my-cost-report-bucket-2023"},
{"Effect": "Allow", "Action": "s3:GetObject",
 "Resource": "arn:aws:s3:::my-cost-report-bucket-2023/cost-report/*"}
```

Bucket ARN with no trailing `/*` for `ListBucket`; object ARN with `/*` for
`GetObject`. Granting only the object statement fails with `AccessDenied` on
listing.

### Running the report

```bash
/home/ubuntu/awscost-venv/bin/python \
  optional-skills/devops/aws-cost-report/scripts/aws_cost_report.py \
  --month 2026-07 --out-dir ~/reports \
  --account "general:env=AWS" \
  --account "egobid:env=EGOBID_AWS" \
  --account "storytellar:env=STORYTELLAR_AWS" \
  --cur-s3 "egobid=s3://my-cost-report-bucket-2023/cost-report" \
  --cur-s3 "storytellar=s3://cost-report-520520953087/cost-report" \
  --ec2-regions ap-east-1,ap-southeast-1
```

`--cur-s3` is per account because these accounts have separate buckets; an
unqualified `--cur-s3 s3://...` applies to any account without its own mapping
(the payer-CUR case). Exit code `3` means the report was written but at least
one account failed — check the errors section rather than trusting the totals.

## 4. Baseline numbers

June 2026 actuals (the last full month with complete data):

| Account | June | Notes |
|---|---|---|
| general | $1,150.59 | |
| egobid | $1,187.28 | 145 resources attributed via CUR = 99.9% |
| storytellar | $1,196.11 | no per-instance data for June |
| **Total** | **$3,533.98** | |

By service, June, rounded USD:

| Service | general | egobid | storytellar | total |
|---|---:|---:|---:|---:|
| RDS | 318 | 399 | 495 | 1,213 |
| EC2 compute | 332 | 414 | 249 | 995 |
| EC2 – Other (EBS, snapshots) | 164 | 243 | 198 | 604 |
| VPC (NAT gateways) | 126 | 122 | 148 | 396 |
| OpenSearch | 173 | — | — | 173 |
| Load balancing | — | — | 79 | 79 |
| SageMaker | 37 | — | — | 37 |
| S3 | 0 | 1 | 22 | 23 |
| Lightsail / CloudWatch | — | 8 | 6 | 14 |

Daily run rate is extremely stable at $37–40/day per account (±2% over a week),
so a projection from the last full day is trustworthy. July was on track for
~$3,558 before cleanup; August should land near **$3,318**.

Useful derived figure: storytellar `db.t4g.micro` instances cost
**$25.6/month each** (July instance-usage line $184.64 ÷ 8 instances ÷ 28 days).
Use measured usage-type lines like this instead of list prices.

## 5. What was changed in AWS this session

All destructive actions were explicitly approved by Leo, and every one was
preceded by a verified backup.

**EC2 terminated** (general, `ap-east-1`) — imaged first, AMI confirmed
`available` before termination, because both root volumes were
`DeleteOnTermination=true`:

| Instance | AMI (do not delete) | Snapshot |
|---|---|---|
| `carbon-finance-dev` `i-0e57429a763ee2c01` | `ami-0e82044658eb2e2ae` | `snap-09d7efe0371338760` |
| `learn-word-la` `i-04edede8fa041f24f` | `ami-09d85634222078b38` | `snap-0783f8bbeacf09df1` |

Those two AMIs are the **only** surviving copies of those servers. Any future
cleanup script must exclude them explicitly.

**RDS deleted** — each with a manual pre-deletion snapshot *and* the deletion's
own final snapshot, and `DeleteAutomatedBackups=False` so point-in-time restore
survives its retention window:

- egobid / `ap-east-1`: `hkmoa-dev`, `hkstp-dev`
- storytellar / `ap-southeast-1`: `profeinsteindb`, `ecsaf`, `ffa-dev`, `ba`, `ba-prod`

`ba-prod` had `DeletionProtection=true`. It was snapshotted but left running
until Leo confirmed explicitly; only then was protection disabled and the
instance deleted. **Keep that pattern** — deletion protection is a deliberate
signal, not an obstacle.

**Snapshots deleted:** storytellar manual RDS snapshots 73 → 29 (1,170 GB → 490 GB);
cross-account groups covering the `polyu-dev` collapse, a 1 TB egobid orphan,
the `mpf-uat` series, and decommissioned-project snapshots; then 31 AMIs
deregistered with their 38 backing snapshots (977 GB).

Total realised saving ≈ **$300/month**, of which ~$133 is terminated compute.

## 6. Snapshot mechanics learned the hard way

**An AMI is a manifest, not a copy.** It points at EBS snapshots; the snapshots
hold the bytes and carry the cost. Consequences:

- `DeleteSnapshot` on an AMI-referenced snapshot fails with
  `InvalidSnapshot.InUse`. Deregister the AMI first.
- Deregistering an AMI alone frees **nothing** — the snapshots keep billing.
  Always do both, and re-check remaining AMIs before deleting a snapshot in
  case another image shares it.
- One AMI can hold several snapshots (multi-volume instances). 31 AMIs held 38
  snapshots here, and the second disks were where most of the space was.

**Costs are billed on consumed blocks, not allocated size.** `AllocatedStorage`
× rate is an **upper bound** only. Label it as such; the audit's "$517/month"
ceiling was realistically 40–70% of that. Rates used: EBS ~$0.05/GB-month, RDS
backup ~$0.095/GB-month.

**Never derive deletion groups from a field you have not validated.** A collapse
script grouped RDS snapshots by `DBInstanceIdentifier` and deleted
`backup-before-upgrade-2023-07-31`, which had been explicitly listed as keep —
two unrelated snapshots shared `source_db = tuf-prod`. Snapshot deletion is
irreversible. Since then every deletion list is **hardcoded and printed for
review before execution**, with an assertion that no keeper appears in the
delete list. Do the same.

Other detection notes:

- `describe_db_snapshots` **must be paginated**. An unpaginated call reported 76
  snapshots where the true count was 73.
- A snapshot whose `DBInstanceIdentifier` no longer exists is orphaned — 67 of
  73 were. Snapshot names are unreliable; restored-from-backup copies carry
  names like `tuf-prod-from-backup-2023-03-09-1130`, so name prefixes do not
  identify a database.
- Snapshots hide in unexpected regions. storytellar had snapshots in
  `us-east-1` and `eu-central-1` while all its live infrastructure is in
  `ap-southeast-1`. Always sweep all regions.

## 7. Open items

Largest remaining cost item, not yet approved for deletion:

- **The 2026-02-02 batch in storytellar** — 35 EBS snapshots, 1,287 GB,
  ~$64/month, named `Snapshot-<name>-20260202`. Someone bulk-snapshotted every
  volume in the account on one day and never cleaned up. Several of those
  servers were re-imaged properly as AMIs on 2026-04-02, so most are probably
  redundant — verify before proposing deletion.
- 57 EBS snapshots total are free to delete (no AMI reference), 2,015 GB,
  ~$101/month; 14 of them are over two years old (~$27/month).
- **No lifecycle policy exists anywhere.** Every snapshot found was manual and
  forgotten. AWS Backup or DLM with 30/90-day retention on dev databases would
  stop this recurring, and is worth more than any single deletion.
- ~$396/month of VPC charges across the three accounts is almost certainly NAT
  gateways (~$36/month each just to exist). Not yet investigated.
- A monthly cron for the cost report (day 3) was offered and not yet approved.

Security observations noted but not acted on:

- Every RDS instance examined was `PubliclyAccessible: true`, including dev
  databases.
- `learn-word-la` ran Java 8 with `log4j-1.2.17.jar` (EOL, unpatched since
  2015) and was internet-facing; `carbon-finance-dev` had 792 days of uptime
  with SSH open to `0.0.0.0/0`. Both are now terminated, but sibling hosts
  likely share the pattern.

## 8. Operating rules for the next agent

1. Inventory first, and write the inventory to a CSV before proposing anything.
2. Verify account **and** region before touching a resource — the same project
   name appears in more than one account.
3. Back up before deleting; wait for the backup to report `available`, not just
   for the API call to return.
4. Never bypass deletion protection, and never widen an IAM policy, without
   explicit approval in the conversation.
5. Hardcode deletion lists. Print them with sizes and totals. Assert keepers
   are absent.
6. Report per-item failures individually; do not let one failure abort a batch
   or get summarised away.
7. State upper bounds as upper bounds, and never claim a saving until the API
   output confirms the deletion.
