# CloudWatch Logs Insights — query cookbook

Save each of these as a named query in the CloudWatch console
(`Logs Insights → Saved queries`) so on-call can re-run them with one
click during an incident.

All queries target the Lambda log group
`/aws/lambda/<FunctionName>`.

## 1. Recent failures with row counts

```
fields @timestamp, request_id, event, Stage, Errors, RowsIngested, RowsDropped
| filter (event = "stage_done" and Errors = 1)
   or event = "pipeline_failed"
| sort @timestamp desc
| limit 50
```

Pivots: failures within the last 24h, broken down by which stage.
`success` is a JSON boolean in the EMF record; we filter on the
numeric `Errors = 1` instead, which is unambiguous in Logs Insights.

## 2. One invocation, full timeline

Drop the `request_id` from the previous query in here:

```
fields @timestamp, event, Stage, success, Latency, RowsIngested, @message
| filter request_id = "PASTE-ME"
| sort @timestamp asc
```

Returns the stage_done records, the pipeline_done / pipeline_failed
summary, and the exception traceback (which goes to `@message` via
`logger.exception`).

## 3. p50 / p99 / p99.9 latency per stage, last hour

```
fields @timestamp, Stage, Latency
| filter event = "stage_done"
| stats pct(Latency, 50) as p50,
        pct(Latency, 99) as p99,
        pct(Latency, 99.9) as p999,
        count(*) as samples
  by Stage
| sort p99 desc
```

If your alarm fired but the metric is averaged, this is the query
that confirms a real p99 spike vs an averaged-down view.

## 4. "Did we run today at all?"

```
fields @timestamp
| filter event = "pipeline_done" or event = "pipeline_failed"
| stats count(*) as invocations by bin(1h) as hour
| sort hour desc
```

Empty result for a recent hour → the pipeline didn't run, which is
what `empty-output` with `TreatMissingData=breaching` is supposed to
catch. If the alarm didn't fire, the alarm itself is broken.

## 5. Rows ingested vs dropped, last day

```
fields @timestamp, RowsIngested, RowsDropped
| filter event = "pipeline_done"
| stats sum(RowsIngested) as ingested,
        sum(RowsDropped) as dropped,
        sum(RowsDropped) / sum(RowsIngested + RowsDropped) as drop_ratio
  by bin(1h)
| sort @timestamp desc
```

A sudden jump in `drop_ratio` is a schema-drift early warning — the
column exists but every row has a NaN.

## 6. Stage error rate (proper failure-rate metric)

```
fields Stage, Errors
| filter event = "stage_done"
| stats sum(Errors) as failures, count(*) as total
  by Stage
| stats failures, total, failures / total * 100 as failure_pct by Stage
| sort failure_pct desc
```

The CloudWatch alarm watches absolute count (cheaper, simpler); this
query is the right one for the post-incident write-up.

## 7. Cold-start signature

```
fields @timestamp, @initDuration, @duration, @maxMemoryUsed
| filter @type = "REPORT"
| stats count(*) as invocations,
        avg(@initDuration) as init_ms,
        avg(@duration) as dur_ms,
        max(@maxMemoryUsed) as max_mem
  by bin(1h)
| sort @timestamp desc
```

`@initDuration` is non-zero only on cold starts — pair with
`Stage=read Latency` to see whether warm starts are dominated by S3
or by Python init. Useful when deciding whether provisioned
concurrency would actually help.
