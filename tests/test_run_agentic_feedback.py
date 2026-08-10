"""Tests for pull-request review location validation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "run_agentic_feedback.py"
SPEC = importlib.util.spec_from_file_location("run_agentic_feedback", SCRIPT_PATH)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ParseReviewOutputTests(unittest.TestCase):
    """Verify valid locations remain inline and invalid ones remain safe."""

    def parse(self, comments: list[dict[str, object]]) -> tuple[str, list[dict[str, object]]]:
        return RUNNER.parse_review_output(
            json.dumps({"summary": "Overall review.", "comments": comments}),
            {"test-script.py": {10, 11}},
        )

    def test_accepts_valid_single_line_comment(self) -> None:
        _, comments = self.parse([{"path": "test-script.py", "line": 10, "body": "Review note."}])

        self.assertEqual(comments, [{"path": "test-script.py", "line": 10, "side": "RIGHT", "body": "Review note."}])

    def test_recovers_valid_line_with_invalid_range_without_suggestion(self) -> None:
        _, comments = self.parse(
            [{"path": "test-script.py", "start_line": 9, "line": 10, "body": "Review note."}]
        )

        self.assertEqual(comments, [{"path": "test-script.py", "line": 10, "side": "RIGHT", "body": "Review note."}])

    def test_retains_invalid_line_as_summary_feedback(self) -> None:
        summary, comments = self.parse([{"path": "test-script.py", "line": 9, "body": "Review note."}])

        self.assertEqual(comments, [])
        self.assertIn("Additional feedback (`test-script.py:9`):** Review note.", summary)

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
        self.assertIn("Additional feedback (`test-script.py:10`):** Review note.", summary)

    def test_formats_allowed_locations(self) -> None:
        self.assertEqual(
            RUNNER.format_changed_locations({"b.py": {3, 1}, "a.py": {2}}),
            "- a.py:2\n- b.py:1\n- b.py:3",
        )

    def test_scopes_pull_request_marker_to_head_commit(self) -> None:
        self.assertEqual(
            RUNNER.feedback_marker("pr-documentation-review", "abc123"),
            "<!-- agentic-workflow:pr-documentation-review:v1:abc123 -->",
        )

    def test_keeps_issue_marker_compatible_without_commit(self) -> None:
        self.assertEqual(
            RUNNER.feedback_marker("issue-feedback"),
            "<!-- agentic-workflow:issue-feedback:v1 -->",
        )


if __name__ == "__main__":
    unittest.main()