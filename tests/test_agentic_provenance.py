"""Tests for provenance, digest, and feedback markers (Plan 5)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "agentic_provenance.py"
SPEC = importlib.util.spec_from_file_location("agentic_provenance", SCRIPT_PATH)
assert SPEC and SPEC.loader
PROV = importlib.util.module_from_spec(SPEC)
sys.modules["agentic_provenance"] = PROV
SPEC.loader.exec_module(PROV)


def _bundle_dict():
    return {
        "source_alias": "central",
        "repository": "agentic-configuration/example",
        "resolved_sha": "a" * 40,
        "profile": "documentation-review",
        "manifest_sha256": "b" * 64,
    }


class ConfigurationDigestTests(unittest.TestCase):
    def test_digest_is_deterministic(self):
        a = PROV.configuration_digest(_bundle_dict())
        b = PROV.configuration_digest(_bundle_dict())
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_digest_changes_on_config_change(self):
        bundle = _bundle_dict()
        d1 = PROV.configuration_digest(bundle)
        bundle["profile"] = "different-profile"
        d2 = PROV.configuration_digest(bundle)
        self.assertNotEqual(d1, d2)


class ProvenanceRecordTests(unittest.TestCase):
    def test_complete_schema(self):
        record = PROV.build_provenance(
            workflow_version="v1.0",
            workflow_name="pr-documentation-review",
            caller_repository="owner/repo",
            target_kind="pull_request",
            target_number=42,
            target_head_sha="c" * 40,
            bundle=_bundle_dict(),
            prompt_template_sha256="d" * 64,
            output_contract="pr-review-json-v1",
            model_profile="review-readonly",
            effective_policy_sha256="e" * 64,
            mode="publish",
            result="published",
        )
        data = record.to_dict()
        for key in [
            "workflow_version",
            "workflow_name",
            "caller_repository",
            "target",
            "bundle",
            "prompt_template_sha256",
            "output_contract",
            "model_profile",
            "effective_policy_sha256",
            "mode",
            "result",
            "configuration_digest",
        ]:
            self.assertIn(key, data)
        self.assertEqual(data["target"]["number"], 42)
        self.assertEqual(data["bundle"]["resolved_sha"], "a" * 40)
        self.assertEqual(data["result"], "published")

    def test_invalid_mode_rejected(self):
        with self.assertRaises(PROV.ProvenanceError):
            PROV.build_provenance(
                workflow_version="v1",
                workflow_name="x",
                caller_repository="o/r",
                target_kind=None,
                target_number=None,
                target_head_sha=None,
                bundle=None,
                prompt_template_sha256=None,
                output_contract=None,
                model_profile=None,
                effective_policy_sha256=None,
                mode="invalid",
                result="validated",
            )

    def test_invalid_result_rejected(self):
        with self.assertRaises(PROV.ProvenanceError):
            PROV.build_provenance(
                workflow_version="v1",
                workflow_name="x",
                caller_repository="o/r",
                target_kind=None,
                target_number=None,
                target_head_sha=None,
                bundle=None,
                prompt_template_sha256=None,
                output_contract=None,
                model_profile=None,
                effective_policy_sha256=None,
                mode="publish",
                result="weird",
            )

    def test_record_omits_secrets_and_untrusted_bodies(self):
        record = PROV.build_provenance(
            workflow_version="v1",
            workflow_name="issue-feedback",
            caller_repository="o/r",
            target_kind="issue",
            target_number=1,
            target_head_sha=None,
            bundle=_bundle_dict(),
            prompt_template_sha256="d" * 64,
            output_contract="issue-feedback-markdown-v1",
            model_profile="issue-feedback-readonly",
            effective_policy_sha256="e" * 64,
            mode="publish",
            result="published",
        )
        text = record.to_json()
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("ghp_", text)
        # The full issue body must never appear in provenance.
        self.assertNotIn("ignore previous instructions", text)


class FailureRecordTests(unittest.TestCase):
    def test_failure_record_has_error_fields(self):
        record = PROV.failure_record(
            workflow_version="v1",
            workflow_name="pr-documentation-review",
            caller_repository="o/r",
            mode="publish",
            error=RuntimeError("bundle missing hashes.json"),
        )
        data = record.to_dict()
        self.assertEqual(data["result"], "failed")
        self.assertEqual(data["error"], "RuntimeError")
        self.assertIn("hashes.json", data["error_message"])
        self.assertIsNone(data["configuration_digest"])


class MarkerTests(unittest.TestCase):
    def test_v1_marker_back_compatible(self):
        marker = PROV.feedback_marker(
            "pr-documentation-review",
            head_sha="abc123",
            schema_version="v1",
        )
        self.assertEqual(
            marker,
            "<!-- agentic-workflow:pr-documentation-review:v1:abc123 -->",
        )

    def test_v2_marker_carries_digest(self):
        digest = "a" * 64
        marker = PROV.feedback_marker(
            "pr-documentation-review",
            head_sha="abc123",
            config_digest=digest,
        )
        self.assertIn(":v2:", marker)
        self.assertIn(digest, marker)
        self.assertIn("abc123", marker)

    def test_v2_marker_requires_digest(self):
        with self.assertRaises(PROV.ProvenanceError):
            PROV.feedback_marker("x", config_digest=None)

    def test_parse_v1_marker(self):
        marker = "<!-- agentic-workflow:issue-feedback:v1 -->"
        parsed = PROV.parse_marker(marker, feedback_kind="issue-feedback")
        self.assertEqual(parsed["version"], "v1")
        self.assertIsNone(parsed["config_digest"])

    def test_parse_v2_marker(self):
        digest = "b" * 64
        marker = PROV.feedback_marker(
            "issue-feedback", config_digest=digest, head_sha="deadbeef"
        )
        parsed = PROV.parse_marker(marker, feedback_kind="issue-feedback")
        self.assertEqual(parsed["version"], "v2")
        self.assertEqual(parsed["config_digest"], digest)
        self.assertEqual(parsed["head_sha"], "deadbeef")

    def test_v1_marker_does_not_match_v2_config(self):
        v1 = "<!-- agentic-workflow:issue-feedback:v1 -->"
        self.assertFalse(
            PROV.matches_current_config(
                v1,
                feedback_kind="issue-feedback",
                head_sha=None,
                config_digest="c" * 64,
            )
        )

    def test_v2_marker_matches_same_config(self):
        digest = "d" * 64
        marker = PROV.feedback_marker("issue-feedback", config_digest=digest)
        self.assertTrue(
            PROV.matches_current_config(
                marker,
                feedback_kind="issue-feedback",
                head_sha=None,
                config_digest=digest,
            )
        )

    def test_v2_marker_does_not_match_different_config(self):
        marker = PROV.feedback_marker("issue-feedback", config_digest="e" * 64)
        self.assertFalse(
            PROV.matches_current_config(
                marker,
                feedback_kind="issue-feedback",
                head_sha=None,
                config_digest="f" * 64,
            )
        )

    def test_v2_marker_head_sha_mismatch(self):
        digest = "a" * 64
        marker = PROV.feedback_marker("pr-documentation-review", config_digest=digest, head_sha="aaaaaaa")
        self.assertFalse(
            PROV.matches_current_config(
                marker,
                feedback_kind="pr-documentation-review",
                head_sha="bbbbbbb",
                config_digest=digest,
            )
        )


class JobSummaryTests(unittest.TestCase):
    def test_summary_contains_key_fields(self):
        record = PROV.build_provenance(
            workflow_version="v1",
            workflow_name="pr-documentation-review",
            caller_repository="o/r",
            target_kind="pull_request",
            target_number=42,
            target_head_sha="c" * 40,
            bundle=_bundle_dict(),
            prompt_template_sha256="d" * 64,
            output_contract="pr-review-json-v1",
            model_profile="review-readonly",
            effective_policy_sha256="e" * 64,
            mode="publish",
            result="published",
        )
        summary = PROV.job_summary(record)
        self.assertIn("pr-documentation-review", summary)
        self.assertIn("documentation-review", summary)
        self.assertIn("published", summary)
        self.assertNotIn("OPENROUTER_API_KEY", summary)


class CliTests(unittest.TestCase):
    def test_cli_returns_zero(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text(json.dumps(_bundle_dict()), encoding="utf-8")
            rc = PROV.main(
                [
                    "--workflow-name",
                    "pr-documentation-review",
                    "--caller-repository",
                    "o/r",
                    "--mode",
                    "publish",
                    "--result-status",
                    "published",
                    "--bundle-json",
                    str(bundle_path),
                    "--output-contract",
                    "pr-review-json-v1",
                    "--model-profile",
                    "review-readonly",
                ]
            )
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
