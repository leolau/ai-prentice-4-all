---
name: aws-cost-report
description: Monthly AWS cost report by account and instance.
version: 1.0.0
author: Leo Lau (leolau), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [aws, cost, billing, finops, cost-explorer, cur, athena, reporting]
    category: devops
    requires_toolsets: [terminal]
    related_skills: [watchers]
---

# AWS Cost Report Skill

Produce the same AWS spending report every month: totals per account, a
breakdown per service, and a breakdown per individual EC2 instance. One script,
read-only AWS calls, files named by month so a re-run replaces the previous run
instead of piling up.

It does not tune, right-size, or delete anything — it reports.

## When to Use

- User asks for "AWS spend last month", "cost by account", "which instances cost the most"
- Setting up a recurring monthly cost report (cron job, Slack/Telegram digest)
- Comparing spend across several AWS accounts that are not in one payer organization
- Investigating a bill spike and needing per-instance attribution

## Prerequisites

- `boto3`: `pip install boto3`
- Credentials per account, either an AWS shared-config profile or an env-var
  prefix in `${HERMES_HOME:-~/.hermes}/.env` (e.g. `EGOBID_AWS_ACCESS_KEY_ID`,
  `EGOBID_AWS_SECRET_ACCESS_KEY`).
- IAM permissions: `ce:GetCostAndUsage`, `ce:GetCostAndUsageWithResources`,
  `sts:GetCallerIdentity`, and `ec2:DescribeInstances` for Name tags.
  Cost Explorer must be enabled once in the console per account.
- **For full-month per-instance costs:** a Cost and Usage Report delivered to S3
  with the Athena integration enabled, plus `athena:StartQueryExecution`,
  `athena:GetQueryExecution`, `athena:GetQueryResults`, `glue:GetTable`, and S3
  read/write on the CUR bucket and the Athena results prefix.

## How to Run

Run `scripts/aws_cost_report.py` with the `terminal` tool. Installed path:
`$HERMES_HOME/skills/devops/aws-cost-report/scripts/aws_cost_report.py`.

```bash
# previous calendar month, one account from the ambient AWS_* env
python aws_cost_report.py --out-dir ~/reports

# several accounts, explicit month, resolve instance Name tags
python aws_cost_report.py --month 2026-06 --out-dir ~/reports \
  --account "general:profile=default" \
  --account "egobid:env=EGOBID_AWS" \
  --account "storytellar:env=STORYTELLAR_AWS" \
  --ec2-regions ap-east-1,ap-southeast-1

# full-month per-instance costs from a CUR table in Athena
python aws_cost_report.py --month 2026-06 --account "payer:profile=default" \
  --athena-database athenacurcfn_cur --athena-table cur \
  --athena-output s3://my-athena-results/cost-report/
```

## Quick Reference

| Flag | Meaning |
|---|---|
| `--month YYYY-MM` | Billing month (default: previous calendar month) |
| `--account NAME:profile=P` | Credentials from shared-config profile `P` |
| `--account NAME:env=PREFIX` | Credentials from `PREFIX_ACCESS_KEY_ID` / `PREFIX_SECRET_ACCESS_KEY` (also accepts `PREFIX_AWS_…`); `env=` alone uses ambient `AWS_*` |
| `--instances auto\|athena\|ce\|none` | Per-instance source; `auto` tries Athena, falls back to the CE 14-day window |
| `--athena-database/-table/-output/-region` | CUR table location and Athena results prefix (env: `CUR_ATHENA_DATABASE`, `CUR_ATHENA_TABLE`, `CUR_ATHENA_OUTPUT`, `CUR_ATHENA_REGION`) |
| `--ec2-regions a,b` | Regions to resolve instance `Name` tags in |
| `--quiet` | Write files only, don't print the report |

Outputs in `--out-dir`: `aws-cost-<month>.md`, `aws-cost-<month>.json`,
`aws-cost-<month>-by-service.csv`, `aws-cost-<month>-by-instance.csv`.
Exit codes: `0` complete, `3` report written but some account errored, `2` bad arguments.

## Procedure

1. Confirm which accounts to report on and how each one authenticates. One
   `--account` per credential set; a payer account covers its whole
   organization in a single call because costs are grouped by `LINKED_ACCOUNT`.
2. Decide the per-instance source. If a CUR table exists, pass the `--athena-*`
   flags — that is the only way to get full-month per-resource cost. Without it,
   the script falls back to Cost Explorer resource-level data, which AWS serves
   for the **last 14 days only**; the report marks that section `PARTIAL`.
3. Run the script for the target month.
4. Read the generated Markdown and summarize: grand total, per-account totals,
   biggest movers by service, and the top instances by cost.
5. To make it recurring, create a cron job on day 3 of each month (CUR data for
   the closed month finalizes in the first days) that runs the script with no
   `--month` and then summarizes the Markdown into the user's channel.

## Pitfalls

1. **Expecting per-instance costs for an old month without CUR.** Cost Explorer
   resource-level data is capped at 14 days and needs the resource-level setting
   enabled in Cost Explorer preferences. For "June by instance" in September,
   only a CUR/Athena query can answer.
2. **Assuming one credential set sees every account.** Cost Explorer only
   returns data for the calling account and, if it is a payer, its
   organization's members. Standalone accounts each need their own `--account`.
3. **Running a report for the current month and comparing it to last month.**
   The current month is incomplete; monthly comparisons need closed months.
4. **`UnblendedCost` is not the invoice.** Credits, refunds, RI/SP amortization,
   and tax are not reflected in this metric. Use it for attribution, not for
   reconciling the bill.
5. **Untagged shared spend has no instance.** Data transfer, S3, RDS, and
   support appear in the per-service table but never in the per-instance table —
   the two tables do not sum to the same number.
6. **Athena costs money and scans a lot.** The query is partition-filtered to
   one billing period; do not widen it to a full-table scan.

## Verification

```bash
# arguments and pure logic, no AWS calls
python aws_cost_report.py --help
scripts/run_tests.sh tests/skills/test_aws_cost_report_skill.py -q

# live smoke: one account, no per-instance calls
python aws_cost_report.py --month 2026-06 --instances none --out-dir /tmp/costcheck --quiet
```

A good run prints the output paths on stderr and exits `0`. Exit `3` means the
files were written but at least one account reported an error — read the
`ERROR:` lines in the Markdown before trusting the totals.
