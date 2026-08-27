"""Static verification for reusable workflows and consumer examples.

These assertions are intentionally run both in the full Python suite and by
``verify-reusable-workflows.yml`` so workflow wiring cannot drift from the
locally runnable test coverage.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from urllib.request import Request, urlopen
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[1]
CENTRAL_REF = "9b35f1fc2860a1d6b8f1abaa9b467dc4eb42aec8"
WORKFLOW_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REUSABLE_WORKFLOW_PREFIX = "SecondSkoll/generic-agentic-workflows/.github/workflows/"
REMOTE_HELPER_WORKFLOWS = (
    "opencode-documentation-review.yml",
    "opencode-issue-feedback.yml",
    "opencode-release-project-review.yml",
)

WORKFLOWS = (
    "opencode-documentation-review.yml",
    "opencode-issue-feedback.yml",
    "opencode-issue-implementation.yml",
    "opencode-release-project-review.yml",
    "verify-reusable-workflows.yml",
)

EXAMPLES = {
    "documentation-review.yml": (
        "review",
        "opencode-documentation-review.yml",
        "documentation-review",
    ),
    "issue-feedback.yml": (
        "feedback",
        "opencode-issue-feedback.yml",
        "issue-feedback",
    ),
    "release-project-review-self.yml": (
        "release-review",
        "opencode-release-project-review.yml",
        "release-project-review",
    ),
    "external-release-project-review.yml": (
        "release-review",
        "opencode-release-project-review.yml",
        "release-project-review",
    ),
}


class ReusableWorkflowVerificationTests(unittest.TestCase):
    def test_reusable_workflow_yaml_parses(self) -> None:
        """Every repository workflow remains valid YAML."""
        workflows_root = REPO_ROOT / ".github/workflows"
        for filename in WORKFLOWS:
            with self.subTest(filename=filename):
                data = yaml.safe_load((workflows_root / filename).read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)

    def test_consumer_examples_have_expected_reusable_wiring(self) -> None:
        """Examples retain SHA-pinned, validate-only configuration wiring."""
        examples_root = REPO_ROOT / "docs/how-to/examples/configuration-sources"
        for source in ("default", "local", "central"):
            for filename, (job_name, reusable_workflow, profile) in EXAMPLES.items():
                with self.subTest(source=source, filename=filename):
                    path = examples_root / source / ".github/workflows" / filename
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    job = data["jobs"][job_name]
                    inputs = job["with"]

                    self.assertEqual(inputs["configuration_source"], source)
                    self.assertIs(inputs["validate_only"], True)
                    prefix = f"{REUSABLE_WORKFLOW_PREFIX}{reusable_workflow}@"
                    self.assertTrue(job["uses"].startswith(prefix))
                    workflow_sha = job["uses"].removeprefix(prefix)
                    self.assertRegex(workflow_sha, WORKFLOW_SHA_RE)

                    if source == "local":
                        self.assertNotIn("configuration_ref", inputs)
                        self.assertEqual(inputs["configuration_profile"], f"local-{profile}")
                        continue

                    self.assertEqual(inputs["configuration_profile"], profile)
                    configuration_ref = inputs["configuration_ref"]
                    self.assertRegex(configuration_ref, WORKFLOW_SHA_RE)
                    if source == "default":
                        self.assertEqual(configuration_ref, workflow_sha)
                    else:
                        self.assertEqual(configuration_ref, CENTRAL_REF)

    def test_reusable_workflows_checkout_and_use_pinned_helpers(self) -> None:
        """Called workflows execute helpers from their own immutable revision."""
        workflows_root = REPO_ROOT / ".github/workflows"
        for filename in REMOTE_HELPER_WORKFLOWS:
            with self.subTest(filename=filename):
                text = (workflows_root / filename).read_text(encoding="utf-8")
                self.assertIn("repository: ${{ job.workflow_repository }}", text)
                self.assertIn("ref: ${{ job.workflow_sha }}", text)
                self.assertIn("path: .agentic-workflow", text)
                self.assertIn(
                    "if: job.workflow_repository != github.repository || job.workflow_sha != github.sha",
                    text,
                )
                self.assertIn("AGENTIC_WORKFLOW_ROOT", text)
                self.assertNotRegex(text, r"python3 scripts/(?:resolve_|agentic_|run_)")
                self.assertNotRegex(
                    text,
                    r'spec_from_file_location\([^\n]*,\s*"scripts/agentic_',
                )

    def test_documentation_review_fetches_private_pr_heads_with_job_token(self) -> None:
        """The non-persisted checkout token is restored only for the PR fetch."""
        text = (
            REPO_ROOT / ".github/workflows/opencode-documentation-review.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertIn("http.https://github.com/.extraheader=$auth_header", text)
        self.assertIn('fetch --no-tags origin "pull/$PR_NUMBER/head:pr-head"', text)
        merge_base_cmd = 'git merge-base "$BASE_SHA" pr-head'
        diff_cmd = 'git diff "$merge_base" pr-head -- > pr.diff'
        self.assertIn(merge_base_cmd, text)
        self.assertIn(diff_cmd, text)
        self.assertLess(
            text.index(merge_base_cmd),
            text.index(diff_cmd),
            "merge-base must be computed before the diff command",
        )

    def test_reusable_inputs_are_not_gated_by_event_name(self) -> None:
        """A called workflow honors ``with`` for every caller event type."""
        workflows_root = REPO_ROOT / ".github/workflows"
        for filename in REMOTE_HELPER_WORKFLOWS:
            with self.subTest(filename=filename):
                text = (workflows_root / filename).read_text(encoding="utf-8")
                self.assertNotRegex(
                    text,
                    r"github\.event_name\s*==\s*['\"]workflow_call['\"]\s*&&\s*inputs\.",
                )

    def test_release_dispatch_and_call_have_independent_source_defaults(self) -> None:
        """Direct release runs use local config while calls default remotely."""
        path = REPO_ROOT / ".github/workflows/opencode-release-project-review.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = data[True]
        self.assertEqual(
            triggers["workflow_dispatch"]["inputs"]["configuration_source"]["default"],
            "local",
        )
        self.assertEqual(
            triggers["workflow_call"]["inputs"]["configuration_source"]["default"],
            "default",
        )

    def test_central_examples_match_approved_policy_source(self) -> None:
        """The central-source pin agrees with the checked-in organization policy."""
        policy = json.loads(
            (REPO_ROOT / ".opencode/policy/organization-policy.json").read_text(encoding="utf-8")
        )
        central = policy["remote_source_allowlist"]["central"]
        self.assertEqual(central["repository"], "SecondSkoll/generic-agentic-workflows-config")
        self.assertEqual(central["root"], ".opencode/configuration")
        self.assertRegex(CENTRAL_REF, WORKFLOW_SHA_RE)

    def test_checked_in_configuration_json_parses(self) -> None:
        """Every checked-in primary configuration JSON file is parseable."""
        configuration_root = REPO_ROOT / ".opencode/configuration"
        paths = list(configuration_root.rglob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.relative_to(REPO_ROOT)):
                json.loads(path.read_text(encoding="utf-8"))

    def test_central_pin_is_reachable_from_main(self) -> None:
        """The central configuration pin remains reachable from its main branch.

        This network integration assertion is exercised by the dedicated
        workflow. It is skipped for ordinary local and pull-request test runs
        that have not explicitly provided a GitHub token.
        """
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            self.skipTest("GITHUB_TOKEN is required for central-pin ancestry verification")
        policy = json.loads(
            (REPO_ROOT / ".opencode/policy/organization-policy.json").read_text(encoding="utf-8")
        )
        repository = policy["remote_source_allowlist"]["central"]["repository"]
        request = Request(
            f"https://api.github.com/repos/{repository}/compare/main...{CENTRAL_REF}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request) as response:
            comparison = json.load(response)
        self.assertIn(
            comparison["status"],
            {"behind", "identical"},
            f"{CENTRAL_REF} is not reachable from {repository}'s main branch",
        )


if __name__ == "__main__":
    unittest.main()
