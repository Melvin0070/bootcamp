"""
pytest suite for Activity 6 — Scala ETL schema-evolution fix.

The source is Scala, so tests use regex / string matching on the source files
rather than AST parsing. This lets the suite run with plain `pytest` — no
sbt / JVM toolchain required — while still proving the broken version has
the documented anti-patterns and the fixed version has every fix in place.

Two targets:
  - broken/SpecimenETL.scala  → must exhibit the known anti-patterns
  - SpecimenETL.scala         → must have every fix
"""

import os
import re

import pytest  # noqa: F401 — imported for pytest discovery

BASE = os.path.join(os.path.dirname(__file__), "..")
BROKEN_PATH      = os.path.join(BASE, "broken", "SpecimenETL.scala")
FIXED_PATH       = os.path.join(BASE, "SpecimenETL.scala")
ENV_EXAMPLE_PATH = os.path.join(BASE, ".env.example")
DOCS_PATH        = os.path.join(BASE, "docs", "architecture.md")
README_PATH      = os.path.join(BASE, "README.md")
BUILD_SBT_PATH   = os.path.join(BASE, "build.sbt")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(src: str) -> str:
    """Remove block comments and // line comments so matches aren't spoofed by prose."""
    # Remove /* ... */ (non-greedy, across lines)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    # Remove // line comments
    src = re.sub(r"//[^\n]*", "", src)
    return src


# --------------------------------------------------------------------------- #
# 1. Broken version — must have the documented anti-patterns                   #
# --------------------------------------------------------------------------- #


class TestBrokenAntiPatterns:
    """Verify the broken Scala ETL still has every bug the activity exists to fix."""

    def test_no_merge_schema_option(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'mergeSchema' not in src, "broken ETL must not enable mergeSchema"

    def test_uses_frozen_structtype_contract(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'EXPECTED_SCHEMA' in src, "broken ETL should hardcode a frozen StructType"
        assert '.schema(' in src, "broken ETL should enforce that frozen schema on read"

    def test_no_default_value_helpers(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'withDefaultIfMissing' not in src
        assert 'coalesce(' not in src
        # `lit(` not used to synthesise missing columns
        assert 'lit(' not in src

    def test_no_schema_validator(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'validateSchema' not in src

    def test_hardcoded_paths(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert '"/data/fossils/raw"' in src or '/data/fossils/raw' in src
        assert 'sys.env' not in src, "broken ETL must not use env vars"

    def test_hardcoded_spark_master(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'local[*]' in src
        assert '.master("local[*]")' in src

    def test_uses_println_not_slf4j(self):
        src = _strip_comments(_src(BROKEN_PATH))
        assert 'println(' in src, "broken ETL should log via println"
        assert 'slf4j' not in src.lower()
        assert 'LoggerFactory' not in src


# --------------------------------------------------------------------------- #
# 2. Fixed version — mergeSchema enabled                                       #
# --------------------------------------------------------------------------- #


class TestFixedMergeSchema:

    def test_merge_schema_option_present(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert '.option("mergeSchema", "true")' in src, \
            "fixed ETL must read Parquet with mergeSchema=true"

    def test_merge_schema_used_before_parquet_call(self):
        """The .option(...) must chain into .parquet(...) — not be orphaned."""
        src = _strip_comments(_src(FIXED_PATH))
        assert re.search(
            r'\.option\("mergeSchema",\s*"true"\)\s*\n?\s*\.parquet\(',
            src,
        ), "mergeSchema option must chain into a .parquet() read"


# --------------------------------------------------------------------------- #
# 3. Fixed version — default-value handling                                    #
# --------------------------------------------------------------------------- #


class TestFixedDefaultValues:

    def test_with_default_if_missing_defined(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'def withDefaultIfMissing' in src

    def test_column_contract_case_class(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'case class ColumnContract' in src

    def test_column_contract_has_default_field(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'default' in src
        assert 'dataType' in src

    def test_coalesce_used_for_existing_column_nulls(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'coalesce(' in src, "fixed ETL must coalesce nulls in existing columns"

    def test_lit_cast_for_missing_column(self):
        src = _strip_comments(_src(FIXED_PATH))
        # lit(default).cast(type) pattern for synthesising missing columns
        assert re.search(r'lit\([^)]+\)\.cast\(', src), \
            "fixed ETL must synthesise missing columns with lit(default).cast(type)"

    def test_required_columns_declared(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'REQUIRED_COLUMNS' in src
        # At least the original v1 columns and the v2 evolution columns
        for col in ("specimen_id", "species", "age_ma", "location",
                    "discoverer", "excavation_site", "geological_period"):
            assert f'"{col}"' in src, f"REQUIRED_COLUMNS must include {col}"

    def test_apply_contract_folds_over_required_columns(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'def applyContract' in src
        assert 'foldLeft' in src, "applyContract should fold the contract over the DataFrame"


# --------------------------------------------------------------------------- #
# 4. Fixed version — schema validation warns, doesn't fail                     #
# --------------------------------------------------------------------------- #


class TestFixedSchemaValidation:

    def test_validate_schema_defined(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'def validateSchema' in src

    def test_validator_warns_on_new_fields(self):
        src = _strip_comments(_src(FIXED_PATH))
        # Must use logger.warn, not throw, for additive drift
        assert re.search(r'logger\.warn\([^)]*new_fields', src), \
            "new fields should trigger logger.warn, not an exception"

    def test_validator_warns_on_missing_fields(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert re.search(r'logger\.warn\([^)]*missing_fields', src), \
            "missing fields should trigger logger.warn (defaults applied)"

    def test_validator_throws_only_on_breaking_type_change(self):
        src = _strip_comments(_src(FIXED_PATH))
        # `throw new IllegalStateException` should exist exactly for the type-mismatch branch
        assert 'IllegalStateException' in src
        assert 'schema_breaking_change' in src

    def test_type_compatibility_helper_allows_widening(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'isCompatibleType' in src
        # Int→Long and Float→Double widening must be permitted
        assert re.search(r'IntegerType.*LongType', src)
        assert re.search(r'FloatType.*DoubleType', src)


# --------------------------------------------------------------------------- #
# 5. Fixed version — env-var driven config (no hardcoded values)               #
# --------------------------------------------------------------------------- #


class TestFixedEnvVars:

    def test_input_path_from_env(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'sys.env.getOrElse("INPUT_PATH"' in src

    def test_output_path_from_env(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'sys.env.getOrElse("OUTPUT_PATH"' in src

    def test_spark_master_from_env(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'sys.env.getOrElse("SPARK_MASTER"' in src

    def test_app_name_from_env(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'sys.env.getOrElse("APP_NAME"' in src

    def test_log_level_from_env(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'sys.env.getOrElse("LOG_LEVEL"' in src

    def test_env_example_exists(self):
        assert os.path.exists(ENV_EXAMPLE_PATH), ".env.example must be committed"

    def test_env_example_documents_every_var(self):
        content = _src(ENV_EXAMPLE_PATH)
        for var in ("INPUT_PATH", "OUTPUT_PATH", "APP_NAME", "SPARK_MASTER", "LOG_LEVEL"):
            assert var in content, f".env.example missing {var}"


# --------------------------------------------------------------------------- #
# 6. Fixed version — SLF4J structured logging                                  #
# --------------------------------------------------------------------------- #


class TestFixedLogging:

    def test_slf4j_imported(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'import org.slf4j.' in src
        assert 'LoggerFactory' in src

    def test_no_println(self):
        src = _strip_comments(_src(FIXED_PATH))
        assert 'println(' not in src, "fixed ETL must not use println"

    def test_logger_emits_key_value_fields(self):
        src = _strip_comments(_src(FIXED_PATH))
        # Every info/warn/error call carries an event=... field for query parsing
        assert re.search(r'logger\.(info|warn|error)\([^)]*event=', src)

    def test_structured_fields_include_path(self):
        src = _strip_comments(_src(FIXED_PATH))
        # At least one log record should carry path=... for audit trails
        assert re.search(r'path=\$', src) or 'path=${' in src


# --------------------------------------------------------------------------- #
# 7. Documentation & build deliverables                                        #
# --------------------------------------------------------------------------- #


class TestDeliverables:

    def test_architecture_doc_exists(self):
        assert os.path.exists(DOCS_PATH), "docs/architecture.md must exist"

    def test_architecture_doc_has_required_sections(self):
        doc = _src(DOCS_PATH)
        for heading in (
            "Problem Diagnosis",
            "Schema Evolution Policy",
            "Trade-off Table",
            "Edge Cases Handled",
        ):
            assert heading in doc, f"architecture.md missing section: {heading}"

    def test_architecture_doc_has_before_after_diagram(self):
        doc = _src(DOCS_PATH)
        assert "Before" in doc and "After" in doc

    def test_readme_updated_with_fix_table(self):
        readme = _src(README_PATH)
        assert "What Was Fixed" in readme, "README must have a What Was Fixed table"
        assert "mergeSchema" in readme
        assert "default" in readme.lower()

    def test_build_sbt_exists(self):
        assert os.path.exists(BUILD_SBT_PATH), "build.sbt should be committed for reproducibility"

    def test_build_sbt_declares_spark_sql_dep(self):
        sbt = _src(BUILD_SBT_PATH)
        assert "spark-sql" in sbt
        assert "slf4j" in sbt.lower()
