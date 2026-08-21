# Enabling per-instance cost data

Account-level and service-level costs work out of the box with `ce:GetCostAndUsage`.
Per-instance costs do not: AWS exposes them through three different mechanisms with
very different coverage. Pick one before promising a per-instance report.

| Mechanism | Coverage | What it needs | Script flag |
|---|---|---|---|
| CUR CSVs in S3, read directly | every month still in the bucket, hourly detail | an existing CUR with the `RESOURCES` schema element + `s3:GetObject` on its prefix | `--cur-s3` |
| CUR table in Athena | same as above | CUR + Glue table + Athena workgroup + results bucket | `--athena-*` |
| Cost Explorer resource-level | **last 14 days only** | payer-account opt-in + `ce:GetCostAndUsageWithResources` | `--instances ce` |

`--cur-s3` is the cheapest and the default preference in `auto` mode: no Athena
query charges, no Glue catalog, no console opt-in, and it works for closed months.

## 1. Check whether a CUR already exists

```bash
python -c "
import boto3, json
c = boto3.Session(profile_name='PROFILE').client('cur', region_name='us-east-1')
print(json.dumps(c.describe_report_definitions()['ReportDefinitions'], indent=2, default=str))"
```

A usable definition has `AdditionalSchemaElements: ["RESOURCES"]` — without it the
CSVs carry no `lineItem/ResourceId` and no per-instance attribution is possible.
Note the `S3Bucket`, `S3Prefix`, and `ReportName`: `--cur-s3
s3://<S3Bucket>/<S3Prefix>` and `--cur-report-name <ReportName>`.

## 2. IAM for reading an existing CUR

The reporting identity needs list + get on the report prefix only:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BUCKET",
      "Condition": {"StringLike": {"s3:prefix": ["PREFIX/*"]}}
    },
    {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::BUCKET/PREFIX/*"},
    {"Effect": "Allow", "Action": ["cur:DescribeReportDefinitions"], "Resource": "*"}
  ]
}
```

## 3. Creating a CUR when none exists

Member accounts of an organization can create a CUR covering their own usage — the
payer is not involved. The bucket needs a policy that lets the billing service
write to it before `PutReportDefinition` will validate.

```bash
BUCKET=my-cur-bucket-$(date +%s); ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3api create-bucket --bucket "$BUCKET" --region us-east-1
cat > /tmp/cur-bucket-policy.json <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Principal":{"Service":"billingreports.amazonaws.com"},
  "Action":["s3:GetBucketAcl","s3:GetBucketPolicy"],"Resource":"arn:aws:s3:::$BUCKET",
  "Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT"}}},
 {"Effect":"Allow","Principal":{"Service":"billingreports.amazonaws.com"},
  "Action":"s3:PutObject","Resource":"arn:aws:s3:::$BUCKET/*",
  "Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT"}}}]}
JSON
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/cur-bucket-policy.json
aws cur put-report-definition --region us-east-1 --report-definition "{
  \"ReportName\":\"cost-report\",\"TimeUnit\":\"HOURLY\",\"Format\":\"textORcsv\",
  \"Compression\":\"GZIP\",\"AdditionalSchemaElements\":[\"RESOURCES\"],
  \"S3Bucket\":\"$BUCKET\",\"S3Prefix\":\"cost-report\",\"S3Region\":\"us-east-1\",
  \"RefreshClosedReports\":true,\"ReportVersioning\":\"CREATE_NEW_REPORT\"}"
```

First delivery lands within ~24 hours and then refreshes several times a day.
Backfill is not possible — CUR starts from the month it was created, so months
before that only ever have account/service granularity.

## 4. Cost Explorer resource-level opt-in (the 14-day path)

Console only, and it must be done in the **payer** account:
Billing and Cost Management → Cost Management Preferences (or Cost Explorer →
Preferences) → enable resource-level / hourly granular data → Save. Data starts
accumulating after the toggle; it does not backfill, and it stays capped at 14
days. Grant `ce:GetCostAndUsageWithResources` to the reporting identity as well.
Identify the payer with:

```bash
aws organizations describe-organization --query Organization.MasterAccountId --output text
```
