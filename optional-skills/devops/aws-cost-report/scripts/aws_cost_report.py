#!/usr/bin/env python3
"""Generate a repeatable monthly AWS cost report: per account, per service, per instance.

Cost Explorer alone cannot do per-resource monthly costs, so the report combines
sources and always labels which one produced the instance table:

* Account x service totals come from Cost Explorer ``GetCostAndUsage`` (MONTHLY,
  grouped by LINKED_ACCOUNT + SERVICE). Works for any month in the CE retention
  window.
* Per-instance costs come from, in order of preference: the CUR CSVs in S3 read
  directly (``--cur-s3``, needs only s3 read — no Athena, no query cost), a CUR
  table in Athena (``--athena-*``), or Cost Explorer
  ``GetCostAndUsageWithResources``, which AWS only serves for the last 14 days
  and only after the payer account opts into resource-level granularity. That
  last fallback is labelled PARTIAL so a 14-day slice is never mistaken for a
  full month.

Usage:

    aws_cost_report.py --month 2026-06 \\
      --account "general:profile=default" \\
      --account "egobid:env=EGOBID_AWS" \\
      --out-dir ~/reports

    # full-month per-instance costs straight from the CUR in S3
    aws_cost_report.py --month 2026-06 --account "egobid:env=EGOBID_AWS" \\
      --cur-s3 s3://my-cost-report-bucket-2023/cost-report --cur-report-name cost-report

    # ...or from a CUR table registered in Athena
    aws_cost_report.py --month 2026-06 --account "payer:profile=default" \\
      --athena-database athenacurcfn_cur --athena-table cur \\
      --athena-output s3://my-athena-results/cost-report/

Credentials per account come from either an AWS shared-config profile
(``name:profile=<profile>``) or an env-var prefix
(``name:env=EGOBID_AWS`` reads ``EGOBID_AWS_ACCESS_KEY_ID`` /
``EGOBID_AWS_SECRET_ACCESS_KEY`` / optional ``EGOBID_AWS_SESSION_TOKEN``).
``name:env=`` with an empty prefix uses the ambient ``AWS_*`` environment.

Output is written as ``aws-cost-<month>.md``, ``.json``, and two CSVs, all named
by month so re-running a month overwrites rather than accumulates. Exit code is
0 on a complete report, 3 when some account failed (partial report still
written).
"""

from __future__ import annotations

import argparse
import calendar
import csv
import gzip
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

CE_REGION = "us-east-1"
DEFAULT_METRIC = "UnblendedCost"
# GetCostAndUsageWithResources rejects windows older than this.
RESOURCE_LOOKBACK_DAYS = 14
EC2_COMPUTE_SERVICE = "Amazon Elastic Compute Cloud - Compute"
INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]+$")
# CUR CSV column names (CUR v1 / "legacy" schema).
CUR_COST_COLUMN = "lineItem/UnblendedCost"
CUR_RESOURCE_COLUMN = "lineItem/ResourceId"
CUR_ACCOUNT_COLUMN = "lineItem/UsageAccountId"
CUR_TYPE_COLUMNS = ("product/instanceType", "product/instance_type")


class ReportError(RuntimeError):
    """Fatal, user-actionable configuration or credential error."""


# --------------------------------------------------------------------------
# pure helpers (no boto3 — unit tested directly)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountSpec:
    """One credential source to pull cost data from."""

    name: str
    kind: str  # "profile" | "env"
    value: str

    def describe(self) -> str:
        return f"{self.name} ({self.kind}={self.value or 'ambient'})"


@dataclass
class CostRow:
    account: str
    service: str
    amount: float
    unit: str


@dataclass
class InstanceRow:
    account: str
    resource_id: str
    instance_type: str
    name: str
    amount: float
    unit: str


@dataclass
class AccountReport:
    spec_name: str
    account_id: str = ""
    services: list[CostRow] = field(default_factory=list)
    instances: list[InstanceRow] = field(default_factory=list)
    instance_source: str = "none"  # "athena" | "cost-explorer-14d" | "none"
    instance_window: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(row.amount for row in self.services)


def parse_month(month: str) -> tuple[str, str]:
    """Return the CE ``[start, end)`` day pair for a ``YYYY-MM`` month."""
    try:
        parsed = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ReportError(f"--month must be YYYY-MM, got {month!r}") from exc
    last = calendar.monthrange(parsed.year, parsed.month)[1]
    start = date(parsed.year, parsed.month, 1)
    end = date(parsed.year, parsed.month, last) + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def previous_month(today: date) -> str:
    """The ``YYYY-MM`` before ``today``'s month — the default report target."""
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def parse_account_spec(spec: str) -> AccountSpec:
    """Parse ``name:profile=foo`` / ``name:env=PREFIX`` into an AccountSpec."""
    name, sep, source = spec.partition(":")
    if not sep or not name:
        raise ReportError(
            f"--account must look like 'name:profile=<profile>' or 'name:env=<PREFIX>', got {spec!r}"
        )
    kind, eq, value = source.partition("=")
    if not eq or kind not in ("profile", "env"):
        raise ReportError(
            f"--account source must be 'profile=<profile>' or 'env=<PREFIX>', got {source!r}"
        )
    return AccountSpec(name=name, kind=kind, value=value)


def resource_window(start: str, end: str, today: date) -> tuple[str, str] | None:
    """Clamp ``[start, end)`` into the CE resource-level 14-day window.

    Returns None when the requested month ended before the window opens, so the
    caller can say "no resource data" instead of sending a doomed API call.
    """
    earliest = today - timedelta(days=RESOURCE_LOOKBACK_DAYS)
    win_start = max(date.fromisoformat(start), earliest)
    win_end = min(date.fromisoformat(end), today)
    if win_start >= win_end:
        return None
    return win_start.isoformat(), win_end.isoformat()


def aggregate_ce_groups(pages: Iterable[dict[str, Any]], metric: str = DEFAULT_METRIC) -> list[CostRow]:
    """Flatten CE ``GetCostAndUsage`` pages into account/service rows.

    Groups repeat once per ResultsByTime bucket, so amounts are summed by key
    rather than appended — a multi-month window collapses into one row per pair.
    """
    totals: dict[tuple[str, str], CostRow] = {}
    for page in pages:
        for bucket in page.get("ResultsByTime", []):
            for group in bucket.get("Groups", []):
                keys = group.get("Keys", [])
                account = keys[0] if keys else "unknown"
                service = keys[1] if len(keys) > 1 else "all"
                cost = group.get("Metrics", {}).get(metric, {})
                key = (account, service)
                row = totals.get(key)
                if row is None:
                    totals[key] = CostRow(
                        account=account,
                        service=service,
                        amount=float(cost.get("Amount", 0.0)),
                        unit=cost.get("Unit", "USD"),
                    )
                else:
                    row.amount += float(cost.get("Amount", 0.0))
    return sorted(totals.values(), key=lambda r: (-r.amount, r.account, r.service))


def aggregate_resource_groups(
    pages: Iterable[dict[str, Any]],
    account: str,
    metric: str = DEFAULT_METRIC,
) -> list[InstanceRow]:
    """Flatten CE ``GetCostAndUsageWithResources`` pages into instance rows."""
    totals: dict[str, InstanceRow] = {}
    for page in pages:
        for bucket in page.get("ResultsByTime", []):
            for group in bucket.get("Groups", []):
                keys = group.get("Keys", [])
                resource = keys[0] if keys else "unknown"
                cost = group.get("Metrics", {}).get(metric, {})
                row = totals.get(resource)
                if row is None:
                    totals[resource] = InstanceRow(
                        account=account,
                        resource_id=resource,
                        instance_type="",
                        name="",
                        amount=float(cost.get("Amount", 0.0)),
                        unit=cost.get("Unit", "USD"),
                    )
                else:
                    row.amount += float(cost.get("Amount", 0.0))
    rows = [row for row in totals.values() if row.amount != 0.0 or INSTANCE_ID_RE.match(row.resource_id)]
    return sorted(rows, key=lambda r: (-r.amount, r.resource_id))


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/prefix`` into ``(bucket, prefix)`` without a trailing slash."""
    if not uri.startswith("s3://"):
        raise ReportError(f"expected an s3:// URI, got {uri!r}")
    bucket, _, prefix = uri[len("s3://") :].partition("/")
    if not bucket:
        raise ReportError(f"s3 URI has no bucket: {uri!r}")
    return bucket, prefix.strip("/")


def cur_manifest_key(prefix: str, report_name: str, month: str) -> str:
    """Key of the billing-period manifest CUR writes for ``month``.

    Layout is ``<prefix>/<report>/<YYYYMMDD>-<YYYYMMDD>/<report>-Manifest.json``,
    where the range is the billing period, not the delivery date. The manifest
    always names the current assembly, so following it avoids double-counting
    superseded assemblies when ReportVersioning is CREATE_NEW_REPORT.
    """
    start, end = parse_month(month)
    period = f"{start.replace('-', '')}-{end.replace('-', '')}"
    head = f"{prefix}/" if prefix else ""
    return f"{head}{report_name}/{period}/{report_name}-Manifest.json"


def aggregate_cur_rows(
    rows: Iterable[Sequence[str]], spec_name: str, min_cost: float = 0.0
) -> list[InstanceRow]:
    """Sum hourly CUR line items into one row per resource id.

    The first row must be the CSV header — CUR column order is not stable across
    months (new products add columns), so everything is resolved by name.
    """
    header: dict[str, int] | None = None
    cost_idx = account_idx = resource_idx = -1
    type_idx = -1
    totals: dict[str, InstanceRow] = {}
    for row in rows:
        if header is None:
            header = {name: i for i, name in enumerate(row)}
            missing = [c for c in (CUR_COST_COLUMN, CUR_RESOURCE_COLUMN) if c not in header]
            if missing:
                raise ReportError(f"CUR csv is missing column(s) {missing}")
            cost_idx = header[CUR_COST_COLUMN]
            resource_idx = header[CUR_RESOURCE_COLUMN]
            account_idx = header.get(CUR_ACCOUNT_COLUMN, -1)
            for candidate in CUR_TYPE_COLUMNS:
                if candidate in header:
                    type_idx = header[candidate]
                    break
            continue
        if len(row) <= max(cost_idx, resource_idx):
            continue
        resource = row[resource_idx]
        if not resource:
            continue
        try:
            cost = float(row[cost_idx] or 0.0)
        except ValueError:
            continue
        existing = totals.get(resource)
        if existing is None:
            totals[resource] = InstanceRow(
                account=row[account_idx] if 0 <= account_idx < len(row) else spec_name,
                resource_id=resource,
                instance_type=row[type_idx] if 0 <= type_idx < len(row) else "",
                name="",
                amount=cost,
                unit="USD",
            )
        else:
            existing.amount += cost
            if not existing.instance_type and 0 <= type_idx < len(row):
                existing.instance_type = row[type_idx]
    rows_out = [row for row in totals.values() if row.amount > min_cost]
    return sorted(rows_out, key=lambda r: -r.amount)


def build_cur_query(database: str, table: str, month: str, min_cost: float = 0.0) -> str:
    """Athena SQL for per-resource monthly cost from a CUR table.

    Filters on the ``year``/``month`` Hive partitions the CUR-to-Athena
    integration creates, so the scan stays inside one billing period.
    """
    year, mon = month.split("-")
    return (
        "SELECT line_item_usage_account_id AS account_id,\n"
        "       line_item_resource_id AS resource_id,\n"
        "       max(product_instance_type) AS instance_type,\n"
        "       sum(line_item_unblended_cost) AS cost\n"
        f"FROM {database}.{table}\n"
        f"WHERE year = '{year}' AND month = '{int(mon)}'\n"
        "  AND line_item_resource_id <> ''\n"
        "GROUP BY 1, 2\n"
        f"HAVING sum(line_item_unblended_cost) > {min_cost}\n"
        "ORDER BY cost DESC"
    )


def parse_athena_rows(result_pages: Iterable[dict[str, Any]], spec_name: str) -> list[InstanceRow]:
    """Convert Athena ``GetQueryResults`` pages into instance rows.

    The first row of the first page is Athena's header row; every page after
    that is data only.
    """
    rows: list[InstanceRow] = []
    seen_header = False
    for page in result_pages:
        data = page.get("ResultSet", {}).get("Rows", [])
        if not seen_header:
            data = data[1:]
            seen_header = True
        for entry in data:
            cells = [cell.get("VarCharValue", "") for cell in entry.get("Data", [])]
            if len(cells) < 4:
                continue
            account_id, resource_id, instance_type, cost = cells[:4]
            rows.append(
                InstanceRow(
                    account=account_id or spec_name,
                    resource_id=resource_id,
                    instance_type=instance_type or "",
                    name="",
                    amount=float(cost or 0.0),
                    unit="USD",
                )
            )
    return sorted(rows, key=lambda r: -r.amount)


def apply_instance_names(rows: Sequence[InstanceRow], names: dict[str, str]) -> None:
    """Fill in EC2 Name tags for rows whose resource id is an instance id."""
    for row in rows:
        tail = row.resource_id.rsplit("/", 1)[-1]
        if tail in names:
            row.name = names[tail]


def render_markdown(month: str, reports: Sequence[AccountReport], generated_at: str) -> str:
    """Render the human-facing monthly report."""
    lines = [
        f"# AWS cost report — {month}",
        "",
        f"Generated {generated_at} · metric `{DEFAULT_METRIC}`",
        "",
        "## Totals by account",
        "",
        "| Source | Account | Total |",
        "| --- | --- | --- |",
    ]
    grand = 0.0
    for report in reports:
        by_account: dict[str, float] = {}
        for row in report.services:
            by_account[row.account] = by_account.get(row.account, 0.0) + row.amount
        for account, amount in sorted(by_account.items(), key=lambda kv: -kv[1]):
            grand += amount
            lines.append(f"| {report.spec_name} | {account} | {amount:,.2f} |")
        if not by_account:
            lines.append(f"| {report.spec_name} | — | no data |")
    lines += ["| | **Grand total** | " + f"**{grand:,.2f}** |", ""]

    for report in reports:
        lines += [f"## {report.spec_name}", ""]
        if report.errors:
            for err in report.errors:
                lines.append(f"> ERROR: {err}")
            lines.append("")
        if report.services:
            lines += [
                "### Cost by account and service",
                "",
                "| Account | Service | Cost |",
                "| --- | --- | --- |",
            ]
            lines += [
                f"| {row.account} | {row.service} | {row.amount:,.2f} |"
                for row in report.services
                if row.amount != 0.0
            ]
            lines.append("")
        label = {
            "cur-s3": "CUR CSVs in S3 (full month)",
            "athena": "CUR via Athena (full month)",
            "cost-explorer-14d": "Cost Explorer resource-level — PARTIAL, last 14 days only",
            "none": "unavailable",
        }[report.instance_source]
        lines += [f"### Cost by instance — {label}", ""]
        if report.instance_window:
            lines += [f"Window: {report.instance_window}", ""]
        if report.instances:
            lines += [
                "| Account | Resource | Type | Name | Cost |",
                "| --- | --- | --- | --- | --- |",
            ]
            lines += [
                f"| {row.account} | {row.resource_id} | {row.instance_type or '—'} | "
                f"{row.name or '—'} | {row.amount:,.2f} |"
                for row in report.instances
            ]
        else:
            lines.append(
                "No per-instance data. Point `--cur-s3` at the CUR prefix in S3 (or use "
                "`--athena-database/--athena-table/--athena-output`) for full-month "
                "instance costs."
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def report_payload(month: str, reports: Sequence[AccountReport], generated_at: str) -> dict[str, Any]:
    return {
        "month": month,
        "generated_at": generated_at,
        "metric": DEFAULT_METRIC,
        "accounts": [
            {
                "source": report.spec_name,
                "account_id": report.account_id,
                "total": round(report.total, 6),
                "instance_source": report.instance_source,
                "instance_window": report.instance_window,
                "errors": report.errors,
                "services": [
                    {
                        "account": row.account,
                        "service": row.service,
                        "amount": round(row.amount, 6),
                        "unit": row.unit,
                    }
                    for row in report.services
                ],
                "instances": [
                    {
                        "account": row.account,
                        "resource_id": row.resource_id,
                        "instance_type": row.instance_type,
                        "name": row.name,
                        "amount": round(row.amount, 6),
                        "unit": row.unit,
                    }
                    for row in report.instances
                ],
            }
            for report in reports
        ],
    }


# --------------------------------------------------------------------------
# AWS access (boto3 imported lazily so the pure helpers stay importable)
# --------------------------------------------------------------------------


def make_session(spec: AccountSpec):
    import boto3  # noqa: PLC0415 — lazy so tests need no boto3

    if spec.kind == "profile":
        return boto3.Session(profile_name=spec.value)
    prefix = f"{spec.value}_" if spec.value else ""
    key = os.environ.get(f"{prefix}ACCESS_KEY_ID") or os.environ.get(f"{prefix}AWS_ACCESS_KEY_ID")
    secret = os.environ.get(f"{prefix}SECRET_ACCESS_KEY") or os.environ.get(
        f"{prefix}AWS_SECRET_ACCESS_KEY"
    )
    token = os.environ.get(f"{prefix}SESSION_TOKEN") or os.environ.get(f"{prefix}AWS_SESSION_TOKEN")
    if not prefix:
        return boto3.Session()
    if not key or not secret:
        raise ReportError(
            f"account {spec.name}: env prefix {spec.value} has no "
            f"{prefix}ACCESS_KEY_ID / {prefix}SECRET_ACCESS_KEY"
        )
    return boto3.Session(
        aws_access_key_id=key, aws_secret_access_key=secret, aws_session_token=token or None
    )


def paginate(call: Callable[..., dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    """Follow CE's ``NextPageToken`` chain and return every page."""
    pages: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        page = call(**kwargs, NextPageToken=token) if token else call(**kwargs)
        pages.append(page)
        token = page.get("NextPageToken")
        if not token:
            return pages


def fetch_account_service_costs(session, start: str, end: str) -> list[CostRow]:
    client = session.client("ce", region_name=CE_REGION)
    pages = paginate(
        client.get_cost_and_usage,
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=[DEFAULT_METRIC],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            {"Type": "DIMENSION", "Key": "SERVICE"},
        ],
    )
    return aggregate_ce_groups(pages)


def fetch_resource_costs_ce(session, start: str, end: str, spec_name: str) -> list[InstanceRow]:
    """Per-resource EC2 costs from CE. Only valid inside the 14-day window.

    CE rejects RESOURCE_ID grouping without a Filter, hence the EC2-Compute
    service filter.
    """
    client = session.client("ce", region_name=CE_REGION)
    pages = paginate(
        client.get_cost_and_usage_with_resources,
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=[DEFAULT_METRIC],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": [EC2_COMPUTE_SERVICE]}},
        GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
    )
    return aggregate_resource_groups(pages, spec_name)


def fetch_resource_costs_athena(
    session,
    *,
    database: str,
    table: str,
    output: str,
    month: str,
    region: str,
    spec_name: str,
    poll_seconds: float = 2.0,
    timeout_seconds: float = 600.0,
) -> list[InstanceRow]:
    client = session.client("athena", region_name=region)
    query_id = client.start_query_execution(
        QueryString=build_cur_query(database, table, month),
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output},
    )["QueryExecutionId"]
    deadline = time.monotonic() + timeout_seconds
    while True:
        execution = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = execution["Status"].get("StateChangeReason", state)
            raise ReportError(f"Athena query {query_id} {state}: {reason}")
        if time.monotonic() > deadline:
            raise ReportError(f"Athena query {query_id} still {state} after {timeout_seconds}s")
        time.sleep(poll_seconds)
    pages = []
    token = None
    while True:
        kwargs = {"QueryExecutionId": query_id}
        if token:
            kwargs["NextToken"] = token
        page = client.get_query_results(**kwargs)
        pages.append(page)
        token = page.get("NextToken")
        if not token:
            break
    return parse_athena_rows(pages, spec_name)


def fetch_resource_costs_cur_s3(
    session,
    *,
    bucket: str,
    prefix: str,
    report_name: str,
    month: str,
    spec_name: str,
    min_cost: float = 0.0,
) -> list[InstanceRow]:
    """Read the month's CUR CSVs straight out of S3 and sum them per resource.

    Needs only ``s3:GetObject`` on the CUR prefix — no Athena, no Glue, no
    resource-level Cost Explorer opt-in. Each gzip member is streamed and folded
    into the running totals so a multi-GB month never lands in memory.
    """
    s3 = session.client("s3")
    manifest_key = cur_manifest_key(prefix, report_name, month)
    try:
        manifest = json.loads(s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read())
    except Exception as exc:  # noqa: BLE001 — turn any S3/JSON failure into an actionable message
        raise ReportError(f"cannot read CUR manifest s3://{bucket}/{manifest_key}: {exc}") from exc
    keys = manifest.get("reportKeys") or []
    if not keys:
        raise ReportError(f"CUR manifest s3://{bucket}/{manifest_key} lists no reportKeys")
    merged: dict[str, InstanceRow] = {}
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"]
        stream: Any = body
        if key.endswith(".gz"):
            stream = gzip.GzipFile(fileobj=body)
        text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
        for row in aggregate_cur_rows(csv.reader(text), spec_name, min_cost):
            existing = merged.get(row.resource_id)
            if existing is None:
                merged[row.resource_id] = row
            else:
                existing.amount += row.amount
                existing.instance_type = existing.instance_type or row.instance_type
    return sorted(merged.values(), key=lambda r: -r.amount)


def fetch_instance_names(session, regions: Sequence[str]) -> dict[str, str]:
    """Map instance id -> Name tag across regions, skipping unreachable ones."""
    names: dict[str, str] = {}
    for region in regions:
        try:
            ec2 = session.client("ec2", region_name=region)
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate():
                for reservation in page.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                        names[instance["InstanceId"]] = tags.get("Name", "")
        except Exception:  # noqa: BLE001 — a denied/disabled region must not sink the report
            continue
    return names


def account_id_of(session) -> str:
    try:
        return session.client("sts", region_name=CE_REGION).get_caller_identity()["Account"]
    except Exception:  # noqa: BLE001 — sts may be denied; the id is cosmetic
        return ""


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def collect_account(
    spec: AccountSpec, args: argparse.Namespace, start: str, end: str, today: date
) -> AccountReport:
    report = AccountReport(spec_name=spec.name)
    session = make_session(spec)
    report.account_id = account_id_of(session)
    try:
        report.services = fetch_account_service_costs(session, start, end)
    except Exception as exc:  # noqa: BLE001 — one account's failure is reported, not fatal
        report.errors.append(f"cost explorer: {exc}")

    if args.instances == "none":
        return report

    if args.cur_s3 and args.instances in ("auto", "cur-s3"):
        try:
            bucket, prefix = parse_s3_uri(args.cur_s3)
            report.instances = fetch_resource_costs_cur_s3(
                session,
                bucket=bucket,
                prefix=prefix,
                report_name=args.cur_report_name,
                month=args.month,
                spec_name=spec.name,
            )
            report.instance_source = "cur-s3"
            report.instance_window = f"{start} → {end} (full month)"
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"cur in s3: {exc}")
    if args.instances == "cur-s3":
        return _with_names(session, report, args)

    use_athena = bool(args.athena_database and args.athena_table and args.athena_output)
    if not report.instances and use_athena and args.instances in ("auto", "athena"):
        try:
            report.instances = fetch_resource_costs_athena(
                session,
                database=args.athena_database,
                table=args.athena_table,
                output=args.athena_output,
                month=args.month,
                region=args.athena_region,
                spec_name=spec.name,
            )
            report.instance_source = "athena"
            report.instance_window = f"{start} → {end} (full month)"
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"athena: {exc}")
    if args.instances == "athena":
        return _with_names(session, report, args)

    if not report.instances:
        window = resource_window(start, end, today)
        if window is None:
            report.errors.append(
                f"per-instance costs unavailable: {args.month} is outside the "
                f"{RESOURCE_LOOKBACK_DAYS}-day Cost Explorer resource-level window"
            )
        else:
            try:
                report.instances = fetch_resource_costs_ce(session, window[0], window[1], spec.name)
                report.instance_source = "cost-explorer-14d"
                report.instance_window = f"{window[0]} → {window[1]} (partial month)"
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"cost explorer resource-level: {exc}")
    return _with_names(session, report, args)


def _with_names(session, report: AccountReport, args: argparse.Namespace) -> AccountReport:
    if report.instances and args.ec2_regions:
        apply_instance_names(report.instances, fetch_instance_names(session, args.ec2_regions))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--month",
        default=None,
        help="Billing month as YYYY-MM (default: the previous calendar month)",
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        metavar="NAME:SOURCE",
        help="Credential source, e.g. 'egobid:env=EGOBID_AWS' or 'main:profile=default' (repeatable)",
    )
    parser.add_argument("--out-dir", default=".", help="Directory for the report files")
    parser.add_argument(
        "--instances",
        choices=("auto", "cur-s3", "athena", "ce", "none"),
        default="auto",
        help="Per-instance source: auto (CUR in S3, then Athena, then CE 14-day), cur-s3, athena, ce, none",
    )
    parser.add_argument(
        "--cur-s3",
        default=os.environ.get("CUR_S3_URI", ""),
        metavar="S3URI",
        help="CUR delivery prefix, e.g. s3://my-cost-bucket/cost-report (needs s3:GetObject only)",
    )
    parser.add_argument(
        "--cur-report-name",
        default=os.environ.get("CUR_REPORT_NAME", ""),
        help="CUR report name as shown by 'cur describe-report-definitions' (default: last path segment of --cur-s3)",
    )
    parser.add_argument("--athena-database", default=os.environ.get("CUR_ATHENA_DATABASE", ""))
    parser.add_argument("--athena-table", default=os.environ.get("CUR_ATHENA_TABLE", ""))
    parser.add_argument("--athena-output", default=os.environ.get("CUR_ATHENA_OUTPUT", ""))
    parser.add_argument("--athena-region", default=os.environ.get("CUR_ATHENA_REGION", CE_REGION))
    parser.add_argument(
        "--ec2-regions",
        default="",
        help="Comma-separated regions to resolve instance Name tags in (default: none)",
    )
    parser.add_argument("--quiet", action="store_true", help="Write files without printing the report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = datetime.now(timezone.utc).date()
    args.month = args.month or previous_month(today)
    args.ec2_regions = [r.strip() for r in args.ec2_regions.split(",") if r.strip()]
    if args.cur_s3 and not args.cur_report_name:
        args.cur_report_name = args.cur_s3.rstrip("/").rsplit("/", 1)[-1]
    if not args.account:
        args.account = ["default:env="]
    try:
        specs = [parse_account_spec(spec) for spec in args.account]
        start, end = parse_month(args.month)
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    reports: list[AccountReport] = []
    for spec in specs:
        try:
            reports.append(collect_account(spec, args, start, end, today))
        except Exception as exc:  # noqa: BLE001 — keep going so one bad key still yields a report
            reports.append(AccountReport(spec_name=spec.name, errors=[str(exc)]))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"aws-cost-{args.month}"
    markdown = render_markdown(args.month, reports, generated_at)
    (out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(
        json.dumps(report_payload(args.month, reports, generated_at), indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(
        out_dir / f"{stem}-by-service.csv",
        ("source", "account", "service", "amount", "unit"),
        [
            (r.spec_name, row.account, row.service, f"{row.amount:.6f}", row.unit)
            for r in reports
            for row in r.services
        ],
    )
    write_csv(
        out_dir / f"{stem}-by-instance.csv",
        ("source", "account", "resource_id", "instance_type", "name", "amount", "unit"),
        [
            (
                r.spec_name,
                row.account,
                row.resource_id,
                row.instance_type,
                row.name,
                f"{row.amount:.6f}",
                row.unit,
            )
            for r in reports
            for row in r.instances
        ],
    )
    if not args.quiet:
        print(markdown)
    print(f"wrote {out_dir / stem}.{{md,json}} and 2 CSVs", file=sys.stderr)
    return 3 if any(r.errors for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
