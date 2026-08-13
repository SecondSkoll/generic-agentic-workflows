"""Round-2 revision tests: real OpenCode smoke, collision-safe staging, and
profile max_comments default application."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CFG = _load("agentic_configuration")
POLICY = _load("agentic_policy")
PROMPTS = _load("agentic_prompts")


def _resolved(profile: str, workflow: str) -> dict:
    return CFG.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile=profile,
        workflow=workflow,
    ).to_dict()


class RealOpenCodeSmokeTests(unittest.TestCase):
    """Required change #1, #8: real OpenCode schema/loadability smoke check.

    Stages each bundle's verified agents/skills into an isolated workspace and
    runs ``opencode debug config`` (a non-network config/schema load). Skips
    cleanly only when the binary is absent locally.
    """

    BUNDLES = [
        ("documentation-review", "pr-documentation-review"),
        ("issue-feedback", "issue-feedback"),
        ("default-implementation", "issue-implementation"),
    ]

    def test_all_bundle_agents_load_in_real_opencode(self) -> None:
        if shutil.which("opencode") is None:
            self.skipTest("opencode binary not available locally")
        for profile, workflow in self.BUNDLES:
            with self.subTest(profile=profile):
                data = _resolved(profile, workflow)
                with tempfile.TemporaryDirectory() as tmp:
                    staged = CFG.materialize_to_opencode_root(data, Path(tmp))
                    ok, message = CFG.opencode_config_smoke_check(Path(tmp))
                    self.assertTrue(ok, f"{profile}: {message}")

    def test_smoke_skips_when_binary_absent(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            ok, message = CFG.opencode_config_smoke_check(Path("/tmp"))
        self.assertTrue(ok)
        self.assertIn("skipped", message)

    def test_invented_permission_value_rejected_by_opencode(self) -> None:
        """Reproduce the original defect: invented permission values are
        rejected by real OpenCode config schema."""
        if shutil.which("opencode") is None:
            self.skipTest("opencode binary not available locally")
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / ".opencode" / "agents"
            agents.mkdir(parents=True)
            (agents / "bad.md").write_text(
                "---\nname: bad\nmode: primary\nmodel: openrouter/x\n"
                "permission:\n  github_write: review-only\n---\n# bad\n",
                encoding="utf-8",
            )
            ok, message = CFG.opencode_config_smoke_check(Path(tmp))
        self.assertFalse(ok)
        self.assertIn("review-only", message)


class CollisionSafeStagingTests(unittest.TestCase):
    """Required change #2, #8: staging over a tracked file backs up and
    restores it byte-for-byte; clean git status after cleanup."""

    def test_staging_over_existing_file_restores_bytes(self) -> None:
        data = _resolved("default-implementation", "issue-implementation")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Pre-create a tracked executor.md with distinct content.
            dest = root / ".opencode" / "agents" / "executor.md"
            dest.parent.mkdir(parents=True)
            original_bytes = (
                b"---\nname: executor\nmode: subagent\n---\n# original tracked\n"
            )
            dest.write_bytes(original_bytes)
            staged = CFG.materialize_to_opencode_root(data, root)
            # The staged entry for executor should record it pre-existed.
            executor_entry = next(e for e in staged if e.name == "executor")
            self.assertTrue(executor_entry.existed)
            self.assertIsNotNone(executor_entry.backup_path)
            # Staged content differs from original.
            self.assertNotEqual(dest.read_bytes(), original_bytes)
            # Cleanup restores the original bytes exactly.
            CFG.cleanup_staged(staged)
            self.assertEqual(dest.read_bytes(), original_bytes)

    def test_staging_into_repo_preserves_git_status(self) -> None:
        """Staging + cleanup over a git repo leaves the working tree clean."""
        data = _resolved("default-implementation", "issue-implementation")
        env = {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "PATH": os.environ["PATH"],
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(
                ["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "t"],
                check=True,
                env=env,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                check=True,
                env=env,
            )
            # Commit a tracked executor.md.
            agents = repo / ".opencode" / "agents"
            agents.mkdir(parents=True)
            tracked = agents / "executor.md"
            tracked.write_bytes(
                b"---\nname: executor\nmode: subagent\n---\n# tracked\n"
            )
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
                check=True,
                env=env,
            )
            # Stage + cleanup.
            staged = CFG.materialize_to_opencode_root(data, repo)
            CFG.cleanup_staged(staged)
            # git status should be clean (no diff).
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertEqual(
                status.stdout.strip(), "", f"git status not clean: {status.stdout!r}"
            )
            # The tracked executor.md bytes are unchanged.
            self.assertEqual(
                tracked.read_bytes(),
                b"---\nname: executor\nmode: subagent\n---\n# tracked\n",
            )


class ProfileMaxCommentsDefaultTests(unittest.TestCase):
    """Required change #5: profile max_comments applied when caller omits override."""

    def test_parser_clamps_to_profile_ceiling_without_override(self) -> None:
        # The documentation-review bundle limits max_comments to 10.
        # Without a caller override, the parser should clamp to 10.
        items = [{"path": "a.py", "line": 1, "body": f"c{i}"} for i in range(15)]
        output = json.dumps({"summary": "s", "comments": items})
        summary, comments = PROMPTS.parse_pr_review_output(
            output, {"a.py": {1}}, max_comments=10
        )
        self.assertEqual(len(comments), 10)

    def test_runner_applies_profile_max_when_caller_omits(self) -> None:
        """Integration: runner uses profile ceiling as effective_max_comments."""
        RUNNER = _load("run_agentic_feedback")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bundle_path = tmp_path / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    _resolved("documentation-review", "pr-documentation-review")
                ),
                encoding="utf-8",
            )
            policy_path = tmp_path / "policy.json"
            pol = POLICY.merge_policy(
                workflow="pr-documentation-review", model_profile="review-readonly"
            )
            policy_path.write_text(json.dumps(pol.to_dict()), encoding="utf-8")
            diff_path = tmp_path / "pr.diff"
            diff_path.write_text(
                "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+new\n",
                encoding="utf-8",
            )
            # 15 comments in output; caller passes no --max-comments.
            valid_output = json.dumps(
                {
                    "summary": "s",
                    "comments": [
                        {"path": "a.py", "line": 1, "body": f"c{i}"} for i in range(15)
                    ],
                }
            )
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                return type(
                    "R",
                    (),
                    {
                        "stdout": json.dumps(
                            {
                                "type": "text",
                                "part": {
                                    "type": "text",
                                    "text": valid_output,
                                    "time": {"start": 1, "end": 2},
                                },
                            }
                        )
                        + "\n",
                        "stderr": "",
                        "returncode": 0,
                    },
                )()

            with (
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}),
                mock.patch.object(RUNNER.subprocess, "run", side_effect=fake_run),
                mock.patch.object(RUNNER, "github_request") as mock_gh,
                mock.patch.object(RUNNER, "_has_v2_marker_match", return_value=False),
                mock.patch.object(RUNNER, "has_marker", return_value=False),
            ):
                rc = RUNNER.main(
                    [
                        "--input",
                        str(diff_path),
                        "--comments-url",
                        "https://api.github.com/repos/o/r/issues/1/comments",
                        "--repository",
                        "o/r",
                        "--pull-number",
                        "1",
                        "--head-sha",
                        "abc123",
                        "--feedback-kind",
                        "pr-documentation-review",
                        "--author",
                        "octocat",
                        "--resolved-config",
                        str(bundle_path),
                        "--effective-policy",
                        str(policy_path),
                    ]
                )
            self.assertEqual(rc, 0)
            body = mock_gh.call_args.kwargs["body"]
            # Clamped to profile ceiling of 10.
            self.assertEqual(len(body["comments"]), 10)


class BaseRefHardeningTests(unittest.TestCase):
    """Required change #4: base ref resolution hard-fails when unresolvable."""

    def test_collect_raises_on_unresolvable_base_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            env = {
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "PATH": os.environ["PATH"],
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            }
            subprocess.run(
                ["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "t"],
                check=True,
                env=env,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                check=True,
                env=env,
            )
            (repo / "x").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
                check=True,
                env=env,
            )
            with self.assertRaises(POLICY.PolicyError):
                POLICY.collect_implementation_changed_paths(
                    repo, base_ref="refs/remotes/origin/nonexistent"
                )


class OverlayWiringTests(unittest.TestCase):
    """Required change #3: overlay loaded and narrows policy."""

    def test_cli_loads_overlay_to_narrow_publication(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            overlay_path = Path(tmp) / "overlay.json"
            overlay_path.write_text(
                json.dumps(
                    {
                        "max_comments": 3,
                        "allowed_focus": ["documentation"],
                        "publication": {"allow": False},
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
                    "--overlay",
                    str(overlay_path),
                    "--result",
                    str(result_path),
                ]
            )
            self.assertEqual(rc, 0)
            policy = json.loads(result_path.read_text())
            self.assertFalse(policy["publication_allowed"])

    def test_cli_rejects_escalating_overlay(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            overlay_path = Path(tmp) / "overlay.json"
            overlay_path.write_text(
                json.dumps(
                    {
                        "capabilities": {"shell": "allow"},
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
                    "--overlay",
                    str(overlay_path),
                ]
            )
            self.assertEqual(rc, 1)


class AgentCapabilitiesFromFrontMatterTests(unittest.TestCase):
    """Required change #3: actual agent capabilities derived from front matter."""

    def test_parse_capabilities_from_review_agent(self) -> None:
        data = _resolved("documentation-review", "pr-documentation-review")
        agent_text = (Path(data["bundle_root"]) / data["agent_file"]).read_text(
            encoding="utf-8"
        )
        caps = POLICY.parse_agent_capabilities(agent_text)
        # Review agent has edit: deny, bash: deny -> filesystem/shell denied.
        self.assertEqual(caps.get("filesystem"), "deny")
        self.assertEqual(caps.get("shell"), "deny")

    def test_parse_capabilities_from_implementation_planner(self) -> None:
        data = _resolved("default-implementation", "issue-implementation")
        agent_text = (Path(data["bundle_root"]) / data["agent_file"]).read_text(
            encoding="utf-8"
        )
        caps = POLICY.parse_agent_capabilities(agent_text)
        # Planner has bash: allow -> shell requested.
        self.assertEqual(caps.get("shell"), "allow")
        # Planner has edit: deny -> filesystem denied.
        self.assertEqual(caps.get("filesystem"), "deny")
        # Network denied.
        self.assertEqual(caps.get("network"), "deny")


class BoundedReadTests(unittest.TestCase):
    """Required change #7: bounded reads abort before full allocation."""

    def test_read_bounded_rejects_oversized_without_full_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big"
            p.write_bytes(b"x" * (CFG.MAX_FILE_BYTES + 10))
            with self.assertRaises(CFG.ConfigurationError):
                CFG._read_bounded(p, max_bytes=CFG.MAX_FILE_BYTES, label="big")


class ImplementationBranchInPromptTests(unittest.TestCase):
    """Required change #7: branch included in implementation prompt context."""

    def test_branch_appears_in_composed_prompt(self) -> None:
        IMPL = _load("run_agentic_implementation")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bundle = _resolved("default-implementation", "issue-implementation")
            bundle_path = tmp_path / "bundle.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            pol = POLICY.merge_policy(
                workflow="issue-implementation", model_profile="implementation-planner"
            )
            policy_path = tmp_path / "policy.json"
            policy_path.write_text(json.dumps(pol.to_dict()), encoding="utf-8")
            ctx_path = tmp_path / "ctx.md"
            ctx_path.write_text("# Issue #1\nbody\n", encoding="utf-8")
            captured = {}

            def fake_run(cmd, **kwargs):
                # The composed prompt is deliberately passed through a file to
                # avoid exceeding argv limits with large issue context.
                prompt_path = Path(cmd[cmd.index("--file") + 1])
                captured["prompt"] = prompt_path.read_text(encoding="utf-8")
                return type(
                    "R",
                    (),
                    {
                        "stdout": "IMPLEMENTATION_DECISION: BLOCKED\nIMPLEMENTATION_BLOCKER: x",
                        "stderr": "",
                        "returncode": 0,
                    },
                )()

            with (
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}),
                mock.patch.object(IMPL.subprocess, "run", side_effect=fake_run),
            ):
                IMPL.main(
                    [
                        "--resolved-config",
                        str(bundle_path),
                        "--effective-policy",
                        str(policy_path),
                        "--issue-context",
                        str(ctx_path),
                        "--issue-number",
                        "1",
                        "--issue-author",
                        "octocat",
                        "--repository",
                        "o/r",
                        "--branch",
                        "ai/issue-1-impl",
                        "--repo-root",
                        str(tmp_path),
                    ]
                )
            self.assertIn("ai/issue-1-impl", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
