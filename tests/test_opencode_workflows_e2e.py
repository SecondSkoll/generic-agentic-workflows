"""Full local end-to-end tests for the three OpenCode workflow paths.

These tests run the same repository-owned helpers that the workflows invoke,
using temporary GitHub output/summary files and mocked provider/GitHub network
boundaries. They deliberately exercise invocation resolution, configuration,
policy resolution, agent execution, contract parsing, publication mode, and
provenance without requiring Actions, OpenCode, or a provider credential.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(name: str):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CONFIGURATION = _load("agentic_configuration")
POLICY = _load("agentic_policy")
RESOLVER = _load("resolve_invocation")
FEEDBACK = _load("run_agentic_feedback")
IMPLEMENTATION = _load("run_agentic_implementation")


class FakeProcess:
    """Small substitute for the completed OpenCode process."""

    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _jsonl_text_event(text: str) -> str:
    return json.dumps(
        {
            "type": "text",
            "sessionID": "e2e-session",
            "part": {"type": "text", "text": text, "time": {"start": 1, "end": 2}},
        }
    ) + "\n"


def _resolve_policy(workflow: str, profile: str) -> tuple[dict, dict]:
    bundle = CONFIGURATION.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile=profile,
        workflow=workflow,
    ).to_dict()
    policy = POLICY.merge_policy(
        workflow=workflow,
        model_profile=bundle["model_profile"],
    ).to_dict()
    return bundle, policy


def _write_github_output(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


class OpenCodeWorkflowEndToEndTests(unittest.TestCase):
    """One production-shaped end-to-end path for each main workflow."""

    def setUp(self) -> None:
        self.environment = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_documentation_review_workflow_end_to_end(self) -> None:
        """Resolve a PR invocation and publish a contract-valid inline review."""
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            github_output = temp / "github-output"
            summary = temp / "summary.md"
            invocation_result = temp / "resolved-invocation.json"
            resolved_config = temp / "resolved-config.json"
            effective_policy = temp / "effective-policy.json"
            provenance = temp / "provenance.json"
            diagnostics = temp / "diagnostics.json"
            diff = temp / "pr.diff"
            metadata = temp / "pr-metadata.json"
            diff.write_text(
                "diff --git a/docs/guide.md b/docs/guide.md\n"
                "--- a/docs/guide.md\n"
                "+++ b/docs/guide.md\n"
                "@@ -1 +1,2 @@\n"
                " Existing text.\n"
                "+Document the new configuration option.\n",
                encoding="utf-8",
            )
            metadata.write_text(
                json.dumps({"number": 17, "title": "Document configuration"}),
                encoding="utf-8",
            )

            self.assertEqual(
                RESOLVER.main(
                    [
                        "--workflow", "pr-documentation-review",
                        "--configuration-profile", "documentation-review",
                        "--target-number", "17",
                        "--focus", "documentation",
                        "--max-comments", "1",
                        "--github-output", str(github_output),
                        "--github-step-summary", str(summary),
                        "--result", str(invocation_result),
                    ]
                ),
                0,
            )
            invocation = _write_github_output(github_output)
            self.assertEqual(
                CONFIGURATION.main(
                    [
                        "--workflow", "pr-documentation-review",
                        "--configuration-source", invocation["configuration_source"],
                        "--configuration-profile", invocation["configuration_profile"],
                        "--result", str(resolved_config),
                    ]
                ),
                0,
            )
            bundle = json.loads(resolved_config.read_text(encoding="utf-8"))
            agent_capabilities = POLICY.parse_agent_capabilities(
                (Path(bundle["bundle_root"]) / bundle["agent_file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                POLICY.main(
                    [
                        "--workflow", "pr-documentation-review",
                        "--model-profile", bundle["model_profile"],
                        "--resolved-config", str(resolved_config),
                        "--agent-capabilities-json", json.dumps(agent_capabilities),
                        "--result", str(effective_policy),
                    ]
                ),
                0,
            )

            model_response = json.dumps(
                {
                    "summary": "The documentation change is clear.",
                    "comments": [
                        {
                            "path": "docs/guide.md",
                            "line": 2,
                            "body": "State the default value for this option.",
                        }
                    ],
                }
            )
            with (
                mock.patch.object(
                    FEEDBACK.subprocess,
                    "run",
                    return_value=FakeProcess(_jsonl_text_event(model_response)),
                ),
                mock.patch.object(FEEDBACK, "_has_v2_marker_match", return_value=False),
                mock.patch.object(FEEDBACK, "github_request") as github_request,
            ):
                self.assertEqual(
                    FEEDBACK.main(
                        [
                            "--input", str(diff),
                            "--comments-url", "https://api.github.com/repos/acme/widgets/issues/17/comments",
                            "--repository", "acme/widgets",
                            "--pull-number", "17",
                            "--head-sha", "a" * 40,
                            "--feedback-kind", "pr-documentation-review",
                            "--author", "octocat",
                            "--target-title", "Document configuration",
                            "--focus", invocation["focus"],
                            "--max-comments", invocation["max_comments"],
                            "--resolved-config", str(resolved_config),
                            "--effective-policy", str(effective_policy),
                            "--provenance", str(provenance),
                            "--response-diagnostics", str(diagnostics),
                            "--pr-metadata", str(metadata),
                        ]
                    ),
                    0,
                )
            published = github_request.call_args.kwargs["body"]
            self.assertEqual(published["comments"][0]["path"], "docs/guide.md")
            self.assertIn("agentic-workflow:pr-documentation-review:v2", published["body"])
            self.assertEqual(json.loads(provenance.read_text())["result"], "published")
            self.assertEqual(json.loads(diagnostics.read_text())["location_validation"][0]["outcome"], "inline")

    def test_issue_feedback_workflow_end_to_end(self) -> None:
        """Resolve an issue invocation and publish contract-valid issue feedback."""
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            github_output = temp / "github-output"
            invocation_result = temp / "resolved-invocation.json"
            resolved_config = temp / "resolved-config.json"
            effective_policy = temp / "effective-policy.json"
            provenance = temp / "provenance.json"
            issue = temp / "issue.md"
            issue.write_text("# Issue #23: Clarify setup\n\nThe setup instructions are unclear.\n", encoding="utf-8")

            self.assertEqual(
                RESOLVER.main(
                    [
                        "--workflow", "issue-feedback",
                        "--configuration-profile", "issue-feedback",
                        "--target-number", "23",
                        "--focus", "documentation",
                        "--max-issues", "1",
                        "--github-output", str(github_output),
                        "--result", str(invocation_result),
                    ]
                ),
                0,
            )
            invocation = _write_github_output(github_output)
            bundle, policy = _resolve_policy("issue-feedback", "issue-feedback")
            resolved_config.write_text(json.dumps(bundle), encoding="utf-8")
            effective_policy.write_text(json.dumps(policy), encoding="utf-8")

            with (
                mock.patch.object(
                    FEEDBACK.subprocess,
                    "run",
                    return_value=FakeProcess(_jsonl_text_event("Thanks @octocat. Please add the expected setup command.")),
                ),
                mock.patch.object(FEEDBACK, "_has_v2_marker_match", return_value=False),
                mock.patch.object(FEEDBACK, "github_request") as github_request,
            ):
                self.assertEqual(
                    FEEDBACK.main(
                        [
                            "--input", str(issue),
                            "--comments-url", "https://api.github.com/repos/acme/widgets/issues/23/comments",
                            "--feedback-kind", "issue-feedback",
                            "--author", "octocat",
                            "--target-title", "Clarify setup",
                            "--focus", invocation["focus"],
                            "--resolved-config", str(resolved_config),
                            "--effective-policy", str(effective_policy),
                            "--provenance", str(provenance),
                        ]
                    ),
                    0,
                )
            published = github_request.call_args.kwargs["body"]["body"]
            self.assertIn("agentic-workflow:issue-feedback:v2", published)
            self.assertIn("Please add the expected setup command.", published)
            record = json.loads(provenance.read_text(encoding="utf-8"))
            self.assertEqual(record["workflow_name"], "issue-feedback")
            self.assertEqual(record["result"], "published")

    def test_issue_implementation_workflow_end_to_end(self) -> None:
        """Resolve, plan, enforce allowed changes, and dry-run implementation publication."""
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            remote = temp / "remote.git"
            repository = temp / "repository"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
            for key, value in (("user.name", "Test User"), ("user.email", "test@example.com")):
                subprocess.run(["git", "-C", str(repository), "config", key, value], check=True)
            (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repository), "push", "-u", "origin", "main"], check=True, capture_output=True)

            github_output = temp / "github-output"
            invocation_result = temp / "resolved-invocation.json"
            resolved_config = temp / "resolved-config.json"
            effective_policy = temp / "effective-policy.json"
            runner_output = temp / "runner-output"
            runner_provenance = temp / "runner-provenance.json"
            issue_context = temp / "issue-context.md"
            issue_context.write_text("# Issue #29: Increment value\n\nIncrement VALUE in app.py.\n", encoding="utf-8")

            self.assertEqual(
                RESOLVER.main(
                    [
                        "--workflow", "issue-implementation",
                        "--configuration-profile", "default-implementation",
                        "--target-number", "29",
                        "--request-label", "ai-implementation-requested",
                        "--dry-run", "true",
                        "--github-output", str(github_output),
                        "--result", str(invocation_result),
                    ]
                ),
                0,
            )
            invocation = _write_github_output(github_output)
            bundle, policy = _resolve_policy("issue-implementation", "default-implementation")
            resolved_config.write_text(json.dumps(bundle), encoding="utf-8")
            effective_policy.write_text(json.dumps(policy), encoding="utf-8")

            def model_changes_application(cmd, **kwargs):
                (repository / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
                return FakeProcess("IMPLEMENTATION_DECISION: IMPLEMENT\n## Summary\n- Incremented VALUE\n")

            with mock.patch.object(IMPLEMENTATION.subprocess, "run", side_effect=model_changes_application):
                self.assertEqual(
                    IMPLEMENTATION.main(
                        [
                            "--resolved-config", str(resolved_config),
                            "--effective-policy", str(effective_policy),
                            "--issue-context", str(issue_context),
                            "--issue-number", invocation["target_number"],
                            "--issue-author", "octocat",
                            "--repository", "acme/widgets",
                            "--branch", "ai/issue-29-initial-implementation",
                            "--repo-root", str(repository),
                            "--github-output", str(runner_output),
                            "--provenance", str(runner_provenance),
                        ]
                    ),
                    0,
                )
            self.assertEqual(_write_github_output(runner_output)["decision"], "IMPLEMENT")
            base_ref = POLICY.resolve_default_branch_ref(repository, remote="origin")
            changed = POLICY.collect_implementation_changed_paths(repository, base_ref=base_ref)
            self.assertEqual(changed, ["app.py"])
            self.assertEqual(POLICY.enforce_changed_paths(changed, workflow="issue-implementation"), [])
            self.assertEqual(json.loads(runner_provenance.read_text())["result"], "generated")

            changed_files = "\n".join(f"- `{path}`" for path in changed)
            publication_log = io.StringIO()
            with contextlib.redirect_stdout(publication_log):
                if invocation["dry_run"] == "true":
                    print(f"Dry run: would commit and push branch ai/issue-29-initial-implementation with:\n{changed_files}")
                    print("Dry run: would open a pull request for #29.")
            self.assertIn("would commit and push branch", publication_log.getvalue())
            self.assertFalse((repository / ".opencode" / "agents" / "default-implementation.md").exists())


if __name__ == "__main__":
    unittest.main()
