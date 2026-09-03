"""Tests for pull-request review location validation."""

from __future__ import annotations

import importlib.util
import argparse
import contextlib
import io
import json
import urllib.parse
from pathlib import Path
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "run_agentic_feedback.py"
SPEC = importlib.util.spec_from_file_location("run_agentic_feedback", SCRIPT_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ParseReviewOutputTests(unittest.TestCase):
    """Verify valid locations remain inline and invalid ones remain safe."""

    def parse(
        self, comments: list[dict[str, object]]
    ) -> tuple[str, list[dict[str, object]]]:
        return RUNNER.parse_review_output(
            json.dumps({"summary": "Overall review.", "comments": comments}),
            {"test-script.py": {10, 11}},
        )

    def test_accepts_valid_single_line_comment(self) -> None:
        _, comments = self.parse(
            [{"path": "test-script.py", "line": 10, "body": "Review note."}]
        )

        self.assertEqual(
            comments,
            [
                {
                    "path": "test-script.py",
                    "line": 10,
                    "side": "RIGHT",
                    "body": "Review note.",
                }
            ],
        )

    def test_recovers_valid_line_with_invalid_range_without_suggestion(self) -> None:
        _, comments = self.parse(
            [
                {
                    "path": "test-script.py",
                    "start_line": 9,
                    "line": 10,
                    "body": "Review note.",
                }
            ]
        )

        self.assertEqual(
            comments,
            [
                {
                    "path": "test-script.py",
                    "line": 10,
                    "side": "RIGHT",
                    "body": "Review note.",
                }
            ],
        )

    def test_retains_invalid_line_as_summary_feedback(self) -> None:
        summary, comments = self.parse(
            [{"path": "test-script.py", "line": 9, "body": "Review note."}]
        )

        self.assertEqual(comments, [])
        self.assertIn(
            "Additional feedback (no valid inline location):** Review note.", summary
        )
        self.assertNotIn("test-script.py:9", summary)

    def test_rejects_invalid_suggestion_range(self) -> None:
        summary, comments = self.parse(
            [
                {
                    "path": "test-script.py",
                    "start_line": 9,
                    "line": 10,
                    "body": "Review note.",
                    "suggestion": 'print("Hello, World!")',
                }
            ]
        )

        self.assertEqual(comments, [])
        self.assertIn(
            "Additional feedback (no valid inline location):** Review note.", summary
        )
        self.assertNotIn("test-script.py:10", summary)

    def test_formats_allowed_locations(self) -> None:
        self.assertEqual(
            RUNNER.format_changed_locations({"b.py": {3, 1}, "a.py": {2}}),
            "- a.py:2\n- b.py:1\n- b.py:3",
        )

    def test_changed_line_contents_pairs_added_lines_with_coordinates(self) -> None:
        # Realistic multi-hunk diff with context lines, deletions, a blank
        # addition, and nonconsecutive additions across two hunks. The
        # content-to-coordinate mapping must record each added line at its
        # true new-file line number, independently of surrounding context,
        # deletion, or hunk structure.
        diff = (
            "diff --git a/docs/guide.md b/docs/guide.md\n"
            "--- a/docs/guide.md\n"
            "+++ b/docs/guide.md\n"
            "@@ -1,3 +1,5 @@\n"
            " keep one\n"
            "-replace two\n"
            "+fixed two\n"
            " keep three\n"
            "+added four\n"
            "+\n"
            "@@ -10,2 +13,2 @@\n"
            " keep ten\n"
            "-remove eleven\n"
            "+fixed eleven\n"
        )
        contents = RUNNER.changed_line_contents_by_path(diff)
        guide = contents["docs/guide.md"]
        # Added lines record their true new-file coordinates, independently
        # of preceding context and deletions.
        self.assertEqual(guide[2], "fixed two")
        self.assertEqual(guide[4], "added four")
        self.assertEqual(guide[5], "")  # blank addition
        self.assertEqual(guide[14], "fixed eleven")  # second hunk
        # Context lines are not eligible suggestion anchors.
        for context_line in (1, 3, 13):
            self.assertNotIn(context_line, guide)
        # Deleted-line text is never recorded as added content.
        self.assertNotIn("replace two", guide.values())
        self.assertNotIn("remove eleven", guide.values())
        # Coordinates match the line-set parser exactly.
        self.assertEqual(
            RUNNER.changed_lines_by_path(diff)["docs/guide.md"], {2, 4, 5, 14}
        )

    def test_format_changed_line_contents_pairs_content_with_coordinates(self) -> None:
        diff = (
            "diff --git a/docs/guide.md b/docs/guide.md\n"
            "--- a/docs/guide.md\n"
            "+++ b/docs/guide.md\n"
            "@@ -1,3 +1,5 @@\n"
            " keep one\n"
            "-replace two\n"
            "+fixed two\n"
            " keep three\n"
            "+added four\n"
            "+\n"
            "@@ -10,2 +13,2 @@\n"
            " keep ten\n"
            "-remove eleven\n"
            "+fixed eleven\n"
        )
        formatted = RUNNER.format_changed_line_contents(
            RUNNER.changed_line_contents_by_path(diff)
        )
        # Every added line's coordinate is paired with its content.
        self.assertIn("- docs/guide.md:2", formatted)
        self.assertIn("- docs/guide.md:14", formatted)
        # Nonconsecutive additions keep independent coordinates.
        self.assertIn("  fixed two", formatted)
        self.assertIn("  fixed eleven", formatted)
        # Deleted and context text is not presented as eligible anchors.
        self.assertNotIn("replace two", formatted)
        self.assertNotIn("remove eleven", formatted)
        self.assertNotIn("keep one", formatted)
        # A blank added line still gets a coordinate entry.
        self.assertIn("- docs/guide.md:5", formatted)

    def test_format_changed_line_contents_empty_diff(self) -> None:
        self.assertEqual(
            RUNNER.format_changed_line_contents({}),
            "- No changed new-file lines are available.",
        )

    def test_parses_diff_file_statuses(self) -> None:
        diff = """diff --git a/a.txt b/a.txt
index 1..2 100644
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+new
"""
        files = RUNNER.parse_diff_files(diff)
        self.assertEqual(files["a.txt"].status, "modified")
        self.assertEqual(files["new.txt"].status, "added")
        self.assertEqual(files["a.txt"].added_lines, frozenset({1}))

    def test_rejects_unsafe_context_path(self) -> None:
        self.assertFalse(RUNNER._safe_context_path("../secret", ()))
        self.assertFalse(RUNNER._safe_context_path("/secret", ()))

    def test_context_loop_reports_invalid_model_output(self) -> None:
        """Malformed context-loop output must not fail without diagnostics."""
        args = argparse.Namespace(
            max_context_rounds=None,
            max_context_files=None,
            max_context_bytes=None,
            feedback_kind="pr-documentation-review",
            repository="owner/repository",
            author="octocat",
            pull_number=1,
            focus=None,
            base_ref="base",
            head_ref="head",
        )
        bundle = {
            "agent_name": "documentation-review",
            "limits": {"max_comments": 10},
            "prompt_template_text": "Review documentation.",
        }
        policy = {"context": {"max_context_rounds": 0}}
        with mock.patch.object(
            RUNNER,
            "_run_opencode_integrated",
            return_value=(0, "not valid JSON"),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc, _, _ = RUNNER.review_with_context_loop(
                    args=args,
                    resolved_bundle=bundle,
                    effective_policy=policy,
                    changed_lines={},
                    input_text="",
                    pr_metadata={},
                    effective_max_comments=10,
                    provider_timeout=1,
                )
        self.assertEqual(rc, 1)
        self.assertIn(
            "OpenCode response violated the PR review contract", stderr.getvalue()
        )


class ReviewRequestGateTests(unittest.TestCase):
    """Verify the re-review boundary and eligibility algorithm."""

    def _entry(
        self,
        login: str,
        timestamp: str | None,
        body: str = "",
        *,
        review: bool = False,
    ) -> dict:
        entry: dict = {"user": {"login": login}, "body": body}
        if review:
            entry["submitted_at"] = timestamp
        else:
            entry["created_at"] = timestamp
        return entry

    def test_entry_timestamp_prefers_review_submitted_at(self) -> None:
        entry = {
            "submitted_at": "2026-01-02T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        }
        parsed = RUNNER._entry_timestamp(entry)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 1)
        self.assertEqual(parsed.day, 2)

    def test_entry_timestamp_uses_comment_created_at(self) -> None:
        entry = {"created_at": "2026-02-03T04:05:06+02:00"}
        parsed = RUNNER._entry_timestamp(entry)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.hour, 4)

    def test_entry_timestamp_falls_back_to_updated_at(self) -> None:
        entry = {"updated_at": "2026-03-04T00:00:00Z"}
        parsed = RUNNER._entry_timestamp(entry)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.month, 3)

    def test_entry_timestamp_malformed_is_none(self) -> None:
        self.assertIsNone(RUNNER._entry_timestamp({"created_at": "not-a-date"}))
        self.assertIsNone(RUNNER._entry_timestamp({}))
        self.assertIsNone(RUNNER._entry_timestamp({"created_at": ""}))

    def test_entry_timestamp_is_offset_aware(self) -> None:
        # The same instant expressed with different offsets compares equal.
        zulu = RUNNER._entry_timestamp({"created_at": "2026-01-01T12:00:00Z"})
        offset = RUNNER._entry_timestamp(
            {"created_at": "2026-01-01T14:00:00+02:00"}
        )
        self.assertIsNotNone(zulu)
        self.assertIsNotNone(offset)
        self.assertEqual(zulu, offset)
        self.assertTrue(offset > RUNNER._entry_timestamp({"created_at": "2026-01-01T11:59:59Z"}))

    def _eligible(
        self,
        comment_entries: list[dict],
        review_entries: list[dict] | None = None,
        *,
        bot_login: str = "github-actions[bot]",
        request_string: str = "AI REVIEW REQUESTED",
    ) -> bool:
        review_entries = review_entries or []
        entries_by_url = {
            "https://api.github.com/repos/o/r/issues/1/comments": comment_entries,
            "https://api.github.com/repos/o/r/pulls/1/reviews": review_entries,
        }

        def fake_iter(url, token):
            yield from entries_by_url.get(url, [])

        with mock.patch.object(RUNNER, "_iter_json_entries", side_effect=fake_iter):
            return RUNNER._review_request_eligible(
                "https://api.github.com/repos/o/r/issues/1/comments",
                "https://api.github.com/repos/o/r/pulls/1/reviews",
                "token",
                bot_login=bot_login,
                request_string=request_string,
            )

    def test_eligible_comment_after_boundary_returns_true(self) -> None:
        comments = [
            self._entry("github-actions[bot]", "2026-01-01T10:00:00Z", "prior review"),
            self._entry(
                "octocat",
                "2026-01-01T11:00:00Z",
                "Please look again: AI REVIEW REQUESTED",
            ),
        ]
        self.assertTrue(self._eligible(comments, [self._entry("octocat", "2026-01-01T09:00:00Z", "ok", review=True)]))

    def test_request_older_than_bot_action_returns_false(self) -> None:
        comments = [
            self._entry("octocat", "2026-01-01T10:00:00Z", "AI REVIEW REQUESTED"),
            self._entry("github-actions[bot]", "2026-01-01T11:00:00Z", "review posted"),
        ]
        self.assertFalse(self._eligible(comments))

    def test_request_authored_by_bot_returns_false(self) -> None:
        comments = [
            self._entry("github-actions[bot]", "2026-01-01T10:00:00Z", "review"),
            self._entry(
                "github-actions[bot]",
                "2026-01-01T11:00:00Z",
                "echoes AI REVIEW REQUESTED",
            ),
        ]
        self.assertFalse(self._eligible(comments))

    def test_no_boundary_returns_false(self) -> None:
        comments = [
            self._entry("octocat", "2026-01-01T11:00:00Z", "AI REVIEW REQUESTED"),
        ]
        self.assertFalse(self._eligible(comments))

    def test_boundary_spans_reviews_and_comments(self) -> None:
        # The newest bot artifact is a review; a comment request older than
        # that review must not authorize a re-run even though it is newer
        # than an earlier bot comment.
        comments = [
            self._entry("github-actions[bot]", "2026-01-01T08:00:00Z", "comment"),
            self._entry("octocat", "2026-01-01T09:00:00Z", "AI REVIEW REQUESTED"),
        ]
        reviews = [
            self._entry(
                "github-actions[bot]",
                "2026-01-01T10:00:00Z",
                "review body",
                review=True,
            ),
        ]
        self.assertFalse(self._eligible(comments, reviews))
        # A request strictly after the newest review becomes eligible.
        comments.append(
            self._entry("octocat", "2026-01-01T10:00:01Z", "AI REVIEW REQUESTED again")
        )
        self.assertTrue(self._eligible(comments, reviews))

    def test_request_at_exact_boundary_is_not_eligible(self) -> None:
        comments = [
            self._entry("github-actions[bot]", "2026-01-01T10:00:00Z", "review"),
            self._entry("octocat", "2026-01-01T10:00:00Z", "AI REVIEW REQUESTED"),
        ]
        self.assertFalse(self._eligible(comments))

    def test_substring_match_is_case_sensitive(self) -> None:
        comments = [
            self._entry("github-actions[bot]", "2026-01-01T10:00:00Z", "review"),
            self._entry("octocat", "2026-01-01T11:00:00Z", "ai review requested"),
        ]
        self.assertFalse(self._eligible(comments))

    def test_iter_json_entries_forces_per_page_100(self) -> None:
        # A bare URL gains per_page=100, and an existing smaller per_page
        # parameter is overridden, matching _has_v2_marker_match.
        requests_made: list[str] = []

        def fake_request(url, token):
            requests_made.append(url)
            return ([{"id": 1}], {})

        with mock.patch.object(RUNNER, "github_request", side_effect=fake_request):
            entries = list(
                RUNNER._iter_json_entries(
                    "https://api.github.com/repos/o/r/issues/1/comments", "token"
                )
            )
        self.assertEqual(entries, [{"id": 1}])
        self.assertEqual(requests_made, ["https://api.github.com/repos/o/r/issues/1/comments?per_page=100"])

        requests_made.clear()
        with mock.patch.object(RUNNER, "github_request", side_effect=fake_request):
            list(
                RUNNER._iter_json_entries(
                    "https://api.github.com/repos/o/r/issues/1/comments?per_page=5&since=2026-01-01",
                    "token",
                )
            )
        forced = urllib.parse.parse_qs(urllib.parse.urlparse(requests_made[0]).query)
        self.assertEqual(forced["per_page"], ["100"])
        self.assertEqual(forced["since"], ["2026-01-01"])

    def test_iter_json_entries_follows_next_links_until_exhausted(self) -> None:
        # rel="next" Link headers are followed; pagination stops when a page
        # has no next link. Non-object entries are filtered out.
        first_url = "https://api.github.com/repos/o/r/issues/1/comments?per_page=100"
        second_url = "https://api.github.com/repos/o/r/issues/1/comments?page=2&per_page=100"
        pages = {
            first_url: (
                [{"id": 1}, "not-an-object"],
                {
                    "Link": (
                        f'<{second_url}>; rel="next", '
                        f'<{first_url}>; rel="first"'
                    )
                },
            ),
            second_url: ([{"id": 2}, {"id": 3}], {}),
        }
        requests_made: list[str] = []

        def fake_request(url, token):
            requests_made.append(url)
            return pages[url]

        with mock.patch.object(RUNNER, "github_request", side_effect=fake_request):
            entries = list(
                RUNNER._iter_json_entries(
                    "https://api.github.com/repos/o/r/issues/1/comments", "token"
                )
            )
        self.assertEqual([entry["id"] for entry in entries], [1, 2, 3])
        self.assertEqual(requests_made, [first_url, second_url])

    def test_bot_login_resolves_from_user_endpoint(self) -> None:
        with mock.patch.object(
            RUNNER,
            "github_request",
            return_value=({"login": "custom-bot"}, {}),
        ) as mock_gh:
            login = RUNNER._bot_login(
                "token", "https://api.github.com/repos/o/r/issues/1/comments"
            )
        self.assertEqual(login, "custom-bot")
        mock_gh.assert_called_once_with(
            "https://api.github.com/user", "token"
        )

    def test_bot_login_falls_back_on_error(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            RUNNER,
            "github_request",
            side_effect=RuntimeError("lookup failed"),
        ):
            with contextlib.redirect_stderr(stderr):
                login = RUNNER._bot_login(
                    "token", "https://api.github.com/repos/o/r/issues/1/comments"
                )
        self.assertEqual(login, "github-actions[bot]")
        self.assertIn("::warning::", stderr.getvalue())

    def test_bot_login_falls_back_on_missing_login(self) -> None:
        with mock.patch.object(
            RUNNER,
            "github_request",
            return_value=({}, {}),
        ):
            login = RUNNER._bot_login(
                "token", "https://api.github.com/repos/o/r/issues/1/comments"
            )
        self.assertEqual(login, "github-actions[bot]")


if __name__ == "__main__":
    unittest.main()
