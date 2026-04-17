"""
FIXED: Idempotent Spark gold layer writer.

What changed and why:
  1. Delta Lake MERGE INTO — atomic upsert on (doc_id, chunk_id).
     Same row re-submitted → updated in-place, never duplicated.
  2. Parquet fallback — read-dedup-overwrite using a left_anti join.
     Incoming rows replace existing rows with the same composite key.
  3. Incoming deduplication — dropDuplicates before any write.
     Delta MERGE fails if the source DataFrame has duplicate keys.
  4. Schema validation — fast-fail if required columns are missing.
  5. All config from environment — no hardcoded paths or formats.
"""

import logging
import os
from typing import Optional

from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_REQUIRED_COLS = {"doc_id", "chunk_id"}


# ---------------------------------------------------------------------------
# Spark session factory
# ---------------------------------------------------------------------------

def build_spark(app_name: str = "GoldLayerJob", use_delta: bool = False) -> SparkSession:
    builder = SparkSession.builder.appName(app_name)
    if use_delta:
        builder = (
            builder
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config(
                "spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog",
            )
        )
    return builder.getOrCreate()


# ---------------------------------------------------------------------------
# Write strategies
# ---------------------------------------------------------------------------

def _upsert_delta(spark: SparkSession, incoming, gold_path: str) -> None:
    """Atomic MERGE INTO on (doc_id, chunk_id) — requires delta-spark."""
    from delta.tables import DeltaTable

    if DeltaTable.isDeltaTable(spark, gold_path):
        gold = DeltaTable.forPath(spark, gold_path)
        (
            gold.alias("g")
            .merge(
                incoming.alias("i"),
                "g.doc_id = i.doc_id AND g.chunk_id = i.chunk_id",
            )
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
        log.info("Delta MERGE INTO complete at %s", gold_path)
    else:
        incoming.write.format("delta").mode("overwrite").save(gold_path)
        log.info("Delta table initialised at %s", gold_path)


def _upsert_parquet(spark: SparkSession, incoming, gold_path: str) -> None:
    """Idempotent upsert for plain Parquet: remove replaced keys then union.

    Incoming always wins on conflict — mirrors Delta MERGE whenMatchedUpdateAll.
    left_anti join removes from existing any rows whose (doc_id, chunk_id)
    appear in incoming; then we union the surviving rows with incoming.
    """
    try:
        existing = spark.read.parquet(gold_path)
        keys = incoming.select("doc_id", "chunk_id")
        unchanged = existing.join(keys, ["doc_id", "chunk_id"], "left_anti")
        merged = unchanged.unionByName(incoming)
        merged.write.mode("overwrite").parquet(gold_path)
        log.info("Parquet upsert complete at %s", gold_path)
    except AnalysisException:
        # Gold layer doesn't exist yet — first run initialises it.
        incoming.write.mode("overwrite").parquet(gold_path)
        log.info("Parquet gold layer initialised at %s", gold_path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    spark: Optional[SparkSession] = None,
    silver_path: str = "",
    gold_path: str = "",
    write_format: str = "",
) -> None:
    """Read silver layer, deduplicate, and upsert into the gold layer.

    Args:
        spark:        existing SparkSession (created if None)
        silver_path:  overrides SILVER_PATH env var
        gold_path:    overrides GOLD_PATH env var
        write_format: "delta" (default) or "parquet"
    """
    silver_path = silver_path or os.environ["SILVER_PATH"]
    gold_path = gold_path or os.environ["GOLD_PATH"]
    write_format = write_format or os.getenv("WRITE_FORMAT", "delta")
    use_delta = write_format == "delta"

    if spark is None:
        spark = build_spark(use_delta=use_delta)

    try:
        incoming = spark.read.parquet(silver_path)
    except AnalysisException:
        log.warning("Silver layer empty or missing at %s — nothing to process", silver_path)
        return

    missing = _REQUIRED_COLS - set(incoming.columns)
    if missing:
        raise ValueError(f"Silver layer missing required columns: {missing}")

    # dropDuplicates before the merge: Delta MERGE INTO fails if incoming
    # contains two rows with the same (doc_id, chunk_id).
    # .count() is logged for observability; remove in hot paths if cost matters.
    incoming = incoming.dropDuplicates(["doc_id", "chunk_id"])
    log.info("Processing %d unique records from silver layer", incoming.count())

    if use_delta:
        _upsert_delta(spark, incoming, gold_path)
    else:
        _upsert_parquet(spark, incoming, gold_path)


if __name__ == "__main__":
    use_delta = os.getenv("WRITE_FORMAT", "delta") == "delta"
    spark = build_spark(use_delta=use_delta)
    try:
        run(spark)
    finally:
        spark.stop()
