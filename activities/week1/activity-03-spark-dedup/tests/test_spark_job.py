"""
Tests for the idempotent Spark gold layer writer.

All tests use local[1] Spark with the parquet write_format so that
delta-spark is not required as a test dependency.

The delta routing test mocks _upsert_delta to verify the dispatch
logic without requiring a Delta-capable cluster.

Run with:  pytest tests/ -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spark_job import _upsert_parquet, run


# ---------------------------------------------------------------------------
# Session-scoped SparkSession — one per pytest run (startup is slow)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("test-spark-dedup")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_silver(spark, rows, path):
    (spark
     .createDataFrame(rows, ["doc_id", "chunk_id", "text"])
     .write.mode("overwrite").parquet(path))


def _read_gold(spark, path):
    return spark.read.parquet(path).collect()


# ---------------------------------------------------------------------------
# _upsert_parquet unit tests
# ---------------------------------------------------------------------------

class TestParquetUpsert:

    def test_initialises_gold_when_missing(self, spark, tmp_path):
        """First run creates the gold layer from scratch."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "hello"), ("d1", "c2", "world")], silver)

        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        assert len(_read_gold(spark, gold)) == 2

    def test_idempotency(self, spark, tmp_path):
        """Running the same data twice produces the same row count as running it once."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "hello"), ("d1", "c2", "world")], silver)

        _upsert_parquet(spark, spark.read.parquet(silver), gold)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        assert len(_read_gold(spark, gold)) == 2

    def test_updates_existing_record(self, spark, tmp_path):
        """Re-submitting a (doc_id, chunk_id) updates the row, not duplicates it."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "old text")], silver)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        _write_silver(spark, [("d1", "c1", "new text")], silver)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        rows = _read_gold(spark, gold)
        assert len(rows) == 1
        assert rows[0].text == "new text"

    def test_adds_new_records_without_touching_existing(self, spark, tmp_path):
        """New (doc_id, chunk_id) pairs are appended; existing rows are preserved."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "chunk one")], silver)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        _write_silver(spark, [("d1", "c2", "chunk two")], silver)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        rows = spark.read.parquet(gold)
        assert rows.count() == 2
        assert {r.chunk_id for r in rows.collect()} == {"c1", "c2"}

    def test_empty_incoming_does_not_change_gold(self, spark, tmp_path):
        """An empty silver batch leaves the gold layer completely unchanged."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "original")], silver)
        _upsert_parquet(spark, spark.read.parquet(silver), gold)

        schema = spark.read.parquet(silver).schema
        empty = spark.createDataFrame([], schema)
        _upsert_parquet(spark, empty, gold)

        rows = _read_gold(spark, gold)
        assert len(rows) == 1
        assert rows[0].text == "original"


# ---------------------------------------------------------------------------
# run() integration tests
# ---------------------------------------------------------------------------

class TestRun:

    def test_raises_on_missing_required_columns(self, spark, tmp_path):
        """run() must fail fast when chunk_id or doc_id is absent."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        spark.createDataFrame(
            [("d1", "hello")], ["doc_id", "text"]
        ).write.parquet(silver)

        with pytest.raises(ValueError, match="chunk_id"):
            run(spark, silver_path=silver, gold_path=gold, write_format="parquet")

    def test_deduplicates_duplicate_keys_in_incoming_batch(self, spark, tmp_path):
        """Duplicate (doc_id, chunk_id) rows in silver are collapsed to one."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "v1"), ("d1", "c1", "v2")], silver)

        run(spark, silver_path=silver, gold_path=gold, write_format="parquet")

        assert spark.read.parquet(gold).count() == 1

    def test_idempotency_end_to_end(self, spark, tmp_path):
        """Full end-to-end: three re-runs produce the same record count as one."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        rows = [("d1", "c1", "a"), ("d1", "c2", "b"), ("d2", "c1", "c")]
        _write_silver(spark, rows, silver)

        for _ in range(3):
            run(spark, silver_path=silver, gold_path=gold, write_format="parquet")

        assert spark.read.parquet(gold).count() == 3

    def test_routes_to_upsert_delta_when_format_is_delta(self, spark, tmp_path):
        """write_format='delta' dispatches to _upsert_delta, not _upsert_parquet."""
        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "text")], silver)

        with patch("spark_job._upsert_delta") as mock_delta:
            run(spark, silver_path=silver, gold_path=gold, write_format="delta")
            mock_delta.assert_called_once()

    def test_missing_silver_layer_logs_warning_without_raising(self, spark, tmp_path):
        """If silver_path doesn't exist, run() logs a warning and returns cleanly."""
        gold = str(tmp_path / "gold")

        # Should not raise — graceful no-op
        run(spark, silver_path=str(tmp_path / "nonexistent"), gold_path=gold, write_format="parquet")


# ---------------------------------------------------------------------------
# Regression: documents the broken behaviour
# ---------------------------------------------------------------------------

class TestBrokenBehaviour:

    def test_broken_append_creates_duplicates(self, spark, tmp_path):
        """Documents the bug: broken append mode doubles rows on re-run."""
        import importlib.util
        broken_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "broken", "spark_job.py",
        )
        spec = importlib.util.spec_from_file_location("broken_spark_job", broken_path)
        broken = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(broken)

        silver = str(tmp_path / "silver")
        gold = str(tmp_path / "gold")
        _write_silver(spark, [("d1", "c1", "text")], silver)

        broken.run(spark, silver_path=silver, gold_path=gold)
        broken.run(spark, silver_path=silver, gold_path=gold)

        # Bug confirmed: 2 runs → 2 copies of the same row
        assert spark.read.parquet(gold).count() == 2
