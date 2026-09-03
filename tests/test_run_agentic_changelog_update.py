"""Tests for the pr-changelog-update runner (guard and update subcommands)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = _load("run_agentic_changelog_update")
CFG = _load("agentic_configuration")
POLICY = _load("agentic_policy")

REPO_ROOT = Path(__file__).parents[1]
MARKER = RUNNER.MARKER


def _pr(*, fork: bool = False, state: str = "open", number: int = 12) -> dict:
    return {
        "number": number,
        "state": state,
        "title": "Add a feature",
        "body": "This adds a feature.",
        "user": {"login": "octocat"},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "owner/repo"}},
        "head": {
            "sha": "h" * 40,
            "ref": "feature",
            "repo": {"full_name": "fork/repo" if fork else "owner/repo", "fork": fork},
        },
    }


def _event(action: str = "labeled", label: str = "update-changelog") -> dict:
    return {"pull_request_target": {}, "action": action, "label": {"name": label}}


class GuardTests(unittest.TestCase):
    def test_proceed_for_matching_labeled_open_pr_same_repo(self):
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["mode"], "commit")
        self.assertFalse(result["is_fork"])

    def test_proceed_fork_uses_comment_mode(self):
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["mode"], "comment")
        self.assertTrue(result["is_fork"])

    def test_rejects_non_pull_request_event(self):
        with self.assertRaises(RUNNER.GuardError):
            RUNNER.run_guard(
                event_payload={"action": "labeled", "label": {"name": "update-changelog"}},
                pr=_pr(),
                request_label="update-changelog",
                target_number=12,
            )

    def test_rejects_unlabeled_action(self):
        with self.assertRaises(RUNNER.GuardError):
            RUNNER.run_guard(
                event_payload=_event(action="unlabeled"),
                pr=_pr(),
                request_label="update-changelog",
                target_number=12,
            )

    def test_rejects_mismatched_label(self):
        with self.assertRaises(RUNNER.GuardError):
            RUNNER.run_guard(
                event_payload=_event(label="other-label"),
                pr=_pr(),
                request_label="update-changelog",
                target_number=12,
            )

    def test_rejects_closed_pr(self):
        with self.assertRaises(RUNNER.GuardError):
            RUNNER.run_guard(
                event_payload=_event(),
                pr=_pr(state="closed"),
                request_label="update-changelog",
                target_number=12,
            )

    def test_rejects_wrong_target_number(self):
        with self.assertRaises(RUNNER.GuardError):
            RUNNER.run_guard(
                event_payload=_event(),
                pr=_pr(),
                request_label="update-changelog",
                target_number=99,
            )

    def test_same_repo_skips_when_marker_commit_is_most_recent(self):
        commits = [{"sha": "h" * 40, "commit": {"message": f"Update changelog\n\n{MARKER}", "committer": {"date": "2024-01-02T00:00:00Z"}}}]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=[],
            commits=commits,
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["prior_marker_commit_sha"], "h" * 40)

    def test_same_repo_proceeds_when_newer_comment_exists(self):
        commits = [{"sha": "h" * 40, "commit": {"message": f"Update changelog\n\n{MARKER}", "committer": {"date": "2024-01-02T00:00:00Z"}}}]
        comments = [{"id": 1, "body": "later review comment", "updated_at": "2024-01-03T00:00:00Z"}]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")

    def test_fork_skips_when_marker_comment_is_most_recent(self):
        comments = [
            {"id": 5, "body": "older", "updated_at": "2024-01-01T00:00:00Z"},
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_updates_when_marker_comment_is_not_most_recent(self):
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-01T00:00:00Z"},
            {"id": 12, "body": "follow-up review", "updated_at": "2024-01-03T00:00:00Z"},
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_creates_when_no_marker_comment(self):
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=[{"id": 1, "body": "unrelated", "updated_at": "2024-01-01T00:00:00Z"}],
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "")


class ForkCommitTimestampTests(unittest.TestCase):
    """Fork idempotency: a newer PR source commit must regenerate and update
    the existing marker comment, not skip. Skip only when the marker comment is
    the newest comment and no newer commit followed it. Missing, naive, or
    malformed timestamps on either side conservatively proceed."""

    def _commit(self, sha: str, message: str, date: str) -> dict:
        return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}

    def test_fork_proceeds_when_commit_newer_than_marker_comment(self):
        # Marker comment is the newest comment, but a PR commit arrived after
        # the marker timestamp: regenerate and PATCH the marker in place.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        commits = [self._commit("c" * 40, "human change", "2024-01-03T00:00:00Z")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["mode"], "comment")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_skips_when_marker_newer_than_all_commits(self):
        # Marker comment is newest and its timestamp is newer than every commit.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-03T00:00:00Z"},
        ]
        commits = [self._commit("c" * 40, "human change", "2024-01-02T00:00:00Z")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_proceeds_when_stale_marker_newer_comment_and_commit(self):
        # Marker is not the newest comment (a follow-up comment arrived) and a
        # commit exists: proceed and update the existing marker in place.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-01T00:00:00Z"},
            {"id": 12, "body": "follow-up review", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        commits = [self._commit("c" * 40, "human change", "2024-01-03T00:00:00Z")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_proceeds_when_commit_timestamp_missing(self):
        # Marker is newest comment; the only commit has no committer date.
        # Without a comparable commit timestamp, conservatively proceed.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        commits = [{"sha": "c" * 40, "commit": {"message": "undated"}}]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_proceeds_when_commit_timestamp_malformed(self):
        # Marker is newest comment; the only commit has a malformed date.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        commits = [self._commit("c" * 40, "human change", "not-a-date")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_proceeds_when_marker_timestamp_missing(self):
        # Marker is the only/newest comment but has no timestamp, and a newer
        # commit exists. Missing marker timestamp -> conservatively proceed.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed"},
        ]
        commits = [self._commit("c" * 40, "human change", "2024-01-02T00:00:00Z")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_skips_when_marker_newest_comment_and_no_commits(self):
        # Regression: empty commits with a valid, newest marker timestamp skips.
        comments = [
            {"id": 5, "body": "older", "updated_at": "2024-01-01T00:00:00Z"},
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T00:00:00Z"},
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=[],
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["marker_comment_id"], "9")

    def test_fork_offset_aware_comparison_not_lexical(self):
        # The commit instant (11:00-05:00 == 16:00Z) is newer than the marker
        # instant (12:00Z) even though the commit string sorts lexically before
        # the marker string. Parsed timezone-aware comparison must proceed.
        comments = [
            {"id": 9, "body": f"{MARKER}\nproposed", "updated_at": "2024-01-02T12:00:00Z"},
        ]
        commits = [self._commit("c" * 40, "human change", "2024-01-02T11:00:00-05:00")]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=True),
            request_label="update-changelog",
            target_number=12,
            comments=comments,
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        self.assertEqual(result["marker_comment_id"], "9")


def _resolved_bundle() -> dict:
    return CFG.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile="changelog-update",
        workflow="pr-changelog-update",
    ).to_dict()


def _effective_policy(bundle: dict) -> dict:
    spec = importlib.util.spec_from_file_location("agentic_policy", SCRIPTS / "agentic_policy.py")
    pol = importlib.util.module_from_spec(spec)
    sys.modules["agentic_policy"] = pol
    spec.loader.exec_module(pol)
    agent_text = (Path(bundle["bundle_root"]) / bundle["agent_file"]).read_text(encoding="utf-8")
    caps = pol.parse_agent_capabilities(agent_text)
    eff = pol.merge_policy(
        workflow="pr-changelog-update",
        model_profile="changelog-writer",
        agent_capabilities=caps,
        bundle_policy=bundle.get("bundle_policy", {}),
    )
    return {"sha256": eff.sha256, "timeout_seconds": 300}


class UpdateTests(unittest.TestCase):
    def _opencode_factory(self, decision: str, detail: str, *, edit_target: Path | None = None):
        def fake_run(*, opencode_args, prompt, provider_timeout):
            class Proc:
                returncode = 0
                stdout = (
                    f"CHANGELOG_DECISION: {decision}\n"
                    + (
                        f"CHANGELOG_BLOCKER: {detail}\n"
                        if decision == "BLOCKED"
                        else f"CHANGELOG_SUMMARY: {detail}\n"
                    )
                )
                stderr = ""

            if edit_target is not None and decision == "UPDATED":
                edit_target.parent.mkdir(parents=True, exist_ok=True)
                edit_target.write_text("# Changelog\n\n- new entry\n", encoding="utf-8")
            return Proc()

        return fake_run

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    @staticmethod
    def _commit_all(root: Path, msg: str = "init") -> None:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True)

    def test_update_updated_commits_only_target_file(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            target = root / "CHANGELOG.md"
            target.write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(fork=False),
                diff_text="diff --git a/CHANGELOG.md b/CHANGELOG.md\n+entry\n",
                target_file="CHANGELOG.md",
                repo_root=root,
                opencode_runner=self._opencode_factory("UPDATED", "added entry", edit_target=target),
            )
            self.assertEqual(result["decision"], "UPDATED")
            self.assertEqual(result["changed_files"], ["CHANGELOG.md"])
            self.assertIn("new entry", result["proposed_content"])

    def test_update_no_change_requires_no_edits(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                opencode_runner=self._opencode_factory("NO_CHANGE", "no user-facing change"),
            )
            self.assertEqual(result["decision"], "NO_CHANGE")
            self.assertEqual(result["changed_files"], [])

    def test_update_rejects_extraneous_edits(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            runner = self._opencode_factory("UPDATED", "entry", edit_target=root / "CHANGELOG.md")

            def wrapped(*, opencode_args, prompt, provider_timeout):
                proc = runner(opencode_args=opencode_args, prompt=prompt, provider_timeout=provider_timeout)
                (root / "sneaky.txt").write_text("bad", encoding="utf-8")
                return proc

            with self.assertRaises(RuntimeError):
                RUNNER.run_update(
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    pr=_pr(),
                    diff_text="",
                    target_file="CHANGELOG.md",
                    repo_root=root,
                    opencode_runner=wrapped,
                )

    def test_update_contract_failure_raises(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)

            def bad_run(*, opencode_args, prompt, provider_timeout):
                class Proc:
                    returncode = 0
                    stdout = "no decision here"
                    stderr = ""

                return Proc()

            with self.assertRaises(RUNNER.PROMPTS.ContractError):
                RUNNER.run_update(
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    pr=_pr(),
                    diff_text="",
                    target_file="CHANGELOG.md",
                    repo_root=root,
                    opencode_runner=bad_run,
                )

    def test_update_dry_run_marks_result(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            target = root / "CHANGELOG.md"
            target.write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                dry_run=True,
                opencode_runner=self._opencode_factory("UPDATED", "entry", edit_target=target),
            )
            self.assertTrue(result["dry_run"])

    def test_update_blocked_requires_no_edits(self):
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                opencode_runner=self._opencode_factory("BLOCKED", "scope unclear"),
            )
            self.assertEqual(result["decision"], "BLOCKED")
            self.assertEqual(result["changed_files"], [])


class UpdateDiagnosticsTests(unittest.TestCase):
    """A failed OpenCode run must surface bounded, useful diagnostics."""

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    def _failing_update_message(self, *, stderr_text: str, stdout_text: str = "") -> str:
        bundle = _resolved_bundle()
        policy = _effective_policy(bundle)

        def failing_run(*, opencode_args, prompt, provider_timeout):
            class Proc:
                returncode = 1
                stdout = stdout_text
                stderr = stderr_text

            return Proc()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            with self.assertRaises(RuntimeError) as raised:
                RUNNER.run_update(
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    pr=_pr(),
                    diff_text="",
                    target_file="CHANGELOG.md",
                    repo_root=root,
                    opencode_runner=failing_run,
                )
        return str(raised.exception)

    def test_nonzero_exit_includes_stderr(self):
        message = self._failing_update_message(
            stderr_text="Error: Model not found: provider/model\n"
        )
        self.assertTrue(message.startswith("OpenCode exited with status 1."))
        self.assertIn("Model not found: provider/model", message)

    def test_nonzero_exit_falls_back_to_stdout(self):
        message = self._failing_update_message(
            stderr_text="", stdout_text="Error: no configuration found\n"
        )
        self.assertTrue(message.startswith("OpenCode exited with status 1."))
        self.assertIn("Error: no configuration found", message)

    def test_nonzero_exit_without_output_uses_fixed_note(self):
        message = self._failing_update_message(stderr_text="", stdout_text="")
        self.assertTrue(message.startswith("OpenCode exited with status 1."))
        self.assertIn("No diagnostic output was returned.", message)

    def test_oversized_stderr_is_bounded_and_tail_preserved(self):
        stderr = (
            "BEGIN-SENTINEL\n"
            + "x" * (RUNNER.MAX_DIAGNOSTIC_CHARS * 3)
            + "\nError: provider quota exhausted\n"
        )
        message = self._failing_update_message(stderr_text=stderr)
        self.assertTrue(message.startswith("OpenCode exited with status 1."))
        # The actionable tail is preserved and truncation is marked.
        self.assertIn("Error: provider quota exhausted", message)
        self.assertIn("truncated by workflow", message)
        # The dropped head (beginning of the stream) is not included.
        self.assertNotIn("BEGIN-SENTINEL", message)
        # The diagnostic portion stays within the named cap.
        prefix = "OpenCode exited with status 1. "
        self.assertLessEqual(
            len(message), len(prefix) + RUNNER.MAX_DIAGNOSTIC_CHARS
        )


class GuardRegressionTests(unittest.TestCase):
    """Regression tests for reviewer findings 2 and 3 (same-repo skip and
    newest marker commit selection)."""

    def _commit(self, sha: str, message: str, date: str) -> dict:
        return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}

    def test_same_repo_proceeds_when_newer_non_marker_commit_exists(self):
        # Finding 2: a human commit after the marker commit must not skip.
        commits = [
            self._commit("a" * 40, "human change", "2024-01-01T00:00:00Z"),
            self._commit("b" * 40, f"Update changelog\n\n{MARKER}", "2024-01-02T00:00:00Z"),
            self._commit("c" * 40, "another human change", "2024-01-03T00:00:00Z"),
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=[],
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")

    def test_same_repo_skips_when_marker_commit_is_newest_pr_commit(self):
        commits = [
            self._commit("a" * 40, "human change", "2024-01-01T00:00:00Z"),
            self._commit("b" * 40, f"Update changelog\n\n{MARKER}", "2024-01-02T00:00:00Z"),
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=[],
            commits=commits,
        )
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["prior_marker_commit_sha"], "b" * 40)

    def test_find_marker_commit_returns_newest_match(self):
        # Finding 3: multiple marker commits; newest (last) is selected.
        commits = [
            self._commit("a" * 40, f"Update changelog\n\n{MARKER}", "2024-01-01T00:00:00Z"),
            self._commit("b" * 40, "human", "2024-01-02T00:00:00Z"),
            self._commit("c" * 40, f"Update changelog\n\n{MARKER}", "2024-01-03T00:00:00Z"),
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=[],
            commits=commits,
        )
        # Newest marker commit (c) is also newest PR commit -> skip.
        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["prior_marker_commit_sha"], "c" * 40)

    def test_find_marker_commit_newest_marker_not_newest_commit_proceeds(self):
        commits = [
            self._commit("a" * 40, f"Update changelog\n\n{MARKER}", "2024-01-01T00:00:00Z"),
            self._commit("b" * 40, f"Update changelog\n\n{MARKER}", "2024-01-02T00:00:00Z"),
            self._commit("c" * 40, "human after", "2024-01-03T00:00:00Z"),
        ]
        result = RUNNER.run_guard(
            event_payload=_event(),
            pr=_pr(fork=False),
            request_label="update-changelog",
            target_number=12,
            comments=[],
            commits=commits,
        )
        self.assertEqual(result["action"], "proceed")
        # Newest marker commit is b, not a.
        self.assertEqual(result["prior_marker_commit_sha"], "b" * 40)


class ChangedPathHelperSubtreeTests(unittest.TestCase):
    """Regression test for reviewer finding 1: the nested .agentic-workflow/
    helper checkout must not appear as a changed path."""

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    @staticmethod
    def _commit_all(root: Path, msg: str = "init") -> None:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True)

    def test_helper_subtree_ignored_from_changed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            # Simulate the pinned helper checkout appearing untracked.
            helper = root / ".agentic-workflow" / "scripts"
            helper.mkdir(parents=True)
            (helper / "resolve_invocation.py").write_text("# helper", encoding="utf-8")
            changed = RUNNER._collect_changed_paths(root)
            self.assertNotIn(".agentic-workflow", changed)
            self.assertNotIn(".agentic-workflow/scripts/resolve_invocation.py", changed)
            # No real edits -> empty changed set.
            self.assertEqual(changed, [])

    def test_helper_subtree_ignored_but_real_target_edit_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            helper = root / ".agentic-workflow"
            helper.mkdir(parents=True)
            (helper / "x.txt").write_text("x", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("# Changelog\n\n- entry\n", encoding="utf-8")
            changed = RUNNER._collect_changed_paths(root)
            self.assertEqual(changed, ["CHANGELOG.md"])


class TruncationTests(unittest.TestCase):
    """Regression tests for reviewer finding 4: byte-correct truncation and
    fork comment body bounding."""

    def test_truncate_bytes_preserves_under_limit(self):
        self.assertEqual(RUNNER._truncate_bytes("abc", 100), "abc")

    def test_truncate_bytes_is_byte_correct_on_multibyte(self):
        # 3-byte chars; truncating mid-codepoint must not raise and the result
        # must not exceed the byte budget.
        text = "é" * 100  # 100 * 3 = 300 bytes
        truncated = RUNNER._truncate_bytes(text, 10)
        self.assertLessEqual(len(truncated.encode("utf-8")), 10)
        # Decodes cleanly (no UnicodeDecodeError).
        truncated.encode("utf-8")

    def test_truncate_bytes_adds_marker_when_truncating(self):
        truncated = RUNNER._truncate_bytes("x" * 1000, 50)
        self.assertIn("[truncated by workflow]", truncated)
        self.assertLessEqual(len(truncated.encode("utf-8")), 50)

    def test_truncate_chars_bounded(self):
        truncated = RUNNER._truncate_chars("x" * 1000, 50)
        self.assertLessEqual(len(truncated), 50)
        self.assertIn("[truncated by workflow]", truncated)

    def test_max_comment_chars_constant_below_github_limit(self):
        self.assertLess(RUNNER.MAX_COMMENT_CHARS, 65536)
        self.assertGreaterEqual(RUNNER.MAX_COMMENT_CHARS, 60000)


class SeedTargetTests(unittest.TestCase):
    """Regression test for reviewer finding 5: seed target from PR head."""

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    def test_seed_target_writes_pr_head_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            # Base content (trusted base).
            (root / "CHANGELOG.md").write_text("# Base changelog\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "base"], check=True)
            base_branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--show-current"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            # PR head content on a branch, fetched as pr-head ref.
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "prhead"], check=True)
            (root / "CHANGELOG.md").write_text("# PR head changelog\n- pr entry\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "pr"], check=True)
            subprocess.run(["git", "-C", str(root), "checkout", "-q", base_branch], check=True)
            subprocess.run(
                ["git", "-C", str(root), "branch", "pr-head", "prhead"],
                check=True,
            )
            seeded = RUNNER.seed_target_from_pr_head(
                repo_root=root, target_file="CHANGELOG.md", head_ref="pr-head"
            )
            self.assertIn("PR head changelog", seeded)
            self.assertEqual(
                (root / "CHANGELOG.md").read_text(encoding="utf-8"), seeded
            )

    def test_seed_target_absent_removes_stale_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Base changelog\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "base"], check=True)
            base_branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--show-current"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            # PR head does not contain the target.
            subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "prhead"], check=True)
            subprocess.run(["git", "-C", str(root), "rm", "CHANGELOG.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "rm"], check=True)
            subprocess.run(["git", "-C", str(root), "checkout", "-q", base_branch], check=True)
            subprocess.run(
                ["git", "-C", str(root), "branch", "pr-head", "prhead"],
                check=True,
            )
            seeded = RUNNER.seed_target_from_pr_head(
                repo_root=root, target_file="CHANGELOG.md", head_ref="pr-head"
            )
            self.assertEqual(seeded, "")
            self.assertFalse((root / "CHANGELOG.md").exists())


class ProvenanceMarkPublishedTests(unittest.TestCase):
    """Regression test for reviewer finding 7: mark-published promotes a
    generated record to published."""

    def test_mark_published_promotes_result(self):
        import argparse
        with tempfile.TemporaryDirectory() as tmp:
            prov_path = Path(tmp) / "prov.json"
            prov_path.write_text(
                json.dumps({"result": "generated", "workflow_name": "pr-changelog-update"}),
                encoding="utf-8",
            )
            args = argparse.Namespace(provenance=prov_path)
            rc = RUNNER._cmd_mark_published(args)
            self.assertEqual(rc, 0)
            record = json.loads(prov_path.read_text(encoding="utf-8"))
            self.assertEqual(record["result"], "published")

    def test_mark_published_missing_file_returns_nonzero(self):
        import argparse
        args = argparse.Namespace(provenance=Path("/nonexistent/prov.json"))
        self.assertEqual(RUNNER._cmd_mark_published(args), 1)


class UpdateSeedTargetIntegrationTests(unittest.TestCase):
    """Finding 1 regression: UPDATED and NO_CHANGE succeed with a nested
    .agentic-workflow/ helper checkout present in the workspace."""

    def _opencode(self, decision: str, detail: str, *, edit_target: Path | None = None):
        def fake_run(*, opencode_args, prompt, provider_timeout):
            class Proc:
                returncode = 0
                stdout = (
                    f"CHANGELOG_DECISION: {decision}\n"
                    + (
                        f"CHANGELOG_BLOCKER: {detail}\n"
                        if decision == "BLOCKED"
                        else f"CHANGELOG_SUMMARY: {detail}\n"
                    )
                )
                stderr = ""

            if edit_target is not None and decision == "UPDATED":
                edit_target.parent.mkdir(parents=True, exist_ok=True)
                edit_target.write_text("# Changelog\n\n- new entry\n", encoding="utf-8")
            return Proc()

        return fake_run

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    @staticmethod
    def _commit_all(root: Path, msg: str = "init") -> None:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True)

    def _bundle_policy(self):
        bundle = CFG.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="changelog-update",
            workflow="pr-changelog-update",
        ).to_dict()
        spec = importlib.util.spec_from_file_location(
            "agentic_policy", SCRIPTS / "agentic_policy.py"
        )
        pol = importlib.util.module_from_spec(spec)
        sys.modules["agentic_policy"] = pol
        spec.loader.exec_module(pol)
        agent_text = (Path(bundle["bundle_root"]) / bundle["agent_file"]).read_text(encoding="utf-8")
        caps = pol.parse_agent_capabilities(agent_text)
        eff = pol.merge_policy(
            workflow="pr-changelog-update",
            model_profile="changelog-writer",
            agent_capabilities=caps,
            bundle_policy=bundle.get("bundle_policy", {}),
        )
        return bundle, {"sha256": eff.sha256, "timeout_seconds": 300}

    def test_updated_succeeds_with_helper_subtree_present(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            target = root / "CHANGELOG.md"
            target.write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            # Nested helper checkout present (untracked).
            helper = root / ".agentic-workflow" / "scripts"
            helper.mkdir(parents=True)
            (helper / "resolve_invocation.py").write_text("# helper", encoding="utf-8")
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(fork=False),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                opencode_runner=self._opencode("UPDATED", "entry", edit_target=target),
            )
            self.assertEqual(result["decision"], "UPDATED")
            self.assertEqual(result["changed_files"], ["CHANGELOG.md"])

    def test_no_change_succeeds_with_helper_subtree_present(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            self._commit_all(root)
            helper = root / ".agentic-workflow"
            helper.mkdir(parents=True)
            (helper / "x.txt").write_text("x", encoding="utf-8")
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                opencode_runner=self._opencode("NO_CHANGE", "none"),
            )
            self.assertEqual(result["decision"], "NO_CHANGE")
            self.assertEqual(result["changed_files"], [])


class SeedTargetBaselineTests(unittest.TestCase):
    """Finding 1 regression: baseline agent modifications against the seeded
    PR-head target state. NO_CHANGE/BLOCKED succeed when the agent leaves the
    seeded PR-head target untouched, even though seeding changed the worktree
    relative to the base checkout. UPDATED must actually change the target
    relative to the seeded state."""

    @staticmethod
    def _init_git(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)

    @staticmethod
    def _commit_all(root: Path, msg: str = "init") -> None:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True)

    def _bundle_policy(self):
        bundle = CFG.resolve_local_bundle(
            bundle_root=REPO_ROOT / ".opencode" / "configuration",
            profile="changelog-update",
            workflow="pr-changelog-update",
        ).to_dict()
        spec = importlib.util.spec_from_file_location(
            "agentic_policy", SCRIPTS / "agentic_policy.py"
        )
        pol = importlib.util.module_from_spec(spec)
        sys.modules["agentic_policy"] = pol
        spec.loader.exec_module(pol)
        agent_text = (Path(bundle["bundle_root"]) / bundle["agent_file"]).read_text(encoding="utf-8")
        caps = pol.parse_agent_capabilities(agent_text)
        eff = pol.merge_policy(
            workflow="pr-changelog-update",
            model_profile="changelog-writer",
            agent_capabilities=caps,
            bundle_policy=bundle.get("bundle_policy", {}),
        )
        return bundle, {"sha256": eff.sha256, "timeout_seconds": 300}

    def _setup_pr_head_target(self, root: Path, pr_head_content: str | None):
        """Create a base CHANGELOG.md and a pr-head ref with differing content."""
        base_content = "# Base changelog\n"
        (root / "CHANGELOG.md").write_text(base_content, encoding="utf-8")
        self._commit_all(root, "base")
        base_branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "prhead"], check=True)
        if pr_head_content is None:
            subprocess.run(["git", "-C", str(root), "rm", "CHANGELOG.md"], check=True)
        else:
            (root / "CHANGELOG.md").write_text(pr_head_content, encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "pr"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-q", base_branch], check=True)
        subprocess.run(["git", "-C", str(root), "branch", "pr-head", "prhead"], check=True)

    def test_no_change_succeeds_when_seeded_target_differs_from_base(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            # PR head target differs from base; agent leaves it untouched.
            self._setup_pr_head_target(root, "# PR head changelog\n- pr entry\n")
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(fork=False),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                seed_target=True,
                opencode_runner=self._noop_runner("NO_CHANGE", "no user-facing change"),
            )
            self.assertEqual(result["decision"], "NO_CHANGE")
            self.assertEqual(result["changed_files"], [])

    def test_blocked_succeeds_when_seeded_target_differs_from_base(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            self._setup_pr_head_target(root, "# PR head changelog\n- pr entry\n")
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(fork=False),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                seed_target=True,
                opencode_runner=self._noop_runner("BLOCKED", "scope unclear"),
            )
            self.assertEqual(result["decision"], "BLOCKED")
            self.assertEqual(result["changed_files"], [])

    def test_updated_succeeds_when_agent_changes_seeded_target(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            self._setup_pr_head_target(root, "# PR head changelog\n")
            target = root / "CHANGELOG.md"
            result = RUNNER.run_update(
                resolved_bundle=bundle,
                effective_policy=policy,
                pr=_pr(fork=False),
                diff_text="",
                target_file="CHANGELOG.md",
                repo_root=root,
                seed_target=True,
                opencode_runner=self._edit_target_runner(
                    "UPDATED", "added entry", target, "# PR head changelog\n\n- new\n"
                ),
            )
            self.assertEqual(result["decision"], "UPDATED")
            self.assertIn("new", result["proposed_content"])

    def test_updated_fails_when_agent_leaves_seeded_target_unchanged(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            self._setup_pr_head_target(root, "# PR head changelog\n")
            with self.assertRaises(RuntimeError) as ctx:
                RUNNER.run_update(
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    pr=_pr(fork=False),
                    diff_text="",
                    target_file="CHANGELOG.md",
                    repo_root=root,
                    seed_target=True,
                    opencode_runner=self._noop_runner("UPDATED", "entry"),
                )
            self.assertIn("UPDATED requires the target file to change", str(ctx.exception))

    def test_no_change_fails_when_agent_modifies_seeded_target(self):
        bundle, policy = self._bundle_policy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_git(root)
            self._setup_pr_head_target(root, "# PR head changelog\n")
            target = root / "CHANGELOG.md"
            with self.assertRaises(RuntimeError) as ctx:
                RUNNER.run_update(
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    pr=_pr(fork=False),
                    diff_text="",
                    target_file="CHANGELOG.md",
                    repo_root=root,
                    seed_target=True,
                    opencode_runner=self._edit_target_runner(
                        "NO_CHANGE", "none", target, "# PR head changelog\n- modified\n"
                    ),
                )
            self.assertIn("NO_CHANGE must leave the target", str(ctx.exception))

    @staticmethod
    def _noop_runner(decision: str, detail: str):
        def fake_run(*, opencode_args, prompt, provider_timeout):
            class Proc:
                returncode = 0
                stdout = (
                    f"CHANGELOG_DECISION: {decision}\n"
                    + (
                        f"CHANGELOG_BLOCKER: {detail}\n"
                        if decision == "BLOCKED"
                        else f"CHANGELOG_SUMMARY: {detail}\n"
                    )
                )
                stderr = ""

            return Proc()

        return fake_run

    @staticmethod
    def _edit_target_runner(decision: str, detail: str, target: Path, new_content: str):
        def fake_run(*, opencode_args, prompt, provider_timeout):
            class Proc:
                returncode = 0
                stdout = (
                    f"CHANGELOG_DECISION: {decision}\n"
                    + (
                        f"CHANGELOG_BLOCKER: {detail}\n"
                        if decision == "BLOCKED"
                        else f"CHANGELOG_SUMMARY: {detail}\n"
                    )
                )
                stderr = ""

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8")
            return Proc()

        return fake_run


if __name__ == "__main__":
    unittest.main()
