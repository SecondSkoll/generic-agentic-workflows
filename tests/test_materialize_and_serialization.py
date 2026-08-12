"""Tests for verified agent/skill materialization and resolved-config fields."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "agentic_configuration.py"
SPEC = importlib.util.spec_from_file_location("agentic_configuration", SCRIPT_PATH)
assert SPEC and SPEC.loader
CFG = importlib.util.module_from_spec(SPEC)
sys.modules["agentic_configuration"] = CFG
SPEC.loader.exec_module(CFG)

REPO_ROOT = Path(__file__).parents[1]


def _resolved_review_bundle() -> dict:
    resolved = CFG.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile="documentation-review",
        workflow="pr-documentation-review",
    )
    return resolved.to_dict()


def _resolved_implementation_bundle() -> dict:
    resolved = CFG.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile="default-implementation",
        workflow="issue-implementation",
    )
    return resolved.to_dict()


class ResolvedConfigSerializationTests(unittest.TestCase):
    """Required change #3: serialization carries template text + limits."""

    def test_to_dict_includes_prompt_template_text(self):
        data = _resolved_review_bundle()
        self.assertTrue(data["prompt_template_text"])
        self.assertIn("documentation", data["prompt_template_text"])

    def test_to_dict_includes_limits(self):
        data = _resolved_review_bundle()
        self.assertEqual(data["limits"], {"max_comments": 10})

    def test_to_dict_includes_bundle_policy(self):
        data = _resolved_review_bundle()
        self.assertIn("bundle_policy", data)

    def test_to_dict_includes_additional_agent_names(self):
        data = _resolved_implementation_bundle()
        self.assertEqual(data["additional_agent_names"], ["executor"])
        self.assertIn("executor.md", data["additional_agent_files"])

    def test_json_round_trip_preserves_template_and_limits(self):
        data = _resolved_review_bundle()
        text = json.dumps(data, sort_keys=True)
        restored = json.loads(text)
        self.assertTrue(restored["prompt_template_text"])
        self.assertEqual(restored["limits"], {"max_comments": 10})


class MaterializeToOpencodeRootTests(unittest.TestCase):
    """Required change #1: verified agent/skill delivery."""

    def test_materialize_stages_verified_agents_and_skills(self):
        data = _resolved_review_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = CFG.materialize_to_opencode_root(data, root)
            agent_path = root / ".opencode" / "agents" / "documentation-review.md"
            skill_path = root / ".opencode" / "skills" / "documentation" / "SKILL.md"
            self.assertTrue(agent_path.is_file())
            self.assertTrue(skill_path.is_file())
            # Re-verify staged hashes match content_hashes.
            import hashlib

            self.assertEqual(
                hashlib.sha256(agent_path.read_bytes()).hexdigest(),
                data["content_hashes"]["agent.md"],
            )
            self.assertEqual(
                hashlib.sha256(skill_path.read_bytes()).hexdigest(),
                data["content_hashes"]["skills/documentation/SKILL.md"],
            )

    def test_materialize_stages_additional_agents(self):
        data = _resolved_implementation_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = CFG.materialize_to_opencode_root(data, root)
            executor_path = root / ".opencode" / "agents" / "executor.md"
            planner_path = root / ".opencode" / "agents" / "default-implementation.md"
            self.assertTrue(executor_path.is_file())
            self.assertTrue(planner_path.is_file())

    def test_materialize_fails_closed_on_hash_mismatch(self):
        data = _resolved_review_bundle()
        # Tamper with the source file's recorded hash so re-verification fails.
        data["content_hashes"]["agent.md"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CFG.ConfigurationError):
                CFG.materialize_to_opencode_root(data, Path(tmp))

    def test_materialize_fails_if_bundle_root_missing(self):
        data = _resolved_review_bundle()
        data["bundle_root"] = "/nonexistent/path"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CFG.ConfigurationError):
                CFG.materialize_to_opencode_root(data, Path(tmp))

    def test_cleanup_staged_removes_files(self):
        data = _resolved_review_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = CFG.materialize_to_opencode_root(data, root)
            agent_path = root / ".opencode" / "agents" / "documentation-review.md"
            self.assertTrue(agent_path.is_file())
            CFG.cleanup_staged(staged)
            self.assertFalse(agent_path.exists())


class ImplementationBundleIncludesExecutorTests(unittest.TestCase):
    """Required change #1: executor agent included in manifest/hashes."""

    def test_executor_in_hashes_and_manifest(self):
        data = _resolved_implementation_bundle()
        self.assertIn("executor.md", data["content_hashes"])
        self.assertIn("executor.md", data["additional_agent_files"])


if __name__ == "__main__":
    unittest.main()
