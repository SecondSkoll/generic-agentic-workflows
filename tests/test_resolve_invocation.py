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

    def test_central_remote_alias_accepts_full_sha(self) -> None:
        sha = "b" * 40
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_source="central",
            configuration_ref=sha,
            configuration_profile="documentation-review",
            target_number=1,
        )
        self.assertEqual(resolved.configuration_source, "central")
        self.assertEqual(resolved.configuration_ref, sha)

    def test_central_remote_alias_requires_sha(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_source="central",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_central_remote_alias_rejects_mutable_ref(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_source="central",
                configuration_ref="main",
                configuration_profile="documentation-review",
                target_number=1,
            )

    def test_default_supplied_alias_accepts_full_sha(self) -> None:
        sha = "c" * 40
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_source="default",
            configuration_ref=sha,
            configuration_profile="documentation-review",
            target_number=1,
        )
        self.assertEqual(resolved.configuration_source, "default")
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

    VALIDATE_ONLY_CASES = (
        (
            "pr-documentation-review",
            "documentation-review",
            ["--target-number", "1"],
        ),
        ("issue-feedback", "issue-feedback", []),
        (
            "issue-implementation",
            "default-implementation",
            ["--target-number", "1"],
        ),
        (
            "release-project-review",
            "release-project-review",
            ["--target-repository", "SecondSkoll/generic-agentic-workflows", "--release-id", "1"],
        ),
        (
            "release-project-review",
            "release-project-review",
            [
                "--target-repository",
                "SecondSkoll/generic-agentic-workflows",
                "--release-tag",
                "v1.0.0",
            ],
        ),
    )

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

    def test_cli_validate_only_writes_expected_invocations(self) -> None:
        for workflow, profile, target_args in self.VALIDATE_ONLY_CASES:
            with self.subTest(workflow=workflow, target_args=target_args):
                with tempfile.TemporaryDirectory() as tmp:
                    result_path = Path(tmp) / "result.json"
                    rc = RESOLVER.main(
                        [
                            "--workflow",
                            workflow,
                            "--configuration-profile",
                            profile,
                            "--validate-only",
                            "true",
                            *target_args,
                            "--result",
                            str(result_path),
                        ]
                    )
                    self.assertEqual(rc, 0)
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["workflow"], workflow)
                self.assertEqual(payload["configuration_profile"], profile)
                self.assertTrue(payload["validate_only"])

    def test_cli_rejects_mutable_ref(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--configuration-profile",
                "documentation-review",
                "--configuration-ref",
                "main",
                "--target-number",
                "1",
            ]
        )
        self.assertEqual(rc, 1)

    def test_cli_rejects_traversal_profile(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--configuration-profile",
                "../escape",
                "--target-number",
                "1",
            ]
        )
        self.assertEqual(rc, 1)


class ReviewRequestStringTests(unittest.TestCase):
    """review_request_string validation, scoping, and wiring."""

    def test_pr_review_accepts_normal_string(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string="AI REVIEW REQUESTED",
        )
        self.assertEqual(resolved.review_request_string, "AI REVIEW REQUESTED")

    def test_pr_review_strips_surrounding_whitespace(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string="  AI REVIEW REQUESTED  ",
        )
        self.assertEqual(resolved.review_request_string, "AI REVIEW REQUESTED")

    def test_default_none_stays_none(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
        )
        self.assertIsNone(resolved.review_request_string)
        self.assertNotIn(
            "AI REVIEW REQUESTED", json.dumps(resolved.to_dict())
        )

    def test_empty_string_is_none(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string="",
        )
        self.assertIsNone(resolved.review_request_string)

    def test_whitespace_only_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=42,
                review_request_string="   ",
            )

    def test_control_characters_are_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=42,
                review_request_string="AI REVIEW\nREQUESTED",
            )
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=42,
                review_request_string="AI REVIEW\x7fREQUESTED",
            )

    def test_oversize_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=42,
                review_request_string="A" * 129,
            )

    def test_non_string_is_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_invocation(
                workflow="pr-documentation-review",
                configuration_profile="documentation-review",
                target_number=42,
                review_request_string=1337,
            )

    def test_max_length_boundary_is_accepted(self) -> None:
        value = "A" * 128
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string=value,
        )
        self.assertEqual(resolved.review_request_string, value)

    def test_rejected_for_other_workflows(self) -> None:
        cases = (
            {
                "workflow": "issue-feedback",
                "configuration_profile": "issue-feedback",
            },
            {
                "workflow": "issue-implementation",
                "configuration_profile": "default-implementation",
                "target_number": 7,
            },
            {
                "workflow": "release-project-review",
                "configuration_profile": "release-project-review",
                "target_repository": "a/b",
                "release_tag": "v1.0.0",
            },
            {
                "workflow": "pr-changelog-update",
                "configuration_profile": "changelog-update",
                "target_number": 12,
                "request_label": "update-changelog",
                "target_file": "CHANGELOG.md",
            },
        )
        for kwargs in cases:
            with self.subTest(workflow=kwargs["workflow"]):
                with self.assertRaises(RESOLVER.InvocationError):
                    RESOLVER.resolve_invocation(
                        review_request_string="AI REVIEW REQUESTED",
                        **kwargs,
                    )

    def test_env_round_trip_resolves_string(self) -> None:
        resolved = RESOLVER.resolve_from_env(
            {
                "AGENTIC_WORKFLOW": "pr-documentation-review",
                "AGENTIC_CONFIGURATION_PROFILE": "documentation-review",
                "AGENTIC_TARGET_NUMBER": "42",
                "AGENTIC_REVIEW_REQUEST_STRING": "REVIEW AGAIN PLEASE",
            }
        )
        self.assertEqual(resolved.review_request_string, "REVIEW AGAIN PLEASE")

    def test_env_round_trip_without_string_stays_none(self) -> None:
        resolved = RESOLVER.resolve_from_env(
            {
                "AGENTIC_WORKFLOW": "pr-documentation-review",
                "AGENTIC_CONFIGURATION_PROFILE": "documentation-review",
                "AGENTIC_TARGET_NUMBER": "42",
            }
        )
        self.assertIsNone(resolved.review_request_string)

    def test_env_rejects_invalid_string(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            RESOLVER.resolve_from_env(
                {
                    "AGENTIC_WORKFLOW": "pr-documentation-review",
                    "AGENTIC_CONFIGURATION_PROFILE": "documentation-review",
                    "AGENTIC_TARGET_NUMBER": "42",
                    "AGENTIC_REVIEW_REQUEST_STRING": "bad\nstring",
                }
            )

    def test_write_github_outputs_emits_review_request_string(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string="AI REVIEW REQUESTED",
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "outputs.txt"
            RESOLVER.write_github_outputs(resolved, output_path)
            content = output_path.read_text(encoding="utf-8")
        self.assertIn("review_request_string=AI REVIEW REQUESTED", content)

    def test_write_github_outputs_empty_when_unset(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "outputs.txt"
            RESOLVER.write_github_outputs(resolved, output_path)
            content = output_path.read_text(encoding="utf-8")
        self.assertIn("review_request_string=\n", content)

    def test_job_summary_lists_review_request_string_when_set(self) -> None:
        resolved = RESOLVER.resolve_invocation(
            workflow="pr-documentation-review",
            configuration_profile="documentation-review",
            target_number=42,
            review_request_string="REVIEW AGAIN",
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.md"
            RESOLVER.write_job_summary(resolved, summary_path)
            content = summary_path.read_text(encoding="utf-8")
        self.assertIn("- Review request string: `REVIEW AGAIN`", content)

    def test_cli_accepts_review_request_string_for_docs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            rc = RESOLVER.main(
                [
                    "--workflow",
                    "pr-documentation-review",
                    "--configuration-profile",
                    "documentation-review",
                    "--target-number",
                    "42",
                    "--review-request-string",
                    "REVIEW AGAIN",
                    "--result",
                    str(result_path),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["review_request_string"], "REVIEW AGAIN")

    def test_cli_rejects_review_request_string_for_other_workflows(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "issue-feedback",
                "--configuration-profile",
                "issue-feedback",
                "--review-request-string",
                "REVIEW AGAIN",
            ]
        )
        self.assertEqual(rc, 1)

    def test_cli_rejects_invalid_review_request_string(self) -> None:
        rc = RESOLVER.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--configuration-profile",
                "documentation-review",
                "--target-number",
                "42",
                "--review-request-string",
                "   ",
            ]
        )
        self.assertEqual(rc, 1)


class ChangelogUpdateInvocationTests(unittest.TestCase):
    """pr-changelog-update input validation and target_file constraints."""

    def _resolve(self, **overrides):
        kwargs = {
            "workflow": "pr-changelog-update",
            "configuration_profile": "changelog-update",
            "target_number": 12,
            "request_label": "update-changelog",
            "target_file": "CHANGELOG.md",
        }
        kwargs.update(overrides)
        return RESOLVER.resolve_invocation(**kwargs)

    def test_minimum_inputs_resolve(self) -> None:
        resolved = self._resolve()
        self.assertEqual(resolved.workflow, "pr-changelog-update")
        self.assertEqual(resolved.target_number, 12)
        self.assertEqual(resolved.request_label, "update-changelog")
        self.assertEqual(resolved.target_file, "CHANGELOG.md")

    def test_target_number_is_required(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_number=None)

    def test_request_label_is_required(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(request_label=None)

    def test_target_file_is_required(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file=None)

    def test_focus_max_comments_max_issues_rejected(self) -> None:
        for kwarg in ("focus", "max_comments", "max_issues"):
            with self.subTest(kwarg=kwarg):
                with self.assertRaises(RESOLVER.InvocationError):
                    self._resolve(**{kwarg: 1 if kwarg != "focus" else "security"})

    def test_release_fields_rejected(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(release_id=1)
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(release_tag="v1")
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_repository="a/b")

    def test_target_file_rejects_absolute(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="/etc/passwd")

    def test_target_file_rejects_backslash(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="docs\\CHANGELOG.md")

    def test_target_file_rejects_traversal(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="../CHANGELOG.md")
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="docs/../CHANGELOG.md")

    def test_target_file_rejects_dot_segments_and_trailing_slash(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="./CHANGELOG.md")
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="docs/")
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="")

    def test_target_file_rejects_github_and_opencode_first_segment(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file=".github/workflows/x.yml")
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file=".opencode/agents/x.md")

    def test_target_file_allows_nested_relative(self) -> None:
        resolved = self._resolve(target_file="docs/CHANGELOG.md")
        self.assertEqual(resolved.target_file, "docs/CHANGELOG.md")

    def test_target_file_rejects_control_characters(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="CHANGELOG.md\nmalicious")

    def test_target_file_rejects_oversize(self) -> None:
        with self.assertRaises(RESOLVER.InvocationError):
            self._resolve(target_file="a" * 600)

    def test_target_file_rejected_for_other_workflows(self) -> None:
        for workflow, profile in (
            ("pr-documentation-review", "documentation-review"),
            ("issue-feedback", "issue-feedback"),
            ("issue-implementation", "default-implementation"),
            ("release-project-review", "release-project-review"),
        ):
            with self.subTest(workflow=workflow):
                kwargs = {
                    "workflow": workflow,
                    "configuration_source": "local",
                    "configuration_profile": profile,
                    "target_file": "CHANGELOG.md",
                }
                if workflow in ("pr-documentation-review", "issue-implementation"):
                    kwargs["target_number"] = 1
                elif workflow == "release-project-review":
                    kwargs["target_repository"] = "a/b"
                    kwargs["release_tag"] = "v1.0"
                with self.assertRaises(RESOLVER.InvocationError):
                    RESOLVER.resolve_invocation(**kwargs)

    def test_env_resolves_target_file(self) -> None:
        resolved = RESOLVER.resolve_from_env(
            {
                "AGENTIC_WORKFLOW": "pr-changelog-update",
                "AGENTIC_CONFIGURATION_PROFILE": "changelog-update",
                "AGENTIC_TARGET_NUMBER": "5",
                "AGENTIC_REQUEST_LABEL": "update-changelog",
                "AGENTIC_TARGET_FILE": "docs/CHANGELOG.md",
            }
        )
        self.assertEqual(resolved.target_file, "docs/CHANGELOG.md")


if __name__ == "__main__":
    unittest.main()
