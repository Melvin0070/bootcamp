# Activity 6: Fix a Schema-Breaking Scala ETL

**Week:** 1 | **Day:** 6 | **Course alignment:** AWS Technical Essentials

## Problem Statement

A Scala ETL pipeline **breaks downstream consumers** whenever the input Parquet
schema changes (e.g., a new column is added upstream). The broken job enforces
a frozen `StructType`, disables schema merging, and uses `println` for logging,
so drift is either silently dropped or loudly crashes the pipeline — no
backward-compatibility strategy exists.

## What to Fix

- [x] Implement **schema merging** (`mergeSchema = true` in Spark Parquet reads)
- [x] Add **default values** for new nullable columns so old readers don't break
- [x] Write a schema validation step that warns (not fails) on new fields
- [x] Document the schema evolution policy

## Acceptance Criteria

- Adding a new column upstream does not break downstream consumers
- Old data without the new column reads cleanly with null/default values
- Schema changes are logged and visible

## What Was Fixed

| # | Anti-pattern (broken) | Fix applied | Impact |
|---|---|---|---|
| 1 | No `mergeSchema` on Parquet read | `.option("mergeSchema", "true")` at the read site | Columns present in only some files are unioned into the read schema instead of silently dropped |
| 2 | Frozen `StructType` enforced via `.schema(...)` | `Seq[ColumnContract]` treated as a **minimum** schema, not an equality check | Upstream can add columns freely without throwing `AnalysisException` |
| 3 | No default values for missing columns | `withDefaultIfMissing` synthesises the column with `lit(default).cast(type)`; existing nulls are `coalesce`'d | Downstream consumers always see a complete row; `null` never propagates silently |
| 4 | No schema validator | `validateSchema` compares actual vs. expected fields; WARN on additive drift, FAIL only on type narrowing | Drift is observable via logs and alertable; breaking changes abort before bad data is written |
| 5 | Hardcoded paths + Spark config | `sys.env.getOrElse(...)` for `INPUT_PATH`, `OUTPUT_PATH`, `APP_NAME`, `SPARK_MASTER`, `LOG_LEVEL` | One codebase works across dev / staging / prod / EMR / EKS |
| 6 | `println` logging | SLF4J `LoggerFactory` with `event=... key=value` records | CloudWatch Insights / Datadog can query `event=schema_drift`, `column=age_ma`, etc. |

## Schema Evolution Policy (summary)

| Drift kind | Action |
|---|---|
| New column upstream | **Warn** and pass through |
| Column missing in old files | **Warn** and apply typed default |
| Null values in a contract column | **Coalesce** to default |
| Type widening (`Int`→`Long`, `Float`→`Double`) | **Allow** |
| Type narrowing / incompatible type | **Fail** with `IllegalStateException` |
| Column rename | **Fail** — policy is *deprecate + add alias*, never in-place rename |

Full policy, trade-offs, and edge-case handling live in
[`docs/architecture.md`](docs/architecture.md).

## How to Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: **38 tests passing** across 7 test classes.

Tests use regex / source-file analysis against `broken/SpecimenETL.scala` and
`SpecimenETL.scala`, so no sbt / JVM toolchain is required to validate the fix.

## Layout

```
activity-06-scala-etl/
├── broken/
│   └── SpecimenETL.scala    # Original, anti-patterns intact
├── SpecimenETL.scala        # Fixed version
├── build.sbt                # Minimal sbt build with spark-sql + slf4j
├── .env.example             # Documents every env var
├── docs/
│   └── architecture.md      # Before/after, policy, trade-offs, edge cases
├── tests/
│   └── test_etl.py          # 38 pytest assertions
├── requirements-dev.txt
└── README.md
```

## PR Checklist

- [x] Fix applied in `broken/SpecimenETL.scala` → working `SpecimenETL.scala` committed
- [x] `.env.example` documents every environment variable
- [x] 38 pytest assertions cover both broken anti-patterns and every fix
- [x] `docs/architecture.md` — before/after diagrams, trade-off table, edge cases, evolution policy
- [ ] 2–5 min video walkthrough (before/after)

## Notes

**Why `mergeSchema` is off by default:**
Spark scans every Parquet file's footer to compute the union schema. On a
petabyte of partitioned data that's expensive. Enabling it *per-read* (at the
`.option(...)` call site) scopes the cost to this one ETL rather than turning
it on globally via `spark.sql.parquet.mergeSchema`.

**Why defaults beat nulls:**
A column full of `null`s looks identical to a column that *should* be populated
but isn't. A typed default (`"unknown"`, `0.0`) is a loud signal in downstream
dashboards that something upstream needs attention — whereas nulls are usually
silently filtered out of aggregates.

**Why we fail loudly on type narrowing:**
If upstream changes `age_ma: Double` to `age_ma: String`, Spark will either
throw at read time or coerce in a way that corrupts downstream joins. Aborting
with an explicit `IllegalStateException` keeps the existing enriched dataset
intact until a human reviews the change.
