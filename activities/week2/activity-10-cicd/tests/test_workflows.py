"""
Workflow-shape tests.

These are static assertions over the YAML in `.github/workflows/` —
they read the files, parse them, and check the shape we depend on:

- Every workflow has the right triggers.
- The CI workflow is read-only (no id-token, no AWS).
- The deploy workflow has `permissions.id-token: write` and binds to
  the staging environment with the role assumption step.
- No long-lived AWS credentials are pasted into either workflow.
- The `ci-passed` aggregator job exists (branch protection depends on
  this name).

If the workflow YAML drifts from the documented contract, these tests
fail at PR time — exactly the audience that needs to know.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "activity-10-ci.yml"
DEPLOY = WORKFLOWS / "activity-10-deploy.yml"


def _load(path: Path) -> dict:
    # PyYAML parses the bare-`on:` key as the boolean `True` because
    # `on` is a YAML 1.1 truthy literal. Loaders that follow YAML 1.2
    # (e.g. ruamel) do not. We patch by re-reading and renaming the
    # key when needed.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in raw and "on" not in raw:
        raw["on"] = raw.pop(True)
    return raw


@pytest.fixture(scope="module")
def ci_yaml():
    assert CI.exists(), f"missing workflow: {CI}"
    return _load(CI)


@pytest.fixture(scope="module")
def deploy_yaml():
    assert DEPLOY.exists(), f"missing workflow: {DEPLOY}"
    return _load(DEPLOY)


# ---------------------------------------------------------------------------
# CI workflow
# ---------------------------------------------------------------------------


class TestCIWorkflow:
    def test_runs_on_pull_request(self, ci_yaml):
        triggers = ci_yaml["on"]
        assert "pull_request" in triggers
        assert "main" in triggers["pull_request"]["branches"]

    def test_runs_on_push_to_main(self, ci_yaml):
        triggers = ci_yaml["on"]
        assert "push" in triggers
        assert "main" in triggers["push"]["branches"]

    def test_path_filter_scopes_to_activity(self, ci_yaml):
        # Path filter present on both triggers, so a Week-1 README
        # change does not re-run Activity 10's matrix.
        for t in ("pull_request", "push"):
            paths = ci_yaml["on"][t]["paths"]
            assert any("activity-10-cicd" in p for p in paths)

    def test_concurrency_cancels_in_progress(self, ci_yaml):
        c = ci_yaml["concurrency"]
        assert c["cancel-in-progress"] is True
        assert "${{ github.ref }}" in c["group"]

    def test_workflow_is_read_only(self, ci_yaml):
        # CI must not have id-token: write — that privilege belongs
        # only to the deploy workflow.
        assert ci_yaml["permissions"] == {"contents": "read"}

    def test_has_lint_test_and_aggregator_jobs(self, ci_yaml):
        jobs = ci_yaml["jobs"]
        assert "lint" in jobs
        assert "test" in jobs
        # ci-passed is the stable name branch protection requires.
        assert "ci-passed" in jobs

    def test_aggregator_depends_on_lint_and_test(self, ci_yaml):
        agg = ci_yaml["jobs"]["ci-passed"]
        assert set(agg["needs"]) == {"lint", "test"}

    def test_test_matrix_covers_python_311_and_312(self, ci_yaml):
        matrix = ci_yaml["jobs"]["test"]["strategy"]["matrix"]
        assert "3.11" in matrix["python-version"]
        assert "3.12" in matrix["python-version"]

    def test_pip_cache_is_enabled(self, ci_yaml):
        # Cheap and important — without this every run re-downloads
        # pandas + pyarrow.
        for job_name in ("lint", "test"):
            steps = ci_yaml["jobs"][job_name]["steps"]
            setup = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
            assert setup["with"]["cache"] == "pip"

    def test_coverage_threshold_enforced(self, ci_yaml):
        steps = ci_yaml["jobs"]["test"]["steps"]
        run_steps = [s.get("run", "") for s in steps if "run" in s]
        assert any("--cov-fail-under" in s for s in run_steps)

    def test_has_no_aws_credentials_inputs(self, ci_yaml):
        text = CI.read_text(encoding="utf-8").lower()
        # CI never touches AWS. If these tokens appear here, somebody
        # tried to add a deploy step and put it in the wrong file.
        assert "aws-access-key-id" not in text
        assert "aws_secret_access_key" not in text
        assert "configure-aws-credentials" not in text


# ---------------------------------------------------------------------------
# Deploy workflow
# ---------------------------------------------------------------------------


class TestDeployWorkflow:
    def test_triggers_on_push_to_main_only(self, deploy_yaml):
        triggers = deploy_yaml["on"]
        # Critical: no `pull_request` trigger. A PR from a fork must
        # never reach this workflow.
        assert "pull_request" not in triggers
        assert "main" in triggers["push"]["branches"]

    def test_path_filter_scopes_to_activity(self, deploy_yaml):
        paths = deploy_yaml["on"]["push"]["paths"]
        assert any("activity-10-cicd" in p for p in paths)

    def test_supports_manual_dispatch(self, deploy_yaml):
        # workflow_dispatch is required for the rollback runbook.
        assert "workflow_dispatch" in deploy_yaml["on"]

    def test_concurrency_serialises_per_environment(self, deploy_yaml):
        c = deploy_yaml["concurrency"]
        # Two merges to main must NOT both deploy in parallel — the
        # later one waits, the earlier one is not cancelled (would
        # leave staging in a half-deployed state).
        assert c["cancel-in-progress"] is False
        assert "staging" in c["group"]

    def test_has_id_token_write_permission(self, deploy_yaml):
        # Load-bearing for OIDC. Without this line aws-actions/
        # configure-aws-credentials cannot exchange the JWT.
        assert deploy_yaml["permissions"]["id-token"] == "write"
        assert deploy_yaml["permissions"]["contents"] == "read"

    def test_deploy_job_binds_to_staging_environment(self, deploy_yaml):
        env = deploy_yaml["jobs"]["deploy-staging"]["environment"]
        # Either the shorthand `environment: staging` or the long
        # form `environment: { name: staging }` — both are valid.
        if isinstance(env, dict):
            assert env["name"] == "staging"
        else:
            assert env == "staging"

    def test_deploy_uses_oidc_role_assumption(self, deploy_yaml):
        steps = deploy_yaml["jobs"]["deploy-staging"]["steps"]
        creds = next(
            (s for s in steps if "configure-aws-credentials" in s.get("uses", "")),
            None,
        )
        assert creds is not None, "missing aws-actions/configure-aws-credentials step"
        assert "role-to-assume" in creds["with"]
        assert creds["with"]["role-to-assume"].startswith("${{ secrets.")

    def test_deploy_does_not_use_static_credentials(self, deploy_yaml):
        # Strip comments before scanning. Comments may legitimately
        # mention these strings to explain *why they must not appear* —
        # the executable YAML must not use them.
        text = "\n".join(
            line
            for line in DEPLOY.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ).lower()
        assert "aws-access-key-id" not in text
        assert "aws_secret_access_key" not in text
        # AKIA is the prefix on real AWS access keys; appearing in
        # executable YAML would be a literal leaked credential.
        assert "akia" not in text

    def test_deploy_runs_smoke_after_upload(self, deploy_yaml):
        steps = deploy_yaml["jobs"]["deploy-staging"]["steps"]
        names = [s.get("name", "") for s in steps]
        assert any("deploy" in n for n in names)
        assert any("smoke" in n for n in names)
        # Deploy must come before smoke.
        deploy_idx = next(i for i, n in enumerate(names) if "deploy" in n and "smoke" not in n)
        smoke_idx = next(i for i, n in enumerate(names) if "smoke" in n)
        assert deploy_idx < smoke_idx

    def test_role_session_name_includes_sha(self, deploy_yaml):
        # CloudTrail records role-session-name. Pinning it to the SHA
        # makes every deploy independently auditable.
        steps = deploy_yaml["jobs"]["deploy-staging"]["steps"]
        creds = next(s for s in steps if "configure-aws-credentials" in s.get("uses", ""))
        assert "sha" in creds["with"]["role-session-name"].lower()


# ---------------------------------------------------------------------------
# IAM artifacts shipped with the activity
# ---------------------------------------------------------------------------


class TestIAMTemplates:
    ACTIVITY_ROOT = Path(__file__).resolve().parent.parent

    def test_trust_policy_pins_to_staging_environment(self):
        import json

        policy = json.loads(
            (self.ACTIVITY_ROOT / "iam" / "github-oidc-trust-policy.json").read_text()
        )
        sub = policy["Statement"][0]["Condition"]["StringEquals"][
            "token.actions.githubusercontent.com:sub"
        ]
        assert ":environment:staging" in sub

    def test_permissions_policy_is_least_privilege(self):
        import json

        policy = json.loads((self.ACTIVITY_ROOT / "iam" / "staging-deploy-policy.json").read_text())
        actions: set[str] = set()
        resources: set[str] = set()
        for stmt in policy["Statement"]:
            acts = stmt["Action"]
            actions.update([acts] if isinstance(acts, str) else acts)
            res = stmt["Resource"]
            resources.update([res] if isinstance(res, str) else res)
        # No wildcard actions, no IAM, no Secrets Manager.
        assert "*" not in actions
        assert not any(a.startswith("iam:") for a in actions)
        assert not any(a.startswith("secretsmanager:") for a in actions)
        # All resources scoped to the staging bucket only.
        for r in resources:
            assert "fossilrag-staging-deploy" in r

    def test_iam_policies_are_valid_json(self):
        import json

        for name in ("github-oidc-trust-policy.json", "staging-deploy-policy.json"):
            json.loads((self.ACTIVITY_ROOT / "iam" / name).read_text())


# ---------------------------------------------------------------------------
# Branch protection doc references the right status check name
# ---------------------------------------------------------------------------


class TestBranchProtectionDoc:
    DOC = (Path(__file__).resolve().parent.parent / "docs" / "branch-protection.md").read_text(
        encoding="utf-8"
    )

    def test_documents_required_status_check(self):
        # The required check name in the doc MUST match the
        # aggregator job name in ci.yml — otherwise enabling the
        # protection in the GitHub UI silently lists nothing.
        assert "ci-passed" in self.DOC

    def test_documents_environment_protection(self):
        for marker in ("staging", "Required reviewers", "Wait timer"):
            assert marker in self.DOC

    def test_documents_force_push_off(self):
        assert "Allow force pushes" in self.DOC
