"""
Test suite for Activity 9 — credentials management.

Two layers, same pattern as Activities 7 & 8:

1. Source-file analysis — verifies the broken/ file still demonstrates
   every anti-pattern (so the diff stays didactic) and the fixed code
   uses every required pattern.

2. Behaviour tests — exercise `secrets_manager.get_secret()` against:
       - the env layer
       - a stub Secrets Manager client
       - the in-memory TTL cache
       - the no-default-fallback contract

The tests deliberately do NOT use any real secrets or hit AWS.
"""

from __future__ import annotations

import ast
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BROKEN = (ROOT / "broken" / "config.py").read_text(encoding="utf-8")
CONFIG = (ROOT / "config.py").read_text(encoding="utf-8")
SECRETS = (ROOT / "secrets_manager.py").read_text(encoding="utf-8")
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
PRE_COMMIT = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
IAM_POLICY = (ROOT / "iam" / "secrets-reader-policy.json").read_text(encoding="utf-8")


# ===========================================================================
# Layer 1 — broken file demonstrates every anti-pattern
# ===========================================================================


class TestBrokenAntiPatterns:
    def test_has_hardcoded_openai_key(self):
        # The broken file MUST have a hardcoded API key string. We don't
        # care about the value as long as it's a literal sk-... pattern.
        assert re.search(r'OPENAI_API_KEY\s*=\s*["\']sk-', BROKEN), (
            "broken/config.py must demonstrate a hardcoded OpenAI key"
        )

    def test_has_hardcoded_aws_keys(self):
        assert re.search(r'AWS_ACCESS_KEY_ID\s*=\s*["\']AKIA', BROKEN)
        assert re.search(r'AWS_SECRET_ACCESS_KEY\s*=\s*["\'][A-Za-z0-9/+]{10,}', BROKEN)

    def test_boto3_client_takes_explicit_credentials(self):
        # The smoking gun — passing keys to boto3.client bypasses IAM roles.
        assert "aws_access_key_id=AWS_ACCESS_KEY_ID" in BROKEN
        assert "aws_secret_access_key=AWS_SECRET_ACCESS_KEY" in BROKEN

    def test_prints_secret_at_startup(self):
        # The leaks-to-CloudWatch anti-pattern.
        assert "print(" in BROKEN
        assert "OPENAI_API_KEY" in BROKEN.split("print(", 1)[1].split(")", 1)[0]

    def test_does_not_use_secrets_manager(self):
        assert "secretsmanager" not in BROKEN
        assert "get_secret(" not in BROKEN

    def test_does_not_use_environment_variables(self):
        assert "os.environ" not in BROKEN
        assert "os.getenv" not in BROKEN

    def test_obviously_fake_strings(self):
        """Sanity check: the literals in broken/ are clearly fake.

        We guard against the day someone tries to make the broken file
        "more realistic" by pasting a real key. The string FAKE must
        appear at least three times.
        """
        assert BROKEN.upper().count("FAKE") >= 3, (
            "broken/config.py credential strings must contain FAKE multiple times "
            "to make it obvious they are not real secrets"
        )


# ===========================================================================
# Layer 1 — fixed code uses every required pattern
# ===========================================================================


class TestFixedPatterns:
    def test_no_hardcoded_secrets(self):
        # Catch literals that smell like real keys.
        forbidden = [
            r'OPENAI_API_KEY\s*=\s*["\']sk-',
            r'AWS_ACCESS_KEY_ID\s*=\s*["\']AKIA',
            r'AWS_SECRET_ACCESS_KEY\s*=\s*["\'][A-Za-z0-9/+]{20,}["\']',
            r'aws_access_key_id\s*=\s*["\']',
            r'aws_secret_access_key\s*=\s*["\']',
        ]
        for pattern in forbidden:
            assert not re.search(pattern, CONFIG), (
                f"config.py contains forbidden pattern {pattern!r}"
            )
            assert not re.search(pattern, SECRETS), (
                f"secrets_manager.py contains forbidden pattern {pattern!r}"
            )

    def test_uses_env_var_layer(self):
        assert "os.environ" in SECRETS
        assert "_from_env" in SECRETS

    def test_uses_secrets_manager_layer(self):
        assert "secretsmanager" in SECRETS
        assert "GetSecretValue" in SECRETS or "get_secret_value" in SECRETS

    def test_supports_caching(self):
        assert "Cache" in SECRETS or "cache" in SECRETS
        assert "ttl" in SECRETS.lower()

    def test_fails_loud_on_missing(self):
        assert "SecretNotFoundError" in SECRETS
        # Must actually raise it, not just define it.
        assert "raise SecretNotFoundError" in SECRETS

    def test_boto3_client_uses_default_chain(self):
        """boto3 clients in config.py must NOT pass aws_access_key_id."""
        tree = ast.parse(CONFIG)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match boto3.client(...)
            if (isinstance(func, ast.Attribute) and func.attr == "client"
                    and isinstance(func.value, ast.Name) and func.value.id == "boto3"):
                kwargs = {kw.arg for kw in node.keywords if kw.arg}
                assert "aws_access_key_id" not in kwargs
                assert "aws_secret_access_key" not in kwargs

    def test_no_print_logging(self):
        assert "print(" not in CONFIG
        assert "print(" not in SECRETS

    def test_module_import_does_not_resolve_secrets(self):
        """Importing config must not resolve secrets at import time.

        Walk the module top-level for Call nodes that invoke `get_secret`.
        Anything inside a FunctionDef body is fine (lazy resolution).
        """
        tree = ast.parse(CONFIG)
        for node in tree.body:
            # Skip nested function / class bodies — anything inside them
            # only fires when the function is actually called.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    assert child.func.id != "get_secret", (
                        "config.py calls get_secret() at module import"
                    )
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    assert child.func.attr != "get_secret", (
                        "config.py calls .get_secret() at module import"
                    )

    def test_modules_parse_cleanly(self):
        ast.parse(BROKEN)
        ast.parse(CONFIG)
        ast.parse(SECRETS)


# ===========================================================================
# Layer 1 — operational hygiene (gitignore / pre-commit / IAM / .env.example)
# ===========================================================================


class TestOperationalHygiene:
    def test_gitignore_excludes_env(self):
        assert ".env" in GITIGNORE
        assert "!.env.example" in GITIGNORE  # must still allow the template

    def test_gitignore_excludes_credentials_files(self):
        assert "credentials" in GITIGNORE
        assert "*.pem" in GITIGNORE
        assert "*.key" in GITIGNORE

    def test_env_example_has_no_values(self):
        # Each KEY= should be followed by a newline or whitespace, not a value.
        for line in ENV_EXAMPLE.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            # Allow placeholder values for non-secret config like region/bucket
            if key in {
                "AWS_REGION", "S3_BUCKET", "SECRETS_PREFIX",
                "LOG_LEVEL", "LOG_SECRET_PROBE",
            }:
                continue
            assert value == "", (
                f".env.example must not contain a value for the secret {key!r} "
                f"(found {value!r})"
            )

    def test_pre_commit_has_secret_scanners(self):
        assert "detect-secrets" in PRE_COMMIT
        assert "gitleaks" in PRE_COMMIT
        assert "detect-aws-credentials" in PRE_COMMIT

    def test_iam_policy_is_least_privilege(self):
        import json
        policy = json.loads(IAM_POLICY)
        # Only allow the two ReadOnly secret actions.
        statements = policy["Statement"]
        secret_stmt = next(s for s in statements if "secretsmanager:GetSecretValue" in s["Action"])
        assert "secretsmanager:CreateSecret" not in secret_stmt["Action"]
        assert "secretsmanager:DeleteSecret" not in secret_stmt["Action"]
        assert "secretsmanager:UpdateSecret" not in secret_stmt["Action"]
        # Resource must be specific ARNs, not a wildcard.
        for arn in secret_stmt["Resource"]:
            assert arn.startswith("arn:aws:secretsmanager:")
            assert "*" not in arn[:-1]  # trailing -* version suffix is fine


# ===========================================================================
# Layer 2 — secrets_manager behaviour
# ===========================================================================


class FakeSecretsManager:
    """Stand-in for boto3's secretsmanager client."""

    def __init__(self, mapping=None, raise_on=None):
        self.mapping = mapping or {}
        self.raise_on = raise_on or set()
        self.calls = []

    def get_secret_value(self, *, SecretId):
        self.calls.append(SecretId)
        if SecretId in self.raise_on:
            raise RuntimeError(f"forced failure on {SecretId}")
        if SecretId in self.mapping:
            return {"SecretString": self.mapping[SecretId]}
        raise RuntimeError(f"ResourceNotFoundException: {SecretId}")


@pytest.fixture(autouse=True)
def _clear_cache():
    from secrets_manager import clear_cache
    clear_cache()
    yield
    clear_cache()


def test_get_secret_reads_env_first(monkeypatch):
    from secrets_manager import get_secret

    monkeypatch.setenv("OPENAI_API_KEY", "from-env-not-aws")
    sm = FakeSecretsManager(mapping={"fossilrag/prod/openai_api_key": "from-aws"})

    assert get_secret("OPENAI_API_KEY", sm_client=sm) == "from-env-not-aws"
    assert sm.calls == [], "Secrets Manager must NOT be hit when env wins"


def test_get_secret_falls_back_to_secrets_manager(monkeypatch):
    from secrets_manager import get_secret

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sm = FakeSecretsManager(mapping={"fossilrag/prod/openai_api_key": "from-aws"})

    assert get_secret("OPENAI_API_KEY", sm_client=sm) == "from-aws"
    assert sm.calls == ["fossilrag/prod/openai_api_key"]


def test_secret_is_cached(monkeypatch):
    from secrets_manager import get_secret

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sm = FakeSecretsManager(mapping={"fossilrag/prod/openai_api_key": "v1"})

    get_secret("OPENAI_API_KEY", sm_client=sm)
    get_secret("OPENAI_API_KEY", sm_client=sm)
    get_secret("OPENAI_API_KEY", sm_client=sm)

    assert len(sm.calls) == 1, (
        f"get_secret must cache; got {len(sm.calls)} Secrets Manager calls"
    )


def test_cache_respects_ttl(monkeypatch):
    from secrets_manager import get_secret

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sm = FakeSecretsManager(mapping={"fossilrag/prod/openai_api_key": "v1"})

    get_secret("OPENAI_API_KEY", sm_client=sm, cache_ttl_sec=0.01)
    time.sleep(0.05)
    sm.mapping["fossilrag/prod/openai_api_key"] = "v2"
    assert get_secret("OPENAI_API_KEY", sm_client=sm, cache_ttl_sec=0.01) == "v2"


def test_missing_secret_raises_loudly(monkeypatch):
    from secrets_manager import get_secret, SecretNotFoundError

    monkeypatch.delenv("NON_EXISTENT", raising=False)
    sm = FakeSecretsManager()

    with pytest.raises(SecretNotFoundError) as exc:
        get_secret("NON_EXISTENT", sm_client=sm)
    # Must NOT include the secret VALUE in the error (we don't have one) —
    # but the NAME is fine, that's the whole point of identifying what's missing.
    assert "NON_EXISTENT" in str(exc.value)


def test_secrets_manager_returns_unwrapped_json(monkeypatch):
    """If SM returns a JSON object with a single key, unwrap it."""
    import json
    from secrets_manager import get_secret

    monkeypatch.delenv("DATABASE_URL", raising=False)
    sm = FakeSecretsManager(mapping={
        "fossilrag/prod/database_url": json.dumps({"url": "postgres://..."}),
    })
    assert get_secret("DATABASE_URL", sm_client=sm) == "postgres://..."


def test_no_secret_value_logged_in_normal_mode(monkeypatch, caplog):
    """The success log must include the name and source, never the value."""
    import logging
    from secrets_manager import get_secret

    monkeypatch.setenv("OPENAI_API_KEY", "ultra-secret-DO-NOT-LEAK")
    monkeypatch.delenv("LOG_SECRET_PROBE", raising=False)

    with caplog.at_level(logging.DEBUG, logger="secrets"):
        get_secret("OPENAI_API_KEY", sm_client=FakeSecretsManager())

    for record in caplog.records:
        assert "ultra-secret-DO-NOT-LEAK" not in record.getMessage()


def test_redact_strips_value(monkeypatch):
    from secrets_manager import _redact
    redacted = _redact("ultra-secret-1234567890")
    assert "ultra-secret" not in redacted
    assert "1234567890" not in redacted
    # Should keep the first 4 chars only.
    assert redacted.startswith("ultr")
