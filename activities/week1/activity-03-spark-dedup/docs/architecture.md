# Architecture: Idempotent Spark Gold Layer

## Data Flow

```
┌──────────────────────────────────────┐
│           Silver Layer               │
│    S3 / Local Parquet                │
│  doc_id · chunk_id · text · ...      │
└──────────────────┬───────────────────┘
                   │ spark.read.parquet
                   ▼
          ┌────────────────┐
          │  Validation    │  ← raise ValueError if doc_id/chunk_id missing
          └────────┬───────┘
                   │
                   ▼
          ┌────────────────┐
          │ dropDuplicates │  ← collapse duplicate (doc_id, chunk_id) in batch
          │ (doc_id,       │    (Delta MERGE fails if source has dup keys)
          │  chunk_id)     │
          └────────┬───────┘
                   │
         ┌─────────┴──────────┐
   WRITE_FORMAT            WRITE_FORMAT
      = "delta"              = "parquet"
         │                        │
         ▼                        ▼
  ┌─────────────┐        ┌──────────────────┐
  │ _upsert_    │        │  _upsert_parquet  │
  │  delta()    │        │                  │
  │             │        │ 1. read existing │
  │ isDelta?    │        │ 2. left_anti join│
  │  YES →      │        │    on (doc_id,   │
  │  MERGE INTO │        │    chunk_id)     │
  │  NO  →      │        │ 3. union with    │
  │  init table │        │    incoming      │
  └──────┬──────┘        │ 4. overwrite     │
         │               └────────┬─────────┘
         └──────────┬─────────────┘
                    │
                    ▼
         ┌─────────────────────┐
         │     Gold Layer      │
         │  S3 / Local         │
         │  Zero duplicates    │
         │  Safe to re-run ∞   │
         └─────────────────────┘
```

## Idempotency Guarantee

**Key:** `(doc_id, chunk_id)` is the composite primary key.

Any number of re-runs with the same input produces identical output:
- Existing rows with matching keys → updated in-place
- New rows (no matching key)       → inserted
- Re-submitted rows (same key)     → updated, never duplicated

## Trade-offs

| Approach | Pros | Cons | Best for |
|----------|------|------|----------|
| **Delta MERGE INTO** | ACID, atomic, scales to billions of rows, supports time-travel and schema evolution | Requires `delta-spark` dep; S3 needs `_delta_log/`; slightly higher S3 API cost | Production on EMR / Databricks / Glue |
| **Parquet read-dedup-overwrite** | No extra dependencies, works with any plain Spark | Reads entire gold layer on each run — O(n) scan cost; not atomic (window between read and write) | Local dev, small datasets, or when Delta is unavailable |

**Recommendation:** Use Delta Lake in production. Use Parquet fallback (`WRITE_FORMAT=parquet`) for local development or CI environments without delta-spark.

---

## AWS Console Setup

### Option A — AWS EMR (Delta Lake MERGE INTO, production-grade)

**Step 1 — Create S3 buckets**

1. Open the [S3 console](https://s3.console.aws.amazon.com/s3/).
2. Click **Create bucket** → name it `fossil-silver-layer`, region `us-east-1`.
3. Repeat for `fossil-gold-layer`.
4. (Optional) Add a lifecycle rule on each bucket: transition objects to
   S3 Intelligent-Tiering after 30 days.

**Step 2 — Create an EMR 6.15 cluster**

1. Open [EMR console](https://console.aws.amazon.com/emr/) → **Create cluster**.
2. Choose **EMR release**: `emr-6.15.0` (ships with Spark 3.5).
3. Applications: **Spark**.
4. Instance groups:
   - Primary: `m5.xlarge` (1 node)
   - Core: `m5.xlarge` (2 nodes)  ← scale up for production
5. Under **Software settings → Enter configuration**, paste:
   ```json
   [
     {
       "Classification": "spark-defaults",
       "Properties": {
         "spark.sql.extensions": "io.delta.sql.DeltaSparkSessionExtension",
         "spark.sql.catalog.spark_catalog": "org.apache.spark.sql.delta.catalog.DeltaCatalog"
       }
     }
   ]
   ```
6. **Bootstrap actions** → Add bootstrap action → Custom action:
   ```
   Script location: s3://your-scripts-bucket/bootstrap.sh
   ```
   Where `bootstrap.sh` contains:
   ```bash
   #!/bin/bash
   sudo pip3 install delta-spark==3.2.0
   ```
7. EC2 key pair: choose your key pair (needed for SSH).
8. Click **Create cluster**.

**Step 3 — Upload the script**

```
S3 console → fossil-scripts/ → Upload → spark_job.py
```

**Step 4 — Add an EMR Step**

1. EMR console → your cluster → **Steps** tab → **Add step**.
2. Step type: **Spark application**.
3. Name: `GoldLayerUpsert`.
4. Application location: `s3://fossil-scripts/spark_job.py`
5. Spark submit options:
   ```
   --packages io.delta:delta-spark_2.12:3.2.0
   ```
6. Arguments (pass env vars as Spark conf):
   ```
   --conf spark.executorEnv.SILVER_PATH=s3://fossil-silver-layer/chunks/
   --conf spark.executorEnv.GOLD_PATH=s3://fossil-gold-layer/chunks/
   --conf spark.executorEnv.WRITE_FORMAT=delta
   ```
7. Action on failure: **Continue** (safe to re-run).
8. Click **Add**.

---

### Option B — AWS Glue (Managed, no cluster management)

1. Open [Glue console](https://console.aws.amazon.com/glue/) → **ETL jobs** → **Create job**.
2. Choose **Spark script editor** → paste the contents of `spark_job.py`.
3. Set `WRITE_FORMAT=parquet` (Glue manages Delta via LakeFormation separately).
4. Worker type: **G.1X**, Number of workers: **2**.
5. Under **Job parameters**, add:
   | Key | Value |
   |-----|-------|
   | `--SILVER_PATH` | `s3://fossil-silver-layer/chunks/` |
   | `--GOLD_PATH` | `s3://fossil-gold-layer/chunks/` |
   | `--WRITE_FORMAT` | `parquet` |
6. **Schedule**: Triggers → Add trigger → Schedule (e.g., daily 02:00 UTC).

> Note: In the Glue script, read job parameters with:
> ```python
> import sys
> from awsglue.utils import getResolvedOptions
> args = getResolvedOptions(sys.argv, ["SILVER_PATH", "GOLD_PATH", "WRITE_FORMAT"])
> ```

---

### Option C — Local Testing with LocalStack

```bash
# Start LocalStack
docker run -p 4566:4566 localstack/localstack

# Create mock S3 buckets
export AWS_DEFAULT_REGION=us-east-1
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
awslocal s3 mb s3://fossil-silver-layer
awslocal s3 mb s3://fossil-gold-layer

# Run with parquet (no Delta needed locally)
SILVER_PATH=s3://fossil-silver-layer/chunks/ \
GOLD_PATH=s3://fossil-gold-layer/chunks/ \
WRITE_FORMAT=parquet \
python spark_job.py
```
