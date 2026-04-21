# Activity 6 — Scala ETL Schema-Evolution Fix

## Problem Diagnosis

The original Scala ETL read raw fossil-specimen Parquet with a rigid, hardcoded
`StructType` contract. Whenever the upstream producer added a new column (e.g.
`discoverer`, `geological_period`) the job either dropped it silently — because
Spark's default Parquet read picks one file's schema and ignores the rest — or
threw an `AnalysisException` at read time, killing every downstream consumer of
the enriched dataset.

| Anti-pattern | Symptom | Root cause |
|---|---|---|
| No `mergeSchema` | Columns in newer files silently dropped | Spark 1.5+ disables schema merging by default |
| Frozen `StructType` enforced via `.schema(...)` | `AnalysisException` on any upstream field | Contract is a rigid equality check, not a minimum |
| No default values for missing columns | Old Parquet reads surface nulls into downstream SQL | No `coalesce` / `lit(default).cast(...)` plumbing |
| No schema validator | Drift is invisible until prod breaks | No diff between expected and actual schema logged |
| Hardcoded `/data/fossils/...` paths | Can't deploy to staging / prod without code edits | No env-var plumbing |
| `println` logging | Unqueryable noise in CloudWatch | No structured fields, no log level |

---

## Architecture: Before vs After

### Before (broken)

```
Upstream producer
   │ writes v2 Parquet (adds `discoverer`, `geological_period`)
   ▼
┌───────────────────────────────────────────┐
│ spark.read.schema(EXPECTED_SCHEMA).parquet │ ← frozen StructType
└───────────────────────────────────────────┘
   │
   ├── file has extra column  → AnalysisException → job dies
   ├── file missing column    → null propagates → NPE downstream
   └── file-set is mixed v1+v2 → mergeSchema off → Spark picks ONE file's
                                                    schema, silently drops
                                                    columns from the rest
   ▼
┌─────────────────────┐
│  enrich (selectExpr)│ ← assumes all columns exist, not null
└─────────────────────┘
   ▼
   ✗ downstream consumers crash on unexpected schema
   ✗ no warning, no audit trail — println goes to stdout
```

### After (fixed)

```
Upstream producer
   │ writes v2 Parquet (adds `discoverer`, `geological_period`)
   ▼
┌─────────────────────────────────────────────────────────────┐
│ spark.read.option("mergeSchema", "true").parquet(INPUT_PATH)│
│   → schema is the UNION across every file                   │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ validateSchema                                              │
│   • new fields          → logger.warn (policy=additive_ok)  │
│   • missing fields      → logger.warn (defaults applied)    │
│   • type widening       → allowed (Int→Long, Float→Double)  │
│   • type narrow / swap  → IllegalStateException (breaking)  │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ applyContract = foldLeft over REQUIRED_COLUMNS with         │
│                  withDefaultIfMissing                        │
│   • column exists → coalesce(col, lit(default).cast(type))  │
│   • column absent → withColumn(name, lit(default).cast(...)) │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────┐         ┌──────────────────────────────┐
│ enrich (selectExpr) │ ──────▶ │ write.mode(overwrite).parquet │
│  safe: every col    │         │                              │
│  guaranteed to exist│         └──────────────────────────────┘
└─────────────────────┘
   ▼
   ✓ downstream consumers always see the full contract
   ✓ drift is logged in SLF4J key=value records
   ✓ only genuine breaking changes abort the job
```

---

## Schema Evolution Policy

The fixed ETL treats the declared `REQUIRED_COLUMNS` as a **minimum contract**,
not a rigid equality check. The policy is:

| Drift kind | Example | Action |
|---|---|---|
| Additive — upstream adds a new column | upstream adds `curator_notes` | **Warn.** Column is kept in the merged schema and passed through untouched. |
| Missing — old file lacks a current column | v1 Parquet has no `geological_period` | **Warn + default applied.** `withDefaultIfMissing` synthesises the column with `lit("unknown").cast(StringType)`. |
| Null values in existing column | `species` column present but row-level null | **Coalesce to default.** `coalesce(col("species"), lit("unknown"))`. |
| Type widening | upstream widens `count: Int` → `count: Long` | **Allowed.** `isCompatibleType` permits Int→Long, Int/Long→Double, Float→Double. |
| Type narrowing / incompatible type | upstream changes `age_ma: Double` → `age_ma: String` | **Fail loudly.** `IllegalStateException` with column name + expected + actual types. |
| Column rename | `species` → `taxon_name` | **Fail loudly** (as a missing field with no default that makes sense). Rename policy: *deprecate + add alias* — never in-place rename. |

Reasoning: in a FossilRAG-style pipeline, the cost of a silent column drop or a
partial refresh is much higher than the cost of a noisy warning. Breaking
changes (narrowing / rename) are rare and deserve to page the on-call engineer.

---

## Trade-off Table

| Decision | Chosen | Alternative | Reasoning |
|---|---|---|---|
| Schema merging | `mergeSchema=true` at read site | Global `spark.sql.parquet.mergeSchema=true` | Scoped per job — no surprise overhead for other jobs in the same SparkSession |
| Contract shape | `Seq[ColumnContract]` + `foldLeft` | Rigid `StructType` | Minimum-schema + defaults is additive; a StructType is an equality check |
| Missing-column default | Per-column `default: Any` + `.cast(type)` | Always `null` | `null` silently propagates into downstream SQL; typed defaults are debuggable (`"unknown"` shows up in dashboards) |
| Existing-null handling | `coalesce(col, lit(default))` | Leave nulls | Downstream `UPPER(null)` / aggregates would drop rows — coalesce makes behaviour explicit |
| Drift response | WARN + continue | FAIL fast | Additive drift is the common case; halting the pipeline on every upstream change creates merge bottlenecks |
| Breaking-change response | `IllegalStateException` | Log + continue | Silently eating a type change corrupts every downstream consumer — fail-loud beats fail-late |
| Type widening | Int→Long, Int/Long→Double, Float→Double | Strict equality | Widening never loses information; common with upstream moving from a 32-bit to 64-bit counter |
| Logging | SLF4J `key=value` | JSON Logstash encoder | `key=value` is ingestible by CloudWatch Insights and Datadog out of the box with zero extra deps |
| Config source | `sys.env.getOrElse` | Typesafe Config / Hocon | One dependency fewer; env vars map cleanly to 12-factor + Kubernetes Downward API |

---

## Edge Cases Handled

| Case | Behaviour |
|---|---|
| Upstream adds a new column (e.g. `curator_notes`) | `mergeSchema` surfaces it; `validateSchema` WARNs; enrich ignores it; downstream can opt in when ready |
| Upstream removes a column that's in the contract | `applyContract` synthesises it with the default; WARN logged for audit |
| Mixed v1 + v2 Parquet files under INPUT_PATH | `mergeSchema` unions both schemas; missing-in-v1 columns get defaults for v1 rows |
| Column exists but row value is null | `coalesce` replaces with the typed default |
| Primary-key default (`specimen_id`) | Contract default is `null` → skip coalesce so we never forge an id for a broken upstream row |
| Upstream widens `Int` → `Long` | `isCompatibleType` returns true → job continues, Spark handles the cast |
| Upstream swaps `Double` → `String` | `IllegalStateException` with column name and both types; job aborts before write |
| Partition column evolution | `mergeSchema` handles discovered-partition columns the same way as data columns |
| First run with zero files | Spark returns an empty DataFrame; `applyContract` still adds every contract column — downstream schema is stable even on cold start |
| ETL process crashes mid-write | `.mode("overwrite")` on a fresh OUTPUT_PATH is idempotent; next run re-reads the same INPUT_PATH with the same merged schema |

---

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `INPUT_PATH` | Yes (for non-local) | `/data/fossils/raw` | Path / S3 URI to the raw Parquet |
| `OUTPUT_PATH` | Yes (for non-local) | `/data/fossils/enriched` | Path / S3 URI for the enriched Parquet |
| `APP_NAME` | No | `SpecimenETL` | Spark UI application name |
| `SPARK_MASTER` | No | `local[*]` | Spark master URL (`yarn`, `k8s://...`, `local[*]`) |
| `LOG_LEVEL` | No | `WARN` | Spark's internal log level; the ETL's own SLF4J logs are independent |

---

## Structured Log Schema

Every record is a single SLF4J line with key=value fields so CloudWatch Insights
and Datadog can parse it without a custom grok pattern:

```
event=read_start path=s3://fossilrag-raw/specimens/ merge_schema=true
event=schema_drift kind=new_fields fields=curator_notes,isotope_ratio action=warn policy=additive_allowed
event=column_missing column=geological_period default=unknown type=string action=synthesised
event=write_start rows=42315 path=s3://fossilrag-enriched/specimens/
event=etl_complete status=success rows=42315
```

Query example (CloudWatch Logs Insights):

```
fields @timestamp, @message
| filter @message like /event=schema_drift/
| sort @timestamp desc
```

---

## Rollback Plan

The output is written with `mode("overwrite")` to a path the ETL fully owns.
To roll back after a bad upstream change:

1. Keep the previous day's enriched Parquet under an `OUTPUT_PATH_YYYYMMDD/`
   snapshot (handled by the S3 lifecycle rule in Activity 4's IaC).
2. Re-run the ETL with `INPUT_PATH` pointing at the *previous* raw partition.
3. If the upstream change was a breaking type swap, the `IllegalStateException`
   will prevent the corrupted output from ever being written in the first place.
