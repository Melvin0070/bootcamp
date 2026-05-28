# Runbook — FossilRAG ingestion pipeline

When the composite alarm `fossilrag-<fn>-health` fires, work through
this in order.

## 1. Identify the failing alarm

The composite alarm rolls up three children:

| Child alarm | What it tells you |
|---|---|
| `fossilrag-<fn>-failure-rate` | A pipeline stage is throwing exceptions. |
| `fossilrag-<fn>-p99-latency` | A stage is slow (likely upstream — S3 throttling, large object). |
| `fossilrag-<fn>-empty-output` | Pipeline returned success but produced no rows (silent schema drift, or didn't run at all). |

Open the dashboard `fossilrag-<fn>` (DashboardUrl is in the
CloudFormation stack output). The "Per-stage errors" and "Per-stage
latency" panels tell you which stage is at fault.

## 2. Failure-rate alarm — find the offending invocation

Logs Insights query:

```
fields @timestamp, request_id, Stage, Errors, Latency, @message
| filter event = "stage_done" and Errors = 1
| sort @timestamp desc
| limit 50
```

Pick a `request_id` from the result. Then drill into the full
invocation timeline:

```
fields @timestamp, event, Stage, @message
| filter request_id = "<paste-request-id>"
| sort @timestamp asc
```

The `pipeline_failed` record at the end carries the exception in
`@message`. Common causes:

- **`ValueError: missing required columns`** → upstream changed the
  CSV header. Check the latest object in the input bucket; coordinate
  with the data ingest team. **Mitigation:** revert the upstream
  change, or extend `REQUIRED_COLUMNS` and ship a follow-up PR.
- **`ClientError: NoSuchKey`** → S3 event was for an object that's
  already been deleted. Usually a re-deploy race. **Mitigation:**
  retry the invocation manually; if it recurs, check S3 lifecycle.
- **`ClientError: SlowDown`** → S3 throttling on the read or upload.
  Will resolve itself if the burst stops; if sustained, raise the
  Lambda's reserved concurrency to throttle the producer side.

## 3. p99 latency alarm — find the slow stage

```
fields @timestamp, Stage, Latency
| filter event = "stage_done"
| stats pct(Latency, 99) as p99, pct(Latency, 50) as p50, count(*) as n by Stage
| sort p99 desc
```

The dashboard's "Per-stage latency p50 / p99 (ms)" panel shows the
same data over time. If the spike is in `read` or `upload`, the cause
is almost always S3 (large object, throttling, region change).
`normalise` slowness usually means the input got bigger — check
`RowsIngested` on the same dashboard.

## 4. Empty-output alarm — silent failure path

This is the alarm that catches the failure mode the broken baseline
missed. It fires when the pipeline ran but produced zero rows.

Two possibilities:

a. **Pipeline didn't run at all.** Check the S3 event source mapping
   on the Lambda console — was it disabled or detached? Did the input
   bucket's event notification get wiped?

b. **Pipeline ran but dropped every row.** Compare `RowsDropped` to
   `RowsIngested`. If `RowsDropped` is also high → schema drift in
   the species column (every row has empty/missing species). Pull
   the latest input CSV:

   ```bash
   aws s3 cp s3://<bucket>/<key> - | head -3
   ```

   …and check whether the `species` column is empty or renamed.

## 5. Pipeline didn't run at all (no metrics at all)

The empty-output alarm fires with `TreatMissingData=breaching`, which
covers this. Sanity-check:

1. Lambda console → Configuration → Triggers — is the S3 trigger
   still attached?
2. CloudTrail event history for the Lambda function name — was it
   updated / deleted recently?
3. The IAM role — did Activity 10's deploy clobber it?

## 6. After the incident

- Add a new metric or alarm if this failure mode wasn't visible.
- Update this runbook if the diagnosis steps were wrong or missing.
- If the same failure recurs three times, write a follow-up issue
  to fix the root cause (not the symptom).

## Quick reference — re-deploy the observability stack

```bash
aws cloudformation deploy \
  --template-file infra/observability.yaml \
  --stack-name fossilrag-pipeline-observability \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      FunctionName=fossilrag-ingestion-staging \
      AlarmNotificationTopic=arn:aws:sns:us-east-1:<acct>:fossilrag-oncall
```

The template is idempotent — re-running on the same parameter set is
a no-op.
