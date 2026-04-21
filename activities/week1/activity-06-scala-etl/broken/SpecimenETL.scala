/**
 * Broken Scala ETL -- FossilRAG specimen enrichment.
 *
 * This job reads raw fossil specimen Parquet, normalises species names,
 * and writes an enriched dataset for downstream excavation queries.
 *
 * Anti-patterns present (each annotated with BUG):
 *   1. No mergeSchema -- Spark picks one file's schema; columns that exist
 *      only in newer files are silently dropped across the read.
 *   2. Frozen StructType contract -- upstream adds a column and the read
 *      blows up with AnalysisException (or drops it, depending on order).
 *   3. No default values -- columns missing from old files surface as null
 *      all the way to downstream consumers, causing NPEs or bad aggregates.
 *   4. No schema validation -- drift is invisible until a downstream job
 *      crashes in production; no warning, no audit trail.
 *   5. Hardcoded paths + Spark config -- one codebase cannot run across
 *      dev / staging / prod without source edits.
 *   6. println-based logging -- no level, no timestamp, no structured
 *      fields, impossible to query in CloudWatch / Datadog.
 */
package com.fossilrag.etl

import org.apache.spark.sql.{SparkSession, DataFrame}
import org.apache.spark.sql.types._

object SpecimenETL {

  // BUG 2: frozen contract -- this StructType is hardcoded. When upstream
  // adds `discoverer` or `geological_period`, either the read drops them
  // silently (if only .parquet() is used with a partial file), or the
  // enforced-schema read throws AnalysisException on extra/renamed fields.
  val EXPECTED_SCHEMA: StructType = StructType(Array(
    StructField("specimen_id", StringType, nullable = false),
    StructField("species",     StringType, nullable = false),
    StructField("age_ma",      DoubleType, nullable = false),
    StructField("location",    StringType, nullable = true)
  ))

  // BUG 5: paths hardcoded -- cannot point at s3://fossils-staging/... in staging
  val INPUT_PATH  = "/data/fossils/raw"
  val OUTPUT_PATH = "/data/fossils/enriched"

  def readSpecimens(spark: SparkSession): DataFrame = {
    // BUG 1: no .option("mergeSchema", "true") -- Spark picks the schema from
    //         a single "random" file (or the summary file). If the first file
    //         is old-format, newer columns are NEVER surfaced.
    // BUG 4: .schema(EXPECTED_SCHEMA) enforces a rigid contract. Any drift
    //         fails loudly at read time rather than being handled gracefully.
    spark.read
      .schema(EXPECTED_SCHEMA)
      .parquet(INPUT_PATH)
  }

  def enrich(df: DataFrame): DataFrame = {
    // BUG 3: assumes all columns are non-null. If `species` is null (because
    //         the column was missing in old Parquet), UPPER(null) = null and
    //         the corrupt value propagates downstream with no guardrail.
    df.selectExpr(
      "specimen_id",
      "UPPER(species) AS species_upper",
      "age_ma",
      "location"
    )
  }

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("SpecimenETL")  // BUG 5: app name hardcoded
      .master("local[*]")      // BUG 5: master hardcoded -- cannot target YARN/k8s
      .getOrCreate()

    val raw = readSpecimens(spark)

    // BUG 6: println -- no log level, no structured fields, not parseable
    println(s"Read ${raw.count()} rows")

    val enriched = enrich(raw)
    enriched.write.mode("overwrite").parquet(OUTPUT_PATH)

    // BUG 6: println again -- and no error handling if the write fails
    println("ETL complete")
    spark.stop()
  }
}
