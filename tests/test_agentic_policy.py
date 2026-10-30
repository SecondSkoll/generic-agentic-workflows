"""Tests for the model/tool policy engine (Plan 4)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "agentic_policy.py"
SPEC = importlib.util.spec_from_file_location("agentic_policy", SCRIPT_PATH)
assert SPEC and SPEC.loader
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules["agentic_policy"] = POLICY
SPEC.loader.exec_module(POLICY)

CONFIGURATION_PATH = Path(__file__).parents[1] / "scripts" / "agentic_configuration.py"
CONFIGURATION_SPEC = importlib.util.spec_from_file_location(
    "agentic_configuration_for_policy_tests", CONFIGURATION_PATH
)
assert CONFIGURATION_SPEC and CONFIGURATION_SPEC.loader
CONFIGURATION = importlib.util.module_from_spec(CONFIGURATION_SPEC)
sys.modules[CONFIGURATION_SPEC.name] = CONFIGURATION
CONFIGURATION_SPEC.loader.exec_module(CONFIGURATION)

REPO_ROOT = Path(__file__).parents[1]


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
                "review-readonly", workflow="release-project-review"
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
        self.assertTrue(policy.context["allow_changed_file_context"])
        self.assertEqual(policy.context["max_context_rounds"], 3)

    def test_context_limit_cannot_be_broadened(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="pr-documentation-review", model_profile="review-readonly",
                invocation_inputs={"max_context_rounds": 4},
            )

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

    def test_changelog_editor_receives_scoped_workspace_capability(self):
        agent_caps = POLICY.parse_agent_capabilities(
            "---\nname: x\nmode: primary\npermission:\n  edit: allow\n  read: allow\n---\n"
        )
        policy = POLICY.merge_policy(
            workflow="pr-changelog-update",
            model_profile="changelog-writer",
            agent_capabilities=agent_caps,
        )
        self.assertEqual(
            policy.capabilities["filesystem"], "repository-workspace-allowlist"
        )
        self.assertEqual(policy.rejected_conflicts, ())

    def test_scoped_compatibility_does_not_apply_to_network(self):
        policy = POLICY.merge_policy(
            workflow="pr-changelog-update",
            model_profile="changelog-writer",
            agent_capabilities={"network": "allow"},
        )
        self.assertEqual(policy.capabilities["network"], "deny")
        self.assertTrue(any("network" in c for c in policy.rejected_conflicts))

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

    def test_cli_accepts_checked_in_changelog_agent_and_scoped_policy(self):
        import tempfile

        resolved = CONFIGURATION.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="changelog-update",
            workflow="pr-changelog-update",
        )
        data = json.loads(json.dumps(resolved.to_dict(), sort_keys=True))
        agent_text = (Path(data["bundle_root"]) / data["agent_file"]).read_text(
            encoding="utf-8"
        )
        agent_caps = POLICY.parse_agent_capabilities(agent_text)

        with tempfile.TemporaryDirectory() as tmp:
            resolved_path = Path(tmp) / "resolved.json"
            result_path = Path(tmp) / "policy.json"
            resolved_path.write_text(json.dumps(data), encoding="utf-8")
            rc = POLICY.main(
                [
                    "--workflow",
                    "pr-changelog-update",
                    "--model-profile",
                    data["model_profile"],
                    "--resolved-config",
                    str(resolved_path),
                    "--agent-capabilities-json",
                    json.dumps(agent_caps),
                    "--result",
                    str(result_path),
                ]
            )

            self.assertEqual(rc, 0)
            policy = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(
                policy["capabilities"]["filesystem"],
                "repository-workspace-allowlist",
            )
            self.assertEqual(policy["rejected_conflicts"], [])

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


class ChangelogUpdatePolicyTests(unittest.TestCase):
    """Builtin policy and model profile for pr-changelog-update."""

    def test_builtin_policy_registered(self):
        policy = POLICY.BUILTIN_SAFETY_POLICY["pr-changelog-update"]
        self.assertEqual(
            policy["required_output_contract"], "pr-changelog-update-v1"
        )
        self.assertEqual(policy["capabilities"]["shell"], POLICY.DENY)
        self.assertEqual(policy["capabilities"]["network"], "provider-only")
        self.assertEqual(
            policy["capabilities"]["github_write"],
            "pr-target-file-commit-or-comment",
        )
        self.assertEqual(policy["capabilities"]["delegation"], POLICY.DENY)
        self.assertEqual(policy["allowed_workflows"], ("pr-changelog-update",))
        self.assertEqual(policy["data_classification"], "repository-content")
        self.assertEqual(policy["max_tokens"], 16000)
        self.assertGreater(len(policy["deny_path_patterns"]), 0)

    def test_changelog_writer_model_profile_validates(self):
        profile = POLICY.validate_model_profile(
            "changelog-writer", workflow="pr-changelog-update"
        )
        self.assertEqual(profile["provider_model"], "openrouter/openai/gpt-5.6-terra")
        self.assertEqual(profile["allowed_workflows"], ["pr-changelog-update"])

    def test_changelog_writer_rejected_for_other_workflows(self):
        with self.assertRaises(POLICY.PolicyError):
            POLICY.validate_model_profile(
                "changelog-writer", workflow="release-project-review"
            )


if __name__ == "__main__":
    unittest.main()
