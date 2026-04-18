"""
BROKEN: Spark gold layer writer.

Bug: mode("append") creates a new copy of every silver row on each re-run.
  Run 1 → 100 rows in gold
  Run 2 → 200 rows in gold  (same 100 duplicated)
  Run N → N × 100 rows in gold

Root cause: no deduplication — the job treats every execution as additive,
not idempotent. Re-running after a failure or for a backfill multiplies data.
"""

import os

from pyspark.sql import SparkSession


def run(spark=None, silver_path="", gold_path=""):
    silver_path = silver_path or os.environ["SILVER_PATH"]
    gold_path = gold_path or os.environ["GOLD_PATH"]

    if spark is None:
        spark = SparkSession.builder.appName("GoldLayerJob-Broken").getOrCreate()

    df = spark.read.parquet(silver_path)

    # BUG: append mode creates a new copy of every row on each re-run.
    # There is no logic to check whether (doc_id, chunk_id) already exists.
    df.write.mode("append").parquet(gold_path)


if __name__ == "__main__":
    spark = SparkSession.builder.appName("GoldLayerJob-Broken").getOrCreate()
    try:
        run(spark)
    finally:
        spark.stop()
