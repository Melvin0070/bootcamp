"""
Behaviour tests for the fixed pipeline.

Covers the pure transform, the I/O wrappers (against the local
filesystem), and the upload step (against a moto-stubbed S3). No real
AWS calls. Same shape as Activities 7 & 8.
"""

from __future__ import annotations

import sys
from pathlib import Path

import boto3
import pandas as pd
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# normalise() — pure transform
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_renames_columns(self):
        df = pd.DataFrame({"Species": ["Trex"], "Age_MYA": [66.0]})
        out = pipeline.normalise(df)
        assert list(out.columns) == ["species", "age_mya"]

    def test_lowercases_and_strips_species(self):
        df = pd.DataFrame({"Species": ["  T-Rex  "], "Age_MYA": [66.0]})
        out = pipeline.normalise(df)
        assert out.loc[0, "species"] == "t-rex"

    def test_drops_rows_missing_required_columns(self):
        df = pd.DataFrame(
            {
                "Species": ["trex", None, "raptor"],
                "Age_MYA": [66.0, 70.0, None],
            }
        )
        out = pipeline.normalise(df)
        # Only 'trex' has both fields populated.
        assert len(out) == 1
        assert out.loc[0, "species"] == "trex"

    def test_drops_empty_string_species(self):
        df = pd.DataFrame({"Species": ["", "trex"], "Age_MYA": [66.0, 66.0]})
        out = pipeline.normalise(df)
        assert len(out) == 1

    def test_raises_when_required_column_missing(self):
        df = pd.DataFrame({"Species": ["trex"]})  # no age column
        with pytest.raises(ValueError, match="missing required columns"):
            pipeline.normalise(df)

    def test_resets_index(self):
        df = pd.DataFrame(
            {
                "Species": [None, "trex", "raptor"],
                "Age_MYA": [66.0, 66.0, 70.0],
            }
        )
        out = pipeline.normalise(df)
        # After dropna the original index has a gap; the reset means
        # the consumer never sees row 0 missing.
        assert list(out.index) == [0, 1]

    def test_does_not_mutate_input(self):
        df = pd.DataFrame({"Species": ["  Trex  "], "Age_MYA": [66.0]})
        before = df.copy()
        pipeline.normalise(df)
        pd.testing.assert_frame_equal(df, before)


# ---------------------------------------------------------------------------
# read_csv() / write_parquet() — local filesystem
# ---------------------------------------------------------------------------


class TestLocalIO:
    def test_read_csv_returns_dataframe(self, tmp_path):
        p = tmp_path / "in.csv"
        p.write_text("Species,Age_MYA\nTrex,66\n")
        df = pipeline.read_csv(p)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Species", "Age_MYA"]

    def test_write_parquet_roundtrip(self, tmp_path):
        df = pd.DataFrame({"species": ["trex"], "age_mya": [66.0]})
        out = pipeline.write_parquet(df, tmp_path / "nested" / "out.parquet")
        assert out.exists()
        roundtrip = pd.read_parquet(out)
        # check_dtype=False — the parquet engine roundtrip can promote
        # object↔StringDtype depending on which engine is installed
        # (pyarrow on CI, fastparquet locally). We care about values,
        # not the storage discriminant.
        pd.testing.assert_frame_equal(df, roundtrip, check_dtype=False)


# ---------------------------------------------------------------------------
# upload() / run() — moto-stubbed S3
# ---------------------------------------------------------------------------


@mock_aws
class TestS3Upload:
    def test_upload_puts_object(self, tmp_path):
        bucket = "fossilrag-test"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=bucket)

        local = tmp_path / "x.parquet"
        local.write_bytes(b"PARQUET-BYTES")
        pipeline.upload(local, bucket, "out/x.parquet", client=s3)

        body = s3.get_object(Bucket=bucket, Key="out/x.parquet")["Body"].read()
        assert body == b"PARQUET-BYTES"

    def test_run_end_to_end(self, tmp_path):
        bucket = "fossilrag-test"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=bucket)

        csv = tmp_path / "in.csv"
        csv.write_text("Species,Age_MYA\nTrex,66\nRaptor,70\n,80\n")

        # Use a key under /tmp because the function writes there. The
        # final S3 key is what we assert against.
        pipeline.run(str(csv), bucket, "data/normalised.parquet", s3_client=s3)

        head = s3.head_object(Bucket=bucket, Key="data/normalised.parquet")
        assert head["ContentLength"] > 0


# ---------------------------------------------------------------------------
# main() / argparse
# ---------------------------------------------------------------------------


class TestCLI:
    def test_rejects_non_parquet_key(self, capsys):
        with pytest.raises(SystemExit):
            pipeline._parse_args(["--input", "in.csv", "--bucket", "b", "--key", "out.csv"])
        err = capsys.readouterr().err
        assert "must end in .parquet" in err

    def test_requires_all_three(self, capsys):
        with pytest.raises(SystemExit):
            pipeline._parse_args(["--input", "in.csv"])

    def test_parses_valid_args(self):
        ns = pipeline._parse_args(["--input", "in.csv", "--bucket", "b", "--key", "out.parquet"])
        assert ns.input == "in.csv"
        assert ns.bucket == "b"
        assert ns.key == "out.parquet"


# ---------------------------------------------------------------------------
# broken/ baseline still demonstrates the "before" anti-patterns
# ---------------------------------------------------------------------------


class TestBrokenBaselineIsBroken:
    """The broken/ files are part of the teaching diff. If somebody
    'helpfully' fixes them, the activity loses its before/after
    demonstration. These tests fail loudly when that happens."""

    BROKEN_PIPELINE = (ROOT / "broken" / "pipeline.py").read_text(encoding="utf-8")
    BROKEN_DEPLOY = (ROOT / "broken" / "deploy.sh").read_text(encoding="utf-8")

    def test_broken_pipeline_uses_print(self):
        assert "print(" in self.BROKEN_PIPELINE

    def test_broken_pipeline_has_no_argparse(self):
        assert "argparse" not in self.BROKEN_PIPELINE
        assert "sys.argv[1]" in self.BROKEN_PIPELINE

    def test_broken_deploy_is_manual(self):
        # Strip comments before scanning — the explanatory header
        # legitimately *mentions* OIDC/role-to-assume to contrast with
        # the fix, but the executable lines must not actually use them.
        executable = "\n".join(
            line
            for line in self.BROKEN_DEPLOY.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ).lower()
        assert "aws s3 cp" in executable
        for marker in ("configure-aws-credentials", "role-to-assume", "assumerole"):
            assert marker not in executable, (
                f"broken/deploy.sh executable lines must not use {marker!r}"
            )

    def test_fixed_pipeline_uses_logging_not_print(self):
        import ast

        fixed_src = (ROOT / "pipeline.py").read_text(encoding="utf-8")
        assert "logging.getLogger" in fixed_src

        # AST-walk for actual print() calls — string scanning matches
        # the word "print" inside the module docstring, which is fine.
        tree = ast.parse(fixed_src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", (
                    f"print() call in fixed pipeline.py at line {node.lineno}"
                )
