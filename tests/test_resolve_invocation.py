"""Tests for reusable-workflow invocation normalization and validation."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "resolve_invocation.py"
SPEC = importlib.util.spec_from_file_location("resolve_invocation", SCRIPT_PATH)
assert SPEC and SPEC.loader
RESOLVER = importlib.util.module_from_spec(SPEC)
sys.modules["resolve_invocation"] = RESOLVER
SPEC.loader.exec_module(RESOLVER)


class ResolveInvocationTests(unittest.TestCase):
    """Verify input normalization and rejection rules from Plan 1."""

    def test_pr_review_minimum_inputs_resolve(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
        )
        self.assertEqual(resolved.workflow, "pr-documentation-review")
        self.assertEqual(resolved.configuration_source, "local")
        self.assertIsNone(resolved.configuration_ref)
        self.assertEqual(resolved.configuration_profile, "documentation-review")
        self.assertFalse(resolved.dry_run)
        self.assertFalse(resolved.validate_only)
        self.assertEqual(resolved.target_number, 42)

    def test_issue_feedback_minimum_inputs_resolve(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="issue-feedback",
            configuration_profile="issue-triage",
        )
        self.assertEqual(resolved.workflow, "issue-feedback")
        self.assertIsNone(resolved.target_number)

    def test_issue_implementation_minimum_inputs_resolve(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="issue-implementation",
            configuration_profile="default-implementation",
            target_number=7,
        )
        self.assertEqual(resolved.target_number, 7)

    def test_string_target_number_is_normalized(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number="42",
        )
        self.assertEqual(resolved.target_number, 42)

    def test_string_boolean_inputs_are_normalized(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=1,
            dry_run="true",
            validate_only="false",
        )
        self.assertTrue(resolved.dry_run)
        self.assertFalse(resolved.validate_only)

    def test_string_max_comments_is_bounded(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=1,
            max_comments="8",
        )
        self.assertEqual(resolved.max_comments, 8)

    def test_max_comments_above_bound_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=1,
                max_comments=21,
            )

    def test_max_comments_below_bound_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=1,
                max_comments=-1,
            )

    def test_focus_is_allowlisted(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=1,
            focus="documentation",
        )
        self.assertEqual(resolved.focus, "documentation")

    def test_unknown_focus_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=1,
                focus="exfiltrate-secrets",
            )

    def test_invalid_profile_identifier_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="Documentation Review",
                target_number=1,
            )

    def test_profile_with_uppercase_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="DocsReview",
                target_number=1,
            )

    def test_unknown_workflow_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="arbitrary-workflow",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_unknown_configuration_source_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_source="https://example.com/bundle",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_remote_source_requires_full_sha(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_source="local",
                configuration_ref="main",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_local_source_rejects_mutable_ref(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_source="local",
                configuration_ref="v1.2.3",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_full_sha_is_accepted_when_set(self) -> None:
        sha = "a" * 40
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=1,
            configuration_ref=sha,
        )
        self.assertEqual(resolved.configuration_ref, sha)

    def test_dry_run_and_validate_only_are_mutually_exclusive(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=1,
                dry_run=True,
                validate_only=True,
            )

    def test_pr_review_requires_target_number(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
            )

    def test_issue_implementation_requires_target_number(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="issue-implementation",
                configuration_profile="default-implementation",
            )

    def test_pr_review_rejects_max_issues(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=1,
                max_issues=5,
            )

    def test_issue_feedback_rejects_max_comments(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="issue-feedback",
                configuration_profile="issue-triage",
                max_comments=5,
            )

    def test_issue_implementation_rejects_focus(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="issue-implementation",
                configuration_profile="default-implementation",
                target_number=1,
                focus="documentation",
            )

    def test_non_positive_target_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=0,
            )

    def test_request_label_is_validated(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="issue-implementation",
            configuration_profile="default-implementation",
            target_number=1,
            request_label="ai-review-requested",
        )
        self.assertEqual(resolved.request_label, "ai-review-requested")

    def test_invalid_request_label_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="issue-implementation",
                configuration_profile="default-implementation",
                target_number=1,
                request_label="AI Review Requested!",
            )

    def test_to_json_is_deterministic(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=1,
        )
        first = resolved.to_json()
        second = resolved.to_json()
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["workflow"], "pr-documentation-review")


class ResolveFromEnvTests(unittest.TestCase):
    """Verify environment-driven resolution used by the workflow step."""

    def _base_env(self) -> dict[str, str]:
        return {
            "AGENTIC_WORKFLOW": "pr-documentation-review",
            "AGENTIC_CONFIGURATION_PROFILE": "documentation-review",
            "AGENTIC_TARGET_NUMBER": "42",
        }

    def test_env_resolution_succeeds(self) -> None:
        resolved = RESOLVER.resolve_from_env(self._base_env())
        self.assertEqual(resolved.workflow, "pr-documentation-review")
        self.assertEqual(resolved.target_number, 42)

    def test_env_resolution_defaults_booleans_false(self) -> None:
        resolved = RESOLVER.resolve_from_env(self._base_env())
        self.assertFalse(resolved.dry_run)
        self.assertFalse(resolved.validate_only)

    def test_env_resolution_rejects_missing_workflow(self) -> None:
        env = self._base_env()
        env.pop("AGENTIC_WORKFLOW")
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_from_env(env)

    def test_env_resolution_rejects_missing_profile(self) -> None:
        env = self._base_env()
        env.pop("AGENTIC_CONFIGURATION_PROFILE")
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_from_env(env)

    def test_env_resolution_passes_legacy_through(self) -> None:
        env = self._base_env()
        env["AGENTIC_LEGACY_CUSTOM_AGENT_FILE"] = ".opencode/agents/example-agent.md"
        resolved = RESOLVER.resolve_from_env(env)
        self.assertEqual(
            resolved.legacy["AGENTIC_LEGACY_CUSTOM_AGENT_FILE"],
            ".opencode/agents/example-agent.md",
        )


class OutputWriterTests(unittest.TestCase):
    """Verify $GITHUB_OUTPUT and job summary writers."""

    def test_write_github_outputs_round_trips(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            focus="documentation",
            max_comments=8,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "outputs.txt"
            RESOLVER.write_github_outputs(resolved, output_path)
            content = output_path.read_text(encoding="utf-8")
        self.assertIn("workflow=pr-documentation-review", content)
        self.assertIn("target_number=42", content)
        self.assertIn("focus=documentation", content)
        self.assertIn("max_comments=8", content)
        self.assertIn("dry_run=false", content)

    def test_write_job_summary_contains_redacted_metadata(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            dry_run=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.md"
            RESOLVER.write_job_summary(resolved, summary_path)
            content = summary_path.read_text(encoding="utf-8")
        self.assertIn("documentation-review", content)
        self.assertIn("dry-run", content)
        self.assertIn("42", content)


class CliTests(unittest.TestCase):
    """Verify the CLI entry point surfaces validation errors cleanly."""

    def test_cli_returns_zero_on_valid_inputs(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--configuration-profile",
                "documentation-review",
                "--target-number",
                "42",
            ]
        )
        self.assertEqual(rc, 0)

    def test_cli_returns_one_on_invalid_inputs(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--configuration-profile",
                "Bad Profile",
                "--target-number",
                "42",
            ]
        )
        self.assertEqual(rc, 1)

    def test_cli_writes_result_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            rc = RESOLVER.main(
                [
                    "--workflow",
                    "issue-feedback",
                    "--configuration-profile",
                    "issue-triage",
                    "--result",
                    str(result_path),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["workflow"], "issue-feedback")


if __name__ == "__main__":
    unittest.main()
