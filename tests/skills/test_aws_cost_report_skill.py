"""Tests for optional-skills/devops/aws-cost-report/scripts/aws_cost_report.py"""

import csv
import gzip
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2] / "optional-skills" / "devops" / "aws-cost-report"
)
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import aws_cost_report as acr


def ce_page(*groups, metric="UnblendedCost"):
    return {
        "ResultsByTime": [
            {
                "Groups": [
                    {
                        "Keys": list(keys),
                        "Metrics": {metric: {"Amount": str(amount), "Unit": "USD"}},
                    }
                    for *keys, amount in groups
                ]
            }
        ]
    }


class TestSkillMetadata:
    def test_description_within_listing_budget(self):
        body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        match = re.search(r"^description: (.*)$", body, re.MULTILINE)
        assert match, "SKILL.md needs a description"
        assert len(match.group(1)) <= 60, len(match.group(1))

    def test_script_referenced_by_skill(self):
        body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        assert "scripts/aws_cost_report.py" in body
        assert (SKILL_DIR / "scripts" / "aws_cost_report.py").exists()


class TestMonthParsing:
    def test_full_month_is_half_open(self):
        assert acr.parse_month("2026-06") == ("2026-06-01", "2026-07-01")

    def test_leap_february(self):
        assert acr.parse_month("2024-02") == ("2024-02-01", "2024-03-01")

    def test_december_rolls_the_year(self):
        assert acr.parse_month("2025-12") == ("2025-12-01", "2026-01-01")

    @pytest.mark.parametrize("bad", ["2026", "2026-13", "june", "2026/06", ""])
    def test_rejects_malformed(self, bad):
        with pytest.raises(acr.ReportError):
            acr.parse_month(bad)

    def test_previous_month_crosses_year(self):
        assert acr.previous_month(date(2026, 1, 15)) == "2025-12"

    def test_previous_month_same_year(self):
        assert acr.previous_month(date(2026, 7, 1)) == "2026-06"


class TestAccountSpec:
    def test_profile_source(self):
        spec = acr.parse_account_spec("main:profile=default")
        assert (spec.name, spec.kind, spec.value) == ("main", "profile", "default")

    def test_env_prefix_source(self):
        spec = acr.parse_account_spec("egobid:env=EGOBID_AWS")
        assert (spec.name, spec.kind, spec.value) == ("egobid", "env", "EGOBID_AWS")

    def test_empty_env_prefix_means_ambient(self):
        assert acr.parse_account_spec("default:env=").value == ""

    @pytest.mark.parametrize("bad", ["main", "main:default", ":profile=x", "main:role=arn"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(acr.ReportError):
            acr.parse_account_spec(bad)


class TestResourceWindow:
    def test_clamps_month_start_to_lookback(self):
        window = acr.resource_window("2026-07-01", "2026-08-01", date(2026, 7, 29))
        assert window == ("2026-07-15", "2026-07-29")

    def test_month_fully_inside_window_is_untouched_at_start(self):
        window = acr.resource_window("2026-07-25", "2026-07-28", date(2026, 7, 29))
        assert window == ("2026-07-25", "2026-07-28")

    def test_month_older_than_window_has_no_data(self):
        assert acr.resource_window("2026-05-01", "2026-06-01", date(2026, 7, 29)) is None

    def test_future_month_has_no_data(self):
        assert acr.resource_window("2026-09-01", "2026-10-01", date(2026, 7, 29)) is None


class TestAggregation:
    def test_sums_repeated_keys_across_buckets(self):
        pages = [
            ce_page(("111", "Amazon EC2", 10.0), ("111", "Amazon RDS", 4.0)),
            ce_page(("111", "Amazon EC2", 5.0)),
        ]
        rows = acr.aggregate_ce_groups(pages)
        assert [(r.account, r.service, r.amount) for r in rows] == [
            ("111", "Amazon EC2", 15.0),
            ("111", "Amazon RDS", 4.0),
        ]

    def test_orders_by_descending_cost(self):
        pages = [ce_page(("111", "A", 1.0), ("222", "B", 9.0))]
        assert [r.account for r in acr.aggregate_ce_groups(pages)] == ["222", "111"]

    def test_missing_service_key_becomes_all(self):
        pages = [{"ResultsByTime": [{"Groups": [{"Keys": ["111"], "Metrics": {}}]}]}]
        row = acr.aggregate_ce_groups(pages)[0]
        assert (row.service, row.amount, row.unit) == ("all", 0.0, "USD")

    def test_resource_rows_sum_daily_buckets(self):
        pages = [ce_page(("i-abc", 1.5)), ce_page(("i-abc", 2.5), ("i-def", 0.25))]
        rows = acr.aggregate_resource_groups(pages, "egobid")
        assert [(r.resource_id, r.amount) for r in rows] == [("i-abc", 4.0), ("i-def", 0.25)]
        assert {r.account for r in rows} == {"egobid"}

    def test_zero_cost_instance_ids_are_kept(self):
        rows = acr.aggregate_resource_groups([ce_page(("i-abc", 0.0))], "acct")
        assert [r.resource_id for r in rows] == ["i-abc"]

    def test_zero_cost_non_instance_rows_are_dropped(self):
        rows = acr.aggregate_resource_groups([ce_page(("NoResourceId", 0.0))], "acct")
        assert rows == []


class TestNameTags:
    def test_matches_bare_and_arn_style_ids(self):
        rows = [
            acr.InstanceRow("a", "i-123", "", "", 1.0, "USD"),
            acr.InstanceRow("a", "arn:aws:ec2:ap-east-1:1:instance/i-456", "", "", 1.0, "USD"),
            acr.InstanceRow("a", "vol-999", "", "", 1.0, "USD"),
        ]
        acr.apply_instance_names(rows, {"i-123": "egobid-prod", "i-456": "ebid-prod"})
        assert [r.name for r in rows] == ["egobid-prod", "ebid-prod", ""]


CUR_HEADER = [
    "identity/LineItemId",
    "lineItem/UsageAccountId",
    "lineItem/ResourceId",
    "lineItem/UnblendedCost",
    "product/instanceType",
]


class TestCurFromS3:
    def test_parses_bucket_and_prefix(self):
        assert acr.parse_s3_uri("s3://bkt/cost-report/") == ("bkt", "cost-report")
        assert acr.parse_s3_uri("s3://bkt") == ("bkt", "")

    @pytest.mark.parametrize("bad", ["bkt/prefix", "https://bkt", "s3://"])
    def test_rejects_non_s3_uri(self, bad):
        with pytest.raises(acr.ReportError):
            acr.parse_s3_uri(bad)

    def test_unqualified_uri_applies_to_every_account(self):
        assert acr.parse_cur_s3_args(["s3://bkt/cost-report"]) == {"": "s3://bkt/cost-report"}

    def test_named_uris_are_scoped_per_account(self):
        mapping = acr.parse_cur_s3_args(
            ["egobid=s3://a/cost-report", "storytellar=s3://b/cost-report"]
        )
        assert mapping == {"egobid": "s3://a/cost-report", "storytellar": "s3://b/cost-report"}

    def test_rejects_value_that_is_not_an_s3_uri(self):
        with pytest.raises(acr.ReportError, match="NAME="):
            acr.parse_cur_s3_args(["egobid=/local/path"])

    def test_report_name_defaults_to_last_prefix_segment(self):
        assert acr.cur_report_name_of("s3://bkt/exports/cost-report/") == "cost-report"

    def test_manifest_key_uses_billing_period(self):
        key = acr.cur_manifest_key("cost-report", "cost-report", "2026-06")
        assert key == "cost-report/cost-report/20260601-20260701/cost-report-Manifest.json"

    def test_manifest_key_without_prefix(self):
        assert acr.cur_manifest_key("", "cur", "2026-12") == "cur/20261201-20270101/cur-Manifest.json"

    def test_sums_hourly_line_items_per_resource(self):
        rows = [
            CUR_HEADER,
            ["1", "444", "i-abc", "0.5", "t4g.small"],
            ["2", "444", "i-abc", "0.25", "t4g.small"],
            ["3", "444", "i-def", "1.0", "t3.small"],
        ]
        out = acr.aggregate_cur_rows(rows, "egobid")
        assert [(r.resource_id, r.amount, r.instance_type) for r in out] == [
            ("i-def", 1.0, "t3.small"),
            ("i-abc", 0.75, "t4g.small"),
        ]
        assert {r.account for r in out} == {"444"}

    def test_resolves_columns_by_name_not_position(self):
        shuffled = ["lineItem/UnblendedCost", "lineItem/ResourceId", "lineItem/UsageAccountId"]
        out = acr.aggregate_cur_rows([shuffled, ["2.0", "i-xyz", "999"]], "s")
        assert (out[0].resource_id, out[0].amount, out[0].account) == ("i-xyz", 2.0, "999")

    def test_skips_untagged_and_unparseable_rows(self):
        rows = [
            CUR_HEADER,
            ["1", "444", "", "9.0", ""],
            ["2", "444", "i-abc", "not-a-number", ""],
            ["3", "444", "i-abc", "3.0", "t3.micro"],
        ]
        out = acr.aggregate_cur_rows(rows, "s")
        assert [(r.resource_id, r.amount) for r in out] == [("i-abc", 3.0)]

    def test_missing_cost_column_is_actionable(self):
        with pytest.raises(acr.ReportError, match="lineItem/UnblendedCost"):
            acr.aggregate_cur_rows([["identity/LineItemId", "lineItem/ResourceId"]], "s")

    def test_min_cost_drops_dust_rows(self):
        rows = [CUR_HEADER, ["1", "444", "i-dust", "0.0", ""], ["2", "444", "i-real", "5.0", ""]]
        out = acr.aggregate_cur_rows(rows, "s", min_cost=0.0)
        assert [r.resource_id for r in out] == ["i-real"]

    def test_streams_gzipped_report_keys_from_manifest(self):
        def gz(rows):
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as handle:
                handle.write("\n".join(",".join(r) for r in rows).encode())
            return io.BytesIO(buf.getvalue())

        manifest = json.dumps(
            {"reportKeys": ["cost-report/p/asm/cost-report-1.csv.gz", "cost-report/p/asm/cost-report-2.csv.gz"]}
        ).encode()
        bodies = {
            "cost-report/cost-report/20260601-20260701/cost-report-Manifest.json": io.BytesIO(manifest),
            "cost-report/p/asm/cost-report-1.csv.gz": gz([CUR_HEADER, ["1", "444", "i-abc", "1.5", "t4g.small"]]),
            "cost-report/p/asm/cost-report-2.csv.gz": gz([CUR_HEADER, ["2", "444", "i-abc", "2.5", "t4g.small"]]),
        }
        s3 = mock.Mock()
        s3.get_object.side_effect = lambda Bucket, Key: {"Body": bodies[Key]}
        session = mock.Mock()
        session.client.return_value = s3

        rows = acr.fetch_resource_costs_cur_s3(
            session,
            bucket="bkt",
            prefix="cost-report",
            report_name="cost-report",
            month="2026-06",
            spec_name="egobid",
        )
        assert [(r.resource_id, r.amount) for r in rows] == [("i-abc", 4.0)]

    def test_unreadable_manifest_names_the_key(self):
        s3 = mock.Mock()
        s3.get_object.side_effect = RuntimeError("AccessDenied")
        session = mock.Mock()
        session.client.return_value = s3
        with pytest.raises(acr.ReportError, match="cost-report-Manifest.json"):
            acr.fetch_resource_costs_cur_s3(
                session,
                bucket="bkt",
                prefix="cost-report",
                report_name="cost-report",
                month="2026-06",
                spec_name="egobid",
            )


class TestCurQuery:
    def test_filters_partitions_for_the_month(self):
        sql = acr.build_cur_query("cur_db", "cur", "2026-06")
        assert "FROM cur_db.cur" in sql
        assert "year = '2026'" in sql
        assert "month = '6'" in sql

    def test_groups_by_account_and_resource(self):
        sql = acr.build_cur_query("d", "t", "2026-11")
        assert "line_item_usage_account_id" in sql
        assert "line_item_resource_id" in sql
        assert "month = '11'" in sql

    def test_athena_header_row_is_not_data(self):
        pages = [
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": c} for c in
                                  ("account_id", "resource_id", "instance_type", "cost")]},
                        {"Data": [{"VarCharValue": c} for c in
                                  ("444", "i-abc", "t4g.small", "12.5")]},
                    ]
                }
            },
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": c} for c in
                                  ("444", "i-def", "t3.small", "30.0")]},
                    ]
                }
            },
        ]
        rows = acr.parse_athena_rows(pages, "egobid")
        assert [(r.resource_id, r.instance_type, r.amount) for r in rows] == [
            ("i-def", "t3.small", 30.0),
            ("i-abc", "t4g.small", 12.5),
        ]
        assert {r.account for r in rows} == {"444"}


class TestRendering:
    def make_report(self):
        report = acr.AccountReport(spec_name="egobid", account_id="444")
        report.services = [
            acr.CostRow("444", "Amazon EC2", 120.0, "USD"),
            acr.CostRow("444", "Amazon RDS", 40.5, "USD"),
        ]
        report.instances = [acr.InstanceRow("444", "i-abc", "t4g.small", "egobid-prod", 60.0, "USD")]
        report.instance_source = "athena"
        report.instance_window = "2026-06-01 → 2026-07-01 (full month)"
        return report

    def test_markdown_has_totals_services_and_instances(self):
        out = acr.render_markdown("2026-06", [self.make_report()], "2026-07-29 08:00 UTC")
        assert "# AWS cost report — 2026-06" in out
        assert "| egobid | 444 | 160.50 |" in out
        assert "| 444 | Amazon EC2 | 120.00 |" in out
        assert "| 444 | i-abc | t4g.small | egobid-prod | 60.00 |" in out
        assert "**160.50**" in out

    def test_partial_instance_source_is_flagged(self):
        report = self.make_report()
        report.instance_source = "cost-explorer-14d"
        out = acr.render_markdown("2026-07", [report], "now")
        assert "PARTIAL" in out

    def test_errors_are_surfaced_in_the_report(self):
        report = acr.AccountReport(spec_name="broken", errors=["cost explorer: AccessDenied"])
        out = acr.render_markdown("2026-06", [report], "now")
        assert "> ERROR: cost explorer: AccessDenied" in out
        assert "| broken | — | no data |" in out

    def test_payload_rounds_and_keeps_provenance(self):
        payload = acr.report_payload("2026-06", [self.make_report()], "now")
        account = payload["accounts"][0]
        assert account["total"] == 160.5
        assert account["instance_source"] == "athena"
        assert account["instances"][0]["name"] == "egobid-prod"


class TestPagination:
    def test_follows_next_page_token_then_stops(self):
        calls = []

        def call(**kwargs):
            calls.append(kwargs)
            if "NextPageToken" not in kwargs:
                return {"n": 1, "NextPageToken": "t1"}
            return {"n": 2}

        pages = acr.paginate(call, Granularity="MONTHLY")
        assert [p["n"] for p in pages] == [1, 2]
        assert calls[1]["NextPageToken"] == "t1"


class TestMakeSession:
    def test_env_prefix_reads_prefixed_credentials(self, monkeypatch):
        monkeypatch.setenv("EGOBID_AWS_ACCESS_KEY_ID", "AKIA_TEST")
        monkeypatch.setenv("EGOBID_AWS_SECRET_ACCESS_KEY", "secret_test")
        fake_boto3 = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
            acr.make_session(acr.AccountSpec("egobid", "env", "EGOBID_AWS"))
        kwargs = fake_boto3.Session.call_args.kwargs
        assert kwargs["aws_access_key_id"] == "AKIA_TEST"
        assert kwargs["aws_secret_access_key"] == "secret_test"
        assert kwargs["aws_session_token"] is None

    def test_missing_prefixed_credentials_is_actionable(self, monkeypatch):
        monkeypatch.delenv("NOPE_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("NOPE_AWS_ACCESS_KEY_ID", raising=False)
        with mock.patch.dict(sys.modules, {"boto3": mock.MagicMock()}):
            with pytest.raises(acr.ReportError, match="NOPE_ACCESS_KEY_ID"):
                acr.make_session(acr.AccountSpec("nope", "env", "NOPE"))

    def test_profile_source_uses_named_profile(self):
        fake_boto3 = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
            acr.make_session(acr.AccountSpec("main", "profile", "prod"))
        assert fake_boto3.Session.call_args.kwargs == {"profile_name": "prod"}


class TestMain:
    def test_writes_month_named_files_and_flags_errors(self, tmp_path):
        report = acr.AccountReport(spec_name="egobid", account_id="444")
        report.services = [acr.CostRow("444", "Amazon EC2", 12.0, "USD")]
        report.instances = [acr.InstanceRow("444", "i-abc", "t4g.small", "prod", 12.0, "USD")]
        report.errors = ["athena: no such table"]
        with mock.patch.object(acr, "collect_account", return_value=report):
            code = acr.main(
                ["--month", "2026-06", "--out-dir", str(tmp_path), "--account", "egobid:env=X", "--quiet"]
            )
        assert code == 3
        payload = json.loads((tmp_path / "aws-cost-2026-06.json").read_text())
        assert payload["month"] == "2026-06"
        assert (tmp_path / "aws-cost-2026-06.md").exists()
        rows = list(csv.reader((tmp_path / "aws-cost-2026-06-by-instance.csv").read_text().splitlines()))
        assert rows[0][:3] == ["source", "account", "resource_id"]
        assert rows[1][2] == "i-abc"

    def test_clean_run_exits_zero(self, tmp_path):
        report = acr.AccountReport(spec_name="a", services=[acr.CostRow("1", "S", 1.0, "USD")])
        with mock.patch.object(acr, "collect_account", return_value=report):
            assert acr.main(["--month", "2026-06", "--out-dir", str(tmp_path), "--quiet"]) == 0

    def test_bad_month_exits_two(self, tmp_path):
        assert acr.main(["--month", "nope", "--out-dir", str(tmp_path), "--quiet"]) == 2

    def test_account_failure_does_not_abort_the_report(self, tmp_path):
        with mock.patch.object(acr, "collect_account", side_effect=RuntimeError("bad key")):
            code = acr.main(
                ["--month", "2026-06", "--out-dir", str(tmp_path), "--account", "a:env=X", "--quiet"]
            )
        assert code == 3
        assert "bad key" in (tmp_path / "aws-cost-2026-06.md").read_text()


class TestCollectAccount:
    def args(self, **overrides):
        defaults = dict(
            month="2026-07",
            instances="auto",
            cur_s3={},
            cur_report_name="",
            athena_database="",
            athena_table="",
            athena_output="",
            athena_region="us-east-1",
            ec2_regions=[],
        )
        defaults.update(overrides)
        return mock.Mock(**defaults)

    def test_falls_back_to_ce_resource_window_without_cur(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]), \
             mock.patch.object(
                 acr, "fetch_resource_costs_ce",
                 return_value=[acr.InstanceRow("444", "i-abc", "", "", 3.0, "USD")],
             ) as ce_fetch:
            report = acr.collect_account(spec, self.args(), "2026-07-01", "2026-08-01", date(2026, 7, 29))
        assert report.instance_source == "cost-explorer-14d"
        assert "partial month" in report.instance_window
        assert ce_fetch.call_args.args[1:3] == ("2026-07-15", "2026-07-29")

    def test_cur_s3_wins_over_athena_and_ce(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        args = self.args(
            cur_s3={"egobid": "s3://bkt/cost-report"},
            cur_report_name="cost-report",
            athena_database="db",
            athena_table="t",
            athena_output="s3://out/",
        )
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]), \
             mock.patch.object(
                 acr, "fetch_resource_costs_cur_s3",
                 return_value=[acr.InstanceRow("444", "i-abc", "t4g.small", "", 42.0, "USD")],
             ), \
             mock.patch.object(acr, "fetch_resource_costs_athena") as athena_fetch, \
             mock.patch.object(acr, "fetch_resource_costs_ce") as ce_fetch:
            report = acr.collect_account(spec, args, "2026-07-01", "2026-08-01", date(2026, 7, 29))
        assert report.instance_source == "cur-s3"
        assert "full month" in report.instance_window
        athena_fetch.assert_not_called()
        ce_fetch.assert_not_called()

    def test_cur_s3_failure_falls_back_to_ce_window(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        args = self.args(cur_s3={"": "s3://bkt/cost-report"}, cur_report_name="cost-report")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]), \
             mock.patch.object(
                 acr, "fetch_resource_costs_cur_s3", side_effect=acr.ReportError("AccessDenied")
             ), \
             mock.patch.object(
                 acr, "fetch_resource_costs_ce",
                 return_value=[acr.InstanceRow("444", "i-abc", "", "", 1.0, "USD")],
             ):
            report = acr.collect_account(spec, args, "2026-07-01", "2026-08-01", date(2026, 7, 29))
        assert report.instance_source == "cost-explorer-14d"
        assert any("cur in s3" in err for err in report.errors)

    def test_athena_wins_when_cur_is_configured(self):
        spec = acr.AccountSpec("payer", "profile", "default")
        args = self.args(athena_database="db", athena_table="t", athena_output="s3://out/")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="111"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]), \
             mock.patch.object(
                 acr, "fetch_resource_costs_athena",
                 return_value=[acr.InstanceRow("111", "i-abc", "t3.small", "", 9.0, "USD")],
             ), \
             mock.patch.object(acr, "fetch_resource_costs_ce") as ce_fetch:
            report = acr.collect_account(spec, args, "2026-07-01", "2026-08-01", date(2026, 7, 29))
        assert report.instance_source == "athena"
        assert "full month" in report.instance_window
        ce_fetch.assert_not_called()

    def test_old_month_without_cur_explains_the_gap(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]):
            report = acr.collect_account(
                spec, self.args(month="2026-05"), "2026-05-01", "2026-06-01", date(2026, 7, 29)
            )
        assert report.instance_source == "none"
        assert any("14-day" in err for err in report.errors)

    def test_instances_none_skips_resource_calls(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(acr, "fetch_account_service_costs", return_value=[]), \
             mock.patch.object(acr, "fetch_resource_costs_ce") as ce_fetch:
            report = acr.collect_account(
                spec, self.args(instances="none"), "2026-07-01", "2026-08-01", date(2026, 7, 29)
            )
        ce_fetch.assert_not_called()
        assert report.instances == []

    def test_service_call_failure_is_recorded_not_raised(self):
        spec = acr.AccountSpec("egobid", "env", "X")
        with mock.patch.object(acr, "make_session", return_value=mock.Mock()), \
             mock.patch.object(acr, "account_id_of", return_value="444"), \
             mock.patch.object(
                 acr, "fetch_account_service_costs", side_effect=RuntimeError("AccessDenied")
             ), \
             mock.patch.object(acr, "fetch_resource_costs_ce", return_value=[]):
            report = acr.collect_account(
                spec, self.args(instances="none"), "2026-07-01", "2026-08-01", date(2026, 7, 29)
            )
        assert report.services == []
        assert "AccessDenied" in report.errors[0]
