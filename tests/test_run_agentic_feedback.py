"""Tests for pull-request review location validation."""

from __future__ import annotations

import importlib.util
import argparse
import contextlib
import io
import json
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


if __name__ == "__main__":
    unittest.main()
