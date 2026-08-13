"""Tests for the configuration bundle resolver (Plan 2)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "agentic_configuration.py"
SPEC = importlib.util.spec_from_file_location("agentic_configuration", SCRIPT_PATH)
assert SPEC and SPEC.loader
CFG = importlib.util.module_from_spec(SPEC)
sys.modules["agentic_configuration"] = CFG
SPEC.loader.exec_module(CFG)

REPO_ROOT = Path(__file__).parents[1]


def _write_bundle(
    root: Path,
    profile: str = "documentation-review",
    *,
    agent_text: str | None = None,
    skill_text: str | None = None,
    prompt_text: str | None = None,
    manifest: dict | None = None,
    hashes: dict | None = None,
) -> Path:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    agent_text = agent_text or (
        "---\nname: documentation-review\nmode: primary\npermission:\n  edit: deny\n---\n# agent\n"
    )
    skill_text = skill_text or "---\nname: documentation\n---\n# skill\n"
    prompt_text = prompt_text or "# prompt\nReview the diff for {{feedback_kind}}.\n"
    (profile_dir / "agent.md").write_text(agent_text, encoding="utf-8")
    (profile_dir / "skills" / "documentation").mkdir(parents=True, exist_ok=True)
    (profile_dir / "skills" / "documentation" / "SKILL.md").write_text(
        skill_text, encoding="utf-8"
    )
    (profile_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (profile_dir / "prompts" / "review.md").write_text(prompt_text, encoding="utf-8")
    manifest = manifest or {
        "schema_version": 1,
        "profile_name": profile,
        "allowed_workflows": ["pr-documentation-review"],
        "agent_file": "agent.md",
        "skill_files": ["skills/documentation/SKILL.md"],
        "prompt_template": "prompts/review.md",
        "model_profile": "review-readonly",
        "output_contract": "pr-review-json-v1",
        "limits": {"max_comments": 10},
    }
    (profile_dir / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    if hashes is None:
        import hashlib

        hashes = {
            "agent.md": hashlib.sha256(agent_text.encode()).hexdigest(),
            "skills/documentation/SKILL.md": hashlib.sha256(
                skill_text.encode()
            ).hexdigest(),
            "prompts/review.md": hashlib.sha256(prompt_text.encode()).hexdigest(),
        }
    (profile_dir / "hashes.json").write_text(json.dumps(hashes), encoding="utf-8")
    return profile_dir


class FakeRemoteClient:
    """Fake GitHub transport that serves an in-memory bundle."""

    def __init__(self, files: dict[str, bytes], resolved_sha: str):
        self._files = files
        self._resolved_sha = resolved_sha
        self.fetch_calls: list[tuple[str, str]] = []

    def fetch_manifest(self, *, repository, root, profile, sha):
        self.fetch_calls.append(("manifest", f"{root}/{profile}/bundle.json"))
        return self._files[f"{root}/{profile}/bundle.json"], self._resolved_sha

    def fetch_content(self, *, repository, root, path, sha):
        self.fetch_calls.append(("content", path))
        return self._files[path]


class PathSafetyTests(unittest.TestCase):
    def test_normalize_rejects_absolute(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.normalize_bundle_path("/etc/passwd")

    def test_normalize_rejects_dotdot(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.normalize_bundle_path("../escape.md")

    def test_normalize_rejects_backslash(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.normalize_bundle_path("agent.md\\..\\escape")

    def test_normalize_rejects_control_chars(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.normalize_bundle_path("agent\x00.md")

    def test_normalize_accepts_nested(self):
        self.assertEqual(
            CFG.normalize_bundle_path("skills/foo/SKILL.md"), "skills/foo/SKILL.md"
        )


class LocalResolutionTests(unittest.TestCase):
    def test_valid_local_bundle_resolves(self):
        resolved = CFG.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="documentation-review",
            workflow="pr-documentation-review",
        )
        self.assertEqual(resolved.profile_name, "documentation-review")
        self.assertEqual(resolved.agent_name, "documentation-review")
        self.assertEqual(resolved.manifest.output_contract, "pr-review-json-v1")
        self.assertEqual(resolved.source_alias, "local")
        self.assertIsNone(resolved.resolved_sha)
        self.assertTrue(resolved.manifest.manifest_sha256)

    def test_issue_feedback_local_bundle_resolves(self):
        resolved = CFG.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="issue-feedback",
            workflow="issue-feedback",
        )
        self.assertEqual(
            resolved.manifest.output_contract, "issue-feedback-markdown-v1"
        )

    def test_implementation_local_bundle_resolves(self):
        resolved = CFG.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="default-implementation",
            workflow="issue-implementation",
        )
        self.assertEqual(
            resolved.manifest.output_contract, "issue-implementation-decision-v1"
        )

    def test_unknown_profile_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_local_bundle(
                bundle_root=REPO_ROOT / ".opencode" / "configuration",
                profile="nope-not-real",
                workflow="pr-documentation-review",
            )

    def test_workflow_mismatch_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_local_bundle(
                bundle_root=REPO_ROOT / ".opencode" / "configuration",
                profile="documentation-review",
                workflow="issue-feedback",
            )

    def test_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc-review").mkdir()
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="doc-review",
                    workflow="pr-documentation-review",
                )

    def test_malformed_schema_version_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "bad-schema",
                manifest={
                    "schema_version": 99,
                    "profile_name": "bad-schema",
                    "allowed_workflows": ["pr-documentation-review"],
                    "agent_file": "agent.md",
                    "skill_files": [],
                    "prompt_template": "prompts/review.md",
                    "model_profile": "review-readonly",
                    "output_contract": "pr-review-json-v1",
                    "limits": {},
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="bad-schema",
                    workflow="pr-documentation-review",
                )

    def test_traversal_in_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "traversal",
                manifest={
                    "schema_version": 1,
                    "profile_name": "traversal",
                    "allowed_workflows": ["pr-documentation-review"],
                    "agent_file": "../escape.md",
                    "skill_files": [],
                    "prompt_template": "prompts/review.md",
                    "model_profile": "review-readonly",
                    "output_contract": "pr-review-json-v1",
                    "limits": {},
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="traversal",
                    workflow="pr-documentation-review",
                )

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = _write_bundle(root, "symlink-bundle")
            outside = Path(tmp) / "outside.md"
            outside.write_text("---\nname: evil\n---\n", encoding="utf-8")
            # Replace agent.md with a symlink to outside.
            (profile_dir / "agent.md").unlink()
            (profile_dir / "agent.md").symlink_to(outside)
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="symlink-bundle",
                    workflow="pr-documentation-review",
                )

    def test_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "hashfail",
                hashes={
                    "agent.md": "0" * 64,
                    "skills/documentation/SKILL.md": "0" * 64,
                    "prompts/review.md": "0" * 64,
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="hashfail",
                    workflow="pr-documentation-review",
                )

    def test_missing_hash_for_declared_content_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "missing-hash",
                hashes={
                    "skills/documentation/SKILL.md": "x",
                    "prompts/review.md": "y",
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="missing-hash",
                    workflow="pr-documentation-review",
                )

    def test_undeclared_hash_content_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import hashlib

            agent_text = (
                "---\nname: documentation-review\nmode: primary\n---\n# agent\n"
            )
            skill_text = "---\nname: documentation\n---\n# skill\n"
            prompt_text = "# prompt\n"
            _write_bundle(
                root,
                "undeclared",
                agent_text=agent_text,
                skill_text=skill_text,
                prompt_text=prompt_text,
                hashes={
                    "agent.md": hashlib.sha256(agent_text.encode()).hexdigest(),
                    "skills/documentation/SKILL.md": hashlib.sha256(
                        skill_text.encode()
                    ).hexdigest(),
                    "prompts/review.md": hashlib.sha256(
                        prompt_text.encode()
                    ).hexdigest(),
                    "extra.md": hashlib.sha256(b"extra").hexdigest(),
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="undeclared",
                    workflow="pr-documentation-review",
                )

    def test_oversized_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = "x" * (CFG.MAX_FILE_BYTES + 1)
            _write_bundle(
                root,
                "oversized",
                agent_text=f"---\nname: oversized-agent\nmode: primary\n---\n{big}",
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="oversized",
                    workflow="pr-documentation-review",
                )

    def test_invalid_utf8_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "utf8fail"
            profile_dir.mkdir(parents=True)
            (profile_dir / "agent.md").write_bytes(b"---\nname: bad\n---\n\xff\xfe\n")
            (profile_dir / "skills").mkdir()
            (profile_dir / "skills" / "s").mkdir()
            (profile_dir / "skills" / "s" / "SKILL.md").write_text(
                "---\nname: s\n---\n", encoding="utf-8"
            )
            (profile_dir / "prompts").mkdir()
            (profile_dir / "prompts" / "review.md").write_text(
                "# p\n", encoding="utf-8"
            )
            (profile_dir / "bundle.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile_name": "utf8fail",
                        "allowed_workflows": ["pr-documentation-review"],
                        "agent_file": "agent.md",
                        "skill_files": ["skills/s/SKILL.md"],
                        "prompt_template": "prompts/review.md",
                        "model_profile": "review-readonly",
                        "output_contract": "pr-review-json-v1",
                        "limits": {},
                    }
                ),
                encoding="utf-8",
            )
            import hashlib

            (profile_dir / "hashes.json").write_text(
                json.dumps(
                    {
                        "agent.md": hashlib.sha256(
                            b"---\nname: bad\n---\n\xff\xfe\n"
                        ).hexdigest(),
                        "skills/s/SKILL.md": hashlib.sha256(
                            b"---\nname: s\n---\n"
                        ).hexdigest(),
                        "prompts/review.md": hashlib.sha256(b"# p\n").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="utf8fail",
                    workflow="pr-documentation-review",
                )

    def test_review_agent_requesting_edit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "edit-agent",
                agent_text=(
                    "---\nname: editing-agent\nmode: primary\npermission:\n  edit: allow\n---\n# agent\n"
                ),
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="edit-agent",
                    workflow="pr-documentation-review",
                )

    def test_profile_name_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle(
                root,
                "documentation-review",
                manifest={
                    "schema_version": 1,
                    "profile_name": "different-name",
                    "allowed_workflows": ["pr-documentation-review"],
                    "agent_file": "agent.md",
                    "skill_files": ["skills/documentation/SKILL.md"],
                    "prompt_template": "prompts/review.md",
                    "model_profile": "review-readonly",
                    "output_contract": "pr-review-json-v1",
                    "limits": {},
                },
            )
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(
                    bundle_root=root,
                    profile="documentation-review",
                    workflow="pr-documentation-review",
                )


class RemoteResolutionTests(unittest.TestCase):
    def _make_remote_files(
        self, profile: str = "documentation-review"
    ) -> tuple[dict[str, bytes], str]:
        import hashlib

        src_dir = REPO_ROOT / ".opencode" / "configuration" / profile
        files: dict[str, bytes] = {}
        manifest = json.loads((src_dir / "bundle.json").read_text())
        root = ".opencode/configuration"
        files[f"{root}/{profile}/bundle.json"] = (src_dir / "bundle.json").read_bytes()
        files[f"{root}/{profile}/hashes.json"] = (src_dir / "hashes.json").read_bytes()
        for rel in [
            manifest["agent_file"],
            *manifest["skill_files"],
            manifest["prompt_template"],
        ]:
            files[f"{root}/{profile}/{rel}"] = (src_dir / rel).read_bytes()
        sha = "a" * 40
        return files, sha

    def test_pinned_remote_resolves(self):
        files, sha = self._make_remote_files()
        client = FakeRemoteClient(files, sha)
        with tempfile.TemporaryDirectory() as tmp:
            resolved = CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref=sha,
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=client,
                cache_dir=Path(tmp),
            )
        self.assertEqual(resolved.source_alias, "central")
        self.assertEqual(resolved.resolved_sha, sha)
        self.assertEqual(resolved.manifest.output_contract, "pr-review-json-v1")

    def test_unknown_source_alias_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_remote_bundle(
                source_alias="evil",
                configuration_ref="a" * 40,
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=FakeRemoteClient({}, "a" * 40),
            )

    def test_mutable_ref_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref="main",
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=FakeRemoteClient({}, "a" * 40),
            )

    def test_short_sha_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref="abc123",
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=FakeRemoteClient({}, "a" * 40),
            )

    def test_hash_mismatch_remote_rejected(self):
        files, sha = self._make_remote_files()
        # Corrupt one content file.
        key = next(k for k in files if k.endswith("agent.md"))
        files[key] = b"---\nname: tampered\n---\ntampered content"
        client = FakeRemoteClient(files, sha)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_remote_bundle(
                    source_alias="central",
                    configuration_ref=sha,
                    profile="documentation-review",
                    workflow="pr-documentation-review",
                    client=client,
                    cache_dir=Path(tmp),
                )

    def test_remote_sha_mismatch_rejected(self):
        files, _ = self._make_remote_files()
        requested = "b" * 40
        returned = "a" * 40
        client = FakeRemoteClient(files, returned)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_remote_bundle(
                    source_alias="central",
                    configuration_ref=requested,
                    profile="documentation-review",
                    workflow="pr-documentation-review",
                    client=client,
                    cache_dir=Path(tmp),
                )

    def test_remote_failure_no_fallback(self):
        class FailingClient:
            def fetch_manifest(self, **kwargs):
                raise CFG.ConfigurationError("remote unavailable")

            def fetch_content(self, **kwargs):
                raise CFG.ConfigurationError("remote unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_remote_bundle(
                    source_alias="central",
                    configuration_ref="a" * 40,
                    profile="documentation-review",
                    workflow="pr-documentation-review",
                    client=FailingClient(),
                    cache_dir=Path(tmp),
                )

    def test_remote_cache_reused_after_first_fetch(self):
        files, sha = self._make_remote_files()
        client = FakeRemoteClient(files, sha)
        with tempfile.TemporaryDirectory() as tmp:
            first = CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref=sha,
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=client,
                cache_dir=Path(tmp),
            )
            before = len(client.fetch_calls)
            second = CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref=sha,
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=client,
                cache_dir=Path(tmp),
            )
            self.assertEqual(
                first.manifest.manifest_sha256, second.manifest.manifest_sha256
            )
            self.assertEqual(
                len(client.fetch_calls), before
            )  # cache hit: no new fetches


class LegacyCompatTests(unittest.TestCase):
    def test_legacy_bundle_builds_with_warning(self):
        agent = REPO_ROOT / ".opencode" / "agents" / "default-agent.md"
        skill = REPO_ROOT / ".opencode" / "skills" / "basic-review" / "SKILL.md"
        resolved = CFG.resolve_legacy_bundle(
            agent_file=str(agent.relative_to(REPO_ROOT)),
            skill_file=str(skill.relative_to(REPO_ROOT)),
            workflow="pr-documentation-review",
            repo_root=REPO_ROOT,
            output_contract="pr-review-json-v1",
        )
        self.assertEqual(resolved.profile_name, "legacy")
        self.assertEqual(resolved.agent_name, "default-agent")
        self.assertEqual(resolved.manifest.output_contract, "pr-review-json-v1")

    def test_legacy_traversal_rejected(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.resolve_legacy_bundle(
                agent_file="../etc/passwd",
                skill_file=None,
                workflow="pr-documentation-review",
                repo_root=REPO_ROOT,
                output_contract="pr-review-json-v1",
            )


class FailureRecordTests(unittest.TestCase):
    def test_failure_record_redacted(self):
        err = CFG.ConfigurationError("bundle missing hashes.json")
        record = CFG.redacted_failure_record(
            workflow="pr-documentation-review",
            source_alias="central",
            profile="documentation-review",
            configuration_ref="a" * 40,
            error=err,
        )
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["source_alias"], "central")
        self.assertEqual(record["error"], "ConfigurationError")
        # No secret-shaped values appear.
        self.assertNotIn("token", record["message"].lower())


class CliTests(unittest.TestCase):
    def test_cli_local_resolution_returns_zero(self):
        rc = CFG.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--bundle-root",
                str(REPO_ROOT / ".opencode" / "configuration"),
                "--configuration-profile",
                "documentation-review",
            ]
        )
        self.assertEqual(rc, 0)

    def test_cli_rejects_invalid_profile(self):
        rc = CFG.main(
            [
                "--workflow",
                "pr-documentation-review",
                "--bundle-root",
                str(REPO_ROOT / ".opencode" / "configuration"),
                "--configuration-profile",
                "Bad Profile",
            ]
        )
        self.assertEqual(rc, 1)


class RemoteTransportTests(unittest.TestCase):
    """Required change #7: authenticated GitHub Contents API client."""

    def test_client_requires_token(self):
        with self.assertRaises(CFG.ConfigurationError):
            CFG.GitHubContentsClient(token="")

    def test_client_uses_contents_api_url(self):
        client = CFG.GitHubContentsClient(token="t")
        url = client._contents_url("o/r", "path/to/file.md", "a" * 40)
        self.assertIn("api.github.com/repos/o/r/contents/path/to/file.md", url)
        self.assertIn("ref=" + "a" * 40, url)

    def test_client_rejects_non_file_response(self):
        client = CFG.GitHubContentsClient(token="t", max_retries=1)
        payload = json.dumps({"type": "dir"}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [payload, b""]
        with mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(CFG.ConfigurationError):
                client._get(
                    "https://api.github.com/repos/o/r/contents/x?ref=" + "a" * 40
                )

    def test_client_decodes_base64_content(self):
        import base64

        client = CFG.GitHubContentsClient(token="t", max_retries=1)
        raw = b"hello world"
        payload = json.dumps(
            {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
            }
        ).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [payload, b""]
        with mock.patch("urllib.request.urlopen", return_value=response):
            data = client._get(
                "https://api.github.com/repos/o/r/contents/x?ref=" + "a" * 40
            )
        self.assertEqual(data, raw)


class SingleAliasSourceTests(unittest.TestCase):
    """Required change #7: single source of alias allowlist."""

    def test_resolve_invocation_mirrors_configuration_aliases(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "resolve_invocation", REPO_ROOT / "scripts" / "resolve_invocation.py"
        )
        ri = importlib.util.module_from_spec(spec)
        import sys

        sys.modules["resolve_invocation"] = ri
        spec.loader.exec_module(ri)
        expected = {"local"} | set(CFG.REMOTE_SOURCE_ALIASES.keys())
        self.assertEqual(ri.SUPPORTED_SOURCES, expected)


class CacheSlotRobustnessTests(unittest.TestCase):
    """Required change #9: cache slot robustness."""

    def test_partial_slot_removed_before_fetch(self):
        files, sha = self._make_remote_files()
        client = FakeRemoteClient(files, sha)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            # Create a partial/leftover slot.
            slot = cache / CFG._cache_key(
                "agentic-configuration_example-configuration",
                sha,
                "documentation-review",
            )
            slot.mkdir(parents=True)
            (slot / "junk").write_text("partial", encoding="utf-8")
            # Resolution must succeed by removing the leftover slot.
            resolved = CFG.resolve_remote_bundle(
                source_alias="central",
                configuration_ref=sha,
                profile="documentation-review",
                workflow="pr-documentation-review",
                client=client,
                cache_dir=cache,
            )
            self.assertEqual(resolved.resolved_sha, sha)

    def _make_remote_files(self, profile="documentation-review"):
        import hashlib

        src_dir = REPO_ROOT / ".opencode" / "configuration" / profile
        files = {}
        manifest = json.loads((src_dir / "bundle.json").read_text())
        root = ".opencode/configuration"
        files[f"{root}/{profile}/bundle.json"] = (src_dir / "bundle.json").read_bytes()
        files[f"{root}/{profile}/hashes.json"] = (src_dir / "hashes.json").read_bytes()
        declared = [manifest["agent_file"]]
        declared += manifest.get("additional_agent_files", [])
        declared += manifest.get("skill_files", [])
        declared.append(manifest["prompt_template"])
        for rel in declared:
            files[f"{root}/{profile}/{rel}"] = (src_dir / rel).read_bytes()
        return files, "a" * 40


if __name__ == "__main__":
    unittest.main()
