"""Tests for the model/tool policy engine (Plan 4)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "agentic_policy.py"
SPEC = importlib.util.spec_from_file_location("agentic_policy", SCRIPT_PATH)
assert SPEC and SPEC.loader
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules["agentic_policy"] = POLICY
SPEC.loader.exec_module(POLICY)


class CapabilityIntersectionTests(unittest.TestCase):
    def test_deny_always_wins(self):
        self.assertEqual(POLICY.intersect_capability("allow", "deny"), "deny")
        self.assertEqual(POLICY.intersect_capability("deny", "allow"), "deny")

    def test_equal_grants_intersect(self):
        self.assertEqual(
            POLICY.intersect_capability("review-comment-only", "review-comment-only"),
            "review-comment-only",
        )

    def test_different_grants_intersect_to_deny(self):
        self.assertEqual(
            POLICY.intersect_capability(
                "read-trusted-checkout-diff", "review-comment-only"
            ),
            "deny",
        )


class ModelProfileTests(unittest.TestCase):
    def test_unknown_profile_rejected(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_model_profile("nope", workflow="pr-documentation-review")

    def test_profile_workflow_mismatch_rejected(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_model_profile(
                "review-readonly", workflow="issue-implementation"
            )

    def test_profile_above_ceiling_rejected(self):
        # Temporarily patch a profile to exceed the ceiling.
        original = dict(POLICY.MODEL_PROFILES["review-readonly"])
        try:
            POLICY.MODEL_PROFILES["review-readonly"]["max_tokens"] = 999999
            with self.assertRaises(POLICY.PolicyError):
                POLICY.validate_model_profile(
                    "review-readonly", workflow="pr-documentation-review"
                )
        finally:
            POLICY.MODEL_PROFILES["review-readonly"] = original


class MergePolicyTests(unittest.TestCase):
    def test_default_merge_review(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
        )
        self.assertEqual(policy.workflow, "pr-documentation-review")
        self.assertEqual(policy.model_profile, "review-readonly")
        self.assertEqual(policy.capabilities["shell"], "deny")
        self.assertEqual(policy.capabilities["delegation"], "deny")
        self.assertTrue(policy.sha256)

    def test_default_merge_implementation(self):
        policy = POLICY.merge_policy(
            workflow="issue-implementation",
            model_profile="implementation-planner",
        )
        self.assertEqual(policy.capabilities["github_write"], "scoped-branch-pr-issue")
        self.assertEqual(policy.capabilities["delegation"], "planner-to-executor")

    def test_bundle_cannot_broaden_capability(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                bundle_policy={"capabilities": {"shell": "allow"}},
            )

    def test_bundle_can_narrow_capability(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            bundle_policy={"capabilities": {"github_write": "deny"}},
        )
        self.assertEqual(policy.capabilities["github_write"], "deny")

    def test_bundle_max_tokens_above_ceiling_rejected(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                bundle_policy={"max_tokens": 999999},
            )

    def test_agent_requesting_forbidden_edit_denied(self):
        # For PR review, builtin filesystem is read-only; an agent requesting
        # edit: allow cannot broaden it.
        agent_caps = POLICY.parse_agent_capabilities(
            "---\nname: x\nmode: primary\npermission:\n  edit: allow\n---\n"
        )
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            agent_capabilities=agent_caps,
        )
        # The agent's edit request maps to the filesystem axis. Builtin grants
        # read-trusted-checkout-diff; the agent requests "allow" which is a
        # different (broader) value, so it is denied.
        self.assertEqual(policy.capabilities["filesystem"], "deny")
        self.assertTrue(any("filesystem" in c for c in policy.rejected_conflicts))

    def test_agent_requesting_unknown_axis_recorded(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            agent_capabilities={"time-travel": "allow"},
        )
        self.assertTrue(any("time-travel" in c for c in policy.rejected_conflicts))

    def test_overlay_cannot_grant_capabilities(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                overlay={"capabilities": {"shell": "allow"}},
            )

    def test_overlay_can_lower_max_comments(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            overlay={"max_comments": 5, "allowed_focus": ["documentation"]},
        )
        # No exception; overlay accepted.
        self.assertEqual(policy.workflow, "pr-documentation-review")

    def test_overlay_test_commands_only_for_implementation(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                overlay={"test_commands": ["python -m unittest"]},
            )
        # Allowed for implementation.
        policy = POLICY.merge_policy(
            workflow="issue-implementation",
            model_profile="implementation-planner",
            overlay={"test_commands": ["python -m unittest"]},
        )
        self.assertEqual(policy.test_commands, ("python -m unittest",))

    def test_overlay_unknown_focus_rejected(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                overlay={"allowed_focus": ["exfiltrate"]},
            )

    def test_invocation_strict_only(self):
        # invocation max_comments cannot exceed overlay maximum.
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review",
                model_profile="review-readonly",
                overlay={"max_comments": 5},
                invocation_inputs={"max_comments": 10},
            )

    def test_invocation_dry_run_disables_publication(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            invocation_inputs={"dry_run": True},
        )
        self.assertFalse(policy.publication_allowed)

    def test_invocation_validate_only_disables_publication(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            invocation_inputs={"validate_only": True},
        )
        self.assertFalse(policy.publication_allowed)

    def test_review_stays_readonly_regardless_of_agent(self):
        # Even if an agent requests shell and network, the merge must deny
        # for review workflows.
        agent_caps = {"shell": "allow", "network": "provider-and-github-api-only"}
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            agent_capabilities=agent_caps,
        )
        self.assertEqual(policy.capabilities["shell"], "deny")

    def test_effective_policy_report_has_layers(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            bundle_policy={"capabilities": {"github_write": "deny"}},
        )
        self.assertGreaterEqual(len(policy.layers), 3)
        names = [layer["layer"] for layer in policy.layers]
        self.assertIn("builtin-safety", names)
        self.assertIn("model-profile", names)
        self.assertIn("bundle-profile", names)


class ChangedPathTests(unittest.TestCase):
    def test_workflow_file_change_rejected(self):
        offenders = POLICY.enforce_changed_paths(
            [".github/workflows/opencode-review.yml", "docs/readme.md"],
            workflow="issue-implementation",
        )
        self.assertIn(".github/workflows/opencode-review.yml", offenders)
        self.assertNotIn("docs/readme.md", offenders)

    def test_dependency_file_change_rejected(self):
        offenders = POLICY.enforce_changed_paths(
            ["package.json", "package-lock.json", "uv.lock", "pyproject.toml"],
            workflow="issue-implementation",
        )
        self.assertEqual(
            set(offenders),
            {"package.json", "package-lock.json", "uv.lock", "pyproject.toml"},
        )

    def test_agent_instruction_change_rejected(self):
        offenders = POLICY.enforce_changed_paths(
            [
                ".opencode/agents/x.md",
                ".opencode/skills/y/SKILL.md",
                ".opencode/configuration/z/bundle.json",
            ],
            workflow="issue-implementation",
        )
        self.assertEqual(len(offenders), 3)

    def test_clean_diff_passes(self):
        offenders = POLICY.enforce_changed_paths(
            ["scripts/foo.py", "tests/test_foo.py", "docs/guide.md"],
            workflow="issue-implementation",
        )
        self.assertEqual(offenders, [])


class CollectChangedPathsTests(unittest.TestCase):
    """Required change #2: include untracked and committed files."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        import subprocess

        env = os.environ.copy()
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_AUTHOR_NAME"] = "test"
        env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        env["GIT_COMMITTER_NAME"] = "test"
        env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        self._env = env
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.repo)], check=True, env=env
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "test"],
            check=True,
            env=env,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.com"],
            check=True,
            env=env,
        )
        (self.repo / "README.md").write_text("init\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "README.md"], check=True, env=env
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "init"],
            check=True,
            env=env,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args):
        import subprocess

        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            env=self._env,
            capture_output=True,
            text=True,
        )

    def test_untracked_workflow_file_is_collected(self):
        # Create an untracked file in a denied category.
        wf_dir = self.repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "evil.yml").write_text("name: evil\n", encoding="utf-8")
        paths = POLICY.collect_implementation_changed_paths(self.repo)
        self.assertIn(".github/workflows/evil.yml", paths)

    def test_untracked_workflow_file_rejected_by_enforcer(self):
        wf_dir = self.repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "evil.yml").write_text("name: evil\n", encoding="utf-8")
        paths = POLICY.collect_implementation_changed_paths(self.repo)
        offenders = POLICY.enforce_changed_paths(paths, workflow="issue-implementation")
        self.assertIn(".github/workflows/evil.yml", offenders)

    def test_committed_workflow_file_is_collected(self):
        # Create and commit a workflow file (agent-created commit).
        wf_dir = self.repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "evil.yml").write_text("name: evil\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "agent commit")
        paths = POLICY.collect_implementation_changed_paths(
            self.repo, base_ref="HEAD~1"
        )
        self.assertIn(".github/workflows/evil.yml", paths)

    def test_clean_workspace_passes(self):
        paths = POLICY.collect_implementation_changed_paths(self.repo)
        # Only README.md tracked, no changes.
        self.assertEqual(paths, [])


class RedactionTests(unittest.TestCase):
    def test_redact_token(self):
        text = (
            "token=ghp_abcdefghijklmnopqrstuvwxyz and sk-abcdefghijklmnopqrstuvwxyz0123"
        )
        redacted = POLICY.redact_secrets(text)
        self.assertNotIn("ghp_", redacted)
        self.assertNotIn("sk-", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_redacted_policy_report_strips_secrets(self):
        policy = POLICY.merge_policy(
            workflow="pr-documentation-review",
            model_profile="review-readonly",
            bundle_policy={"note": "token=ghp_aaaaaaaaaaaaaaaaaaaaaaaa"},
        )
        report = POLICY.redacted_policy_report(policy)
        # Walk the report to ensure no secret remains.
        text = json.dumps(report)
        self.assertNotIn("ghp_", text)


class CliTests(unittest.TestCase):
    def test_cli_returns_zero(self):
        rc = POLICY.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--model-profile",
                "review-readonly",
            ]
        )
        self.assertEqual(rc, 0)

    def test_cli_rejects_unknown_profile(self):
        rc = POLICY.main(
            ["--workflow", "pr-documentation-review", "--model-profile", "nope"]
        )
        self.assertEqual(rc, 1)

    def test_cli_wires_bundle_policy_and_invocation_mode(self):
        """Required change #6: CLI accepts resolved bundle policy + invocation mode."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "bundle_policy": {"capabilities": {"github_write": "deny"}},
                        "limits": {"max_comments": 5},
                    }
                ),
                encoding="utf-8",
            )
            result_path = Path(tmp) / "policy.json"
            rc = POLICY.main(
                [
                    "--workflow",
                    "pr-documentation-review",
                    "--model-profile",
                    "review-readonly",
                    "--resolved-config",
                    str(bundle_path),
                    "--mode",
                    "dry-run",
                    "--result",
                    str(result_path),
                ]
            )
            self.assertEqual(rc, 0)
            policy = json.loads(result_path.read_text())
            # Bundle narrowed github_write to deny.
            self.assertEqual(policy["capabilities"]["github_write"], "deny")
            # Dry-run disables publication.
            self.assertFalse(policy["publication_allowed"])

    def test_cli_rejects_bundle_escalation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "bundle_policy": {"capabilities": {"shell": "allow"}},
                    }
                ),
                encoding="utf-8",
            )
            rc = POLICY.main(
                [
                    "--workflow",
                    "pr-documentation-review",
                    "--model-profile",
                    "review-readonly",
                    "--resolved-config",
                    str(bundle_path),
                ]
            )
            self.assertEqual(rc, 1)


class DelegatedAgentValidationTests(unittest.TestCase):
    """Required change #6: validate executor front matter."""

    def test_valid_executor_accepted(self):
        text = (
            "---\nname: executor\nmode: subagent\npermission:\n  edit: allow\n  bash: allow\n---\n"
            "# executor\n"
        )
        front = POLICY.validate_delegated_agent(text, workflow="issue-implementation")
        self.assertEqual(front["name"], "executor")

    def test_executor_must_be_subagent(self):
        text = "---\nname: executor\nmode: primary\n---\n# executor\n"
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_delegated_agent(text, workflow="issue-implementation")

    def test_executor_forbidden_capability_rejected(self):
        # For PR review, shell is DENY; a delegated agent requesting bash
        # (shell) must be rejected.
        text = (
            "---\nname: sub\nmode: subagent\npermission:\n  bash: allow\n---\n# sub\n"
        )
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_delegated_agent(text, workflow="pr-documentation-review")


if __name__ == "__main__":
    unittest.main()
