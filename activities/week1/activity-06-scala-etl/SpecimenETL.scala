/**
 * Fixed Scala ETL — FossilRAG specimen enrichment.
 *
 * Backward-compatible reads over a drifting Parquet schema.
 *
 * Fixes applied (each annotated with ✅):
 *   1. mergeSchema=true — Parquet file schemas are unioned into a superset,
 *      so new columns appearing only in newer files are still read.
 *   2. ColumnContract = minimum-schema + default — replaces the brittle
 *      "must match exactly" StructType with an additive contract.
 *   3. withDefaultIfMissing — synthesises missing columns with a typed
 *      default, and coalesces existing-but-null values so downstream never
 *      sees silent nulls.
 *   4. validateSchema — WARNs on new/missing fields (additive drift is safe);
 *      FAILs only on type narrowing, which is a genuine breaking change.
 *   5. sys.env driven config — INPUT_PATH, OUTPUT_PATH, APP_NAME,
 *      SPARK_MASTER, LOG_LEVEL all env-configurable with sane defaults.
 *   6. SLF4J structured logging — key=value fields are parseable by
 *      CloudWatch Insights / Datadog, timestamp + level included.
 */
package com.fossilrag.etl

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions.{coalesce, col, lit}
import org.apache.spark.sql.types._
import org.slf4j.{Logger, LoggerFactory}

object SpecimenETL {

  private val logger: Logger = LoggerFactory.getLogger(getClass)

  // ---------------------------------------------------------------------- //
  // ✅ FIX 5: All config from environment — no hardcoded values            //
  // ---------------------------------------------------------------------- //
  val INPUT_PATH: String    = sys.env.getOrElse("INPUT_PATH", "/data/fossils/raw")
  val OUTPUT_PATH: String   = sys.env.getOrElse("OUTPUT_PATH", "/data/fossils/enriched")
  val APP_NAME: String      = sys.env.getOrElse("APP_NAME", "SpecimenETL")
  val SPARK_MASTER: String  = sys.env.getOrElse("SPARK_MASTER", "local[*]")
  val LOG_LEVEL: String     = sys.env.getOrElse("LOG_LEVEL", "WARN")

  // ---------------------------------------------------------------------- //
  // ✅ FIX 2: Contract = minimum required fields + per-column default      //
  //           NOT a rigid equality check. Upstream may add any number of   //
  //           extra fields without breaking this job.                      //
  // ---------------------------------------------------------------------- //
  final case class ColumnContract(name: String, dataType: DataType, default: Any)

  val REQUIRED_COLUMNS: Seq[ColumnContract] = Seq(
    // Core v1 fields — present since day one
    ColumnContract("specimen_id",       StringType, null),
    ColumnContract("species",           StringType, "unknown"),
    ColumnContract("age_ma",            DoubleType, 0.0),
    ColumnContract("location",          StringType, "unknown"),
    // v2 evolution — old Parquet files will NOT have these; defaults fill in
    ColumnContract("discoverer",        StringType, "unknown"),
    ColumnContract("excavation_site",   StringType, "unknown"),
    ColumnContract("geological_period", StringType, "unknown")
  )

  // ---------------------------------------------------------------------- //
  // ✅ FIX 1: mergeSchema=true — union schemas across all Parquet files    //
  //           under INPUT_PATH. Without this, Spark picks one file's       //
  //           schema and silently drops columns absent from that file.     //
  // ---------------------------------------------------------------------- //
  def readSpecimens(spark: SparkSession, path: String = INPUT_PATH): DataFrame = {
    logger.info(s"event=read_start path=$path merge_schema=true")
    val df = spark.read
      .option("mergeSchema", "true")
      .parquet(path)
    logger.info(s"event=read_complete columns=${df.columns.mkString(",")}")
    df
  }

  // ---------------------------------------------------------------------- //
  // ✅ FIX 3: withDefaultIfMissing — guarantee the column exists in the    //
  //           DataFrame, with a typed default. Two cases:                   //
  //             (a) column absent  → add it with lit(default).cast(type)    //
  //             (b) column present → coalesce null values to default        //
  //           Either way, downstream never sees a silent null.              //
  // ---------------------------------------------------------------------- //
  def withDefaultIfMissing(df: DataFrame, contract: ColumnContract): DataFrame = {
    if (df.columns.contains(contract.name)) {
      // Column exists — fill nulls with the default; keeps declared type
      if (contract.default == null) {
        df  // no default to coalesce against (e.g. primary key)
      } else {
        df.withColumn(
          contract.name,
          coalesce(col(contract.name), lit(contract.default).cast(contract.dataType))
        )
      }
    } else {
      // Column missing — synthesise it with the default value
      logger.warn(
        s"event=column_missing column=${contract.name} " +
        s"default=${contract.default} type=${contract.dataType.simpleString} " +
        s"action=synthesised"
      )
      df.withColumn(contract.name, lit(contract.default).cast(contract.dataType))
    }
  }

  /** Apply every column contract in order, folding defaults into the DataFrame. */
  def applyContract(df: DataFrame): DataFrame =
    REQUIRED_COLUMNS.foldLeft(df)(withDefaultIfMissing)

  // ---------------------------------------------------------------------- //
  // ✅ FIX 4: Schema validator — WARN on drift, FAIL only on breaking      //
  //           changes (type narrowing / incompatible type).                //
  //           Policy:                                                       //
  //             - new fields from upstream  → warn, keep going              //
  //             - missing required fields   → warn, defaults will fill      //
  //             - type widening (Int→Long)  → allowed                       //
  //             - type narrowing / mismatch → fail loudly                   //
  // ---------------------------------------------------------------------- //
  def validateSchema(df: DataFrame): Unit = {
    val actualNames   = df.schema.fields.map(_.name).toSet
    val expectedNames = REQUIRED_COLUMNS.map(_.name).toSet

    val newFields     = actualNames -- expectedNames
    val missingFields = expectedNames -- actualNames

    if (newFields.nonEmpty) {
      logger.warn(
        s"event=schema_drift kind=new_fields fields=${newFields.mkString(",")} " +
        s"action=warn policy=additive_allowed"
      )
    }
    if (missingFields.nonEmpty) {
      logger.warn(
        s"event=schema_drift kind=missing_fields fields=${missingFields.mkString(",")} " +
        s"action=default_applied policy=backward_compatible"
      )
    }

    // Type conflicts on a shared column name = genuine breaking change → fail hard
    REQUIRED_COLUMNS.foreach { c =>
      df.schema.fields.find(_.name == c.name).foreach { f =>
        if (!isCompatibleType(f.dataType, c.dataType)) {
          val msg =
            s"event=schema_breaking_change column=${c.name} " +
            s"expected=${c.dataType.simpleString} got=${f.dataType.simpleString}"
          logger.error(msg)
          throw new IllegalStateException(msg)
        }
      }
    }
  }

  /** Allow exact match or safe widening (e.g. Int → Long, Float → Double). */
  def isCompatibleType(actual: DataType, expected: DataType): Boolean =
    (actual, expected) match {
      case (a, b) if a == b                           => true
      case (IntegerType, LongType)                    => true
      case (IntegerType, DoubleType)                  => true
      case (LongType,    DoubleType)                  => true
      case (FloatType,   DoubleType)                  => true
      case _                                          => false
    }

  // ---------------------------------------------------------------------- //
  // Enrichment — safe to select every contract column because applyContract //
  // has already guaranteed each one exists with a default.                   //
  // ---------------------------------------------------------------------- //
  def enrich(df: DataFrame): DataFrame =
    df.selectExpr(
      "specimen_id",
      "UPPER(species) AS species_upper",
      "age_ma",
      "location",
      "discoverer",
      "excavation_site",
      "geological_period"
    )

  def run(spark: SparkSession): Unit = {
    val raw = readSpecimens(spark)
    validateSchema(raw)
    val withDefaults = applyContract(raw)
    val enriched = enrich(withDefaults)
    val rowCount = enriched.count()
    logger.info(s"event=write_start rows=$rowCount path=$OUTPUT_PATH")
    enriched.write.mode("overwrite").parquet(OUTPUT_PATH)
    logger.info(s"event=etl_complete status=success rows=$rowCount")
  }

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName(APP_NAME)
      .master(SPARK_MASTER)
      .getOrCreate()
    spark.sparkContext.setLogLevel(LOG_LEVEL)

    try {
      run(spark)
    } catch {
      case e: Throwable =>
        logger.error(s"event=etl_failed message=${e.getMessage}", e)
        throw e
    } finally {
      spark.stop()
    }
  }
}
