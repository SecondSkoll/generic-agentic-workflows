#!/usr/bin/env python3
"""Run an OpenCode review using repository-provided agent customisations.

The script is intentionally dependency-free so it can run in a GitHub Actions
runner. It validates the selected agent and skill files, asks OpenCode for
feedback, and posts at most one marked comment or pull-request review for each
feedback type.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def front_matter_name(path: Path) -> str:
    """Read the required `name` from a Markdown file's YAML front matter.

    Args:
        path: Path to the agent or skill Markdown file.

    Returns:
        The non-empty front-matter name.

    Raises:
        ValueError: If the file has no valid YAML front matter or `name` field.
    """
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        raise ValueError(f"{path} does not start with YAML front matter")
    name = re.search(r"^name:\s*[\"']?([^\"'\n]+)[\"']?\s*$", match.group(1), re.MULTILINE)
    if not name:
        raise ValueError(f"{path} has no front-matter name")
    return name.group(1).strip()


def github_request(
    url: str, token: str, method: str = "GET", body: dict | None = None
) -> tuple[object, dict[str, str]]:
    """Send an authenticated request to the GitHub REST API.

    Args:
        url: Absolute GitHub API endpoint URL.
        token: GitHub token used for bearer-token authentication.
        method: HTTP method to use, such as ``GET`` or ``POST``.
        body: Optional JSON-serialisable request body.

    Returns:
        A tuple containing the decoded JSON response and response headers.

    Raises:
        urllib.error.HTTPError: If GitHub returns an HTTP error response.
        urllib.error.URLError: If the request cannot reach GitHub.
    """
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response), dict(response.headers.items())


def has_marker(url: str, token: str, marker: str) -> bool:
    """Check every paginated response page for a workflow feedback marker.

    Args:
        url: GitHub API URL returning issue comments or pull-request reviews.
        token: GitHub token used to read comments.
        marker: Invisible marker uniquely identifying the workflow feedback.

    Returns:
        ``True`` when an existing comment contains the marker; otherwise
        ``False``.

    Raises:
        urllib.error.HTTPError: If GitHub rejects a comments request.
        urllib.error.URLError: If a comments request cannot reach GitHub.
    """
    parsed_url = urllib.parse.urlparse(url)
    parameters = urllib.parse.parse_qs(parsed_url.query)
    parameters["per_page"] = ["100"]
    url = urllib.parse.urlunparse(parsed_url._replace(query=urllib.parse.urlencode(parameters, doseq=True)))
    while url:
        comments, headers = github_request(url, token)
        if any(marker in comment.get("body", "") for comment in comments):
            return True
        links = headers.get("Link", "")
        next_link = re.search(r'<([^>]+)>;\s*rel="next"', links)
        url = next_link.group(1) if next_link else ""
    return False


def changed_lines_by_path(diff: str) -> dict[str, set[int]]:
    """Return changed new-file line numbers, indexed by repository path.

    GitHub only accepts a RIGHT-side line comment when it applies to a line in
    the PR diff. Restricting comments to added lines makes review publication
    deterministic and prevents malformed model output from failing the entire
    review request.
    """
    lines_by_path: dict[str, set[int]] = {}
    path: str | None = None
    new_line: int | None = None

    for line in diff.splitlines():
        if line.startswith("+++ "):
            candidate = line[4:]
            path = candidate[2:] if candidate.startswith("b/") else None
            new_line = None
            if path and path != "/dev/null":
                lines_by_path.setdefault(path, set())
            continue

        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            new_line = int(hunk.group(1))
            continue

        if path is None or new_line is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            lines_by_path[path].add(new_line)
            new_line += 1
        elif not line.startswith("-"):
            new_line += 1

    return lines_by_path


def suggestion_body(feedback: str, suggestion: str) -> str:
    """Format a GitHub suggested-change comment from feedback and replacement.

    GitHub renders this fenced block with controls that let a reviewer apply
    the replacement directly from the pull-request conversation.
    """
    return f"{feedback}\n\n```suggestion\n{suggestion}\n```"


def parse_review_output(output: str, changed_lines: dict[str, set[int]]) -> tuple[str, list[dict[str, object]]]:
    """Parse and validate the structured response requested from OpenCode.

    Invalid location suggestions are included in the overall review body
    instead of being sent as inline comments that GitHub would reject.
    """
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    raw_json = match.group(1) if match else output.strip()
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise ValueError("OpenCode did not return the required JSON review format") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), str):
        raise ValueError("OpenCode review JSON must contain a string 'summary'")
    summary = payload["summary"].strip()
    if not summary:
        raise ValueError("OpenCode review summary must not be empty")

    raw_comments = payload.get("comments", [])
    if not isinstance(raw_comments, list):
        raise ValueError("OpenCode review JSON field 'comments' must be a list")

    comments: list[dict[str, object]] = []
    unlocated: list[str] = []
    for item in raw_comments:
        if not isinstance(item, dict):
            continue
        path, line, body = item.get("path"), item.get("line"), item.get("body")
        start_line, suggestion = item.get("start_line"), item.get("suggestion")
        has_valid_range = (
            start_line is None
            or (
                isinstance(start_line, int)
                and not isinstance(start_line, bool)
                and isinstance(line, int)
                and start_line < line
                and all(candidate in changed_lines.get(path, set()) for candidate in range(start_line, line + 1))
            )
        )
        if (
            isinstance(path, str)
            and isinstance(line, int)
            and not isinstance(line, bool)
            and isinstance(body, str)
            and body.strip()
            and line in changed_lines.get(path, set())
            and has_valid_range
        ):
            comment: dict[str, object] = {"path": path, "line": line, "side": "RIGHT", "body": body.strip()}
            if start_line is not None:
                comment["start_line"] = start_line
                comment["start_side"] = "RIGHT"
            if isinstance(suggestion, str) and suggestion.strip() and "```" not in suggestion:
                comment["body"] = suggestion_body(body.strip(), suggestion.strip())
            comments.append(comment)
        elif isinstance(body, str) and body.strip():
            location = f"`{path}:{line}`" if isinstance(path, str) and isinstance(line, int) else "an unavailable location"
            unlocated.append(f"- **Additional feedback ({location}):** {body.strip()}")

    if unlocated:
        summary = f"{summary}\n\n" + "\n".join(unlocated)
    return summary, comments


def main() -> int:
    """Run the configured OpenCode review and optionally publish its feedback.

    Inputs are supplied through command-line arguments and the ``GITHUB_TOKEN``
    environment variable.

    Returns:
        ``0`` when feedback is posted or already exists, otherwise a non-zero
        process exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="Diff or issue prompt file passed to OpenCode")
    parser.add_argument("--prompt", required=True, help="Review instruction passed to OpenCode")
    parser.add_argument("--agent-file", type=Path, required=True)
    parser.add_argument("--skill-file", type=Path, required=True)
    parser.add_argument("--comments-url", required=True, help="GitHub issue/PR comments API URL")
    parser.add_argument("--repository", help="owner/repository; enables an inline pull-request review")
    parser.add_argument("--pull-number", type=int, help="Pull request number for an inline review")
    parser.add_argument("--head-sha", help="Current pull request head SHA for an inline review")
    parser.add_argument("--feedback-kind", required=True, help="Stable identifier used to avoid duplicate comments")
    parser.add_argument("--author", required=True, help="Verified GitHub login of the issue or pull-request author")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN must be set")
    if not args.author.strip() or args.author.startswith("@"):
        parser.error("--author must be a non-empty GitHub login without a leading @")
    pr_arguments = (args.repository, args.pull_number, args.head_sha)
    if any(pr_arguments) and not all(pr_arguments):
        parser.error("--repository, --pull-number, and --head-sha must be supplied together")
    for path in (args.input, args.agent_file, args.skill_file):
        if not path.is_file():
            parser.error(f"Required file not found: {path}")

    try:
        agent_name = front_matter_name(args.agent_file)
        skill_name = front_matter_name(args.skill_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    marker = f"<!-- agentic-workflow:{args.feedback_kind}:v1 -->"
    reviews_url = ""
    marker_url = args.comments_url
    if args.repository:
        reviews_url = f"https://api.github.com/repos/{args.repository}/pulls/{args.pull_number}/reviews"
        marker_url = reviews_url
    if has_marker(marker_url, token, marker):
        print("Feedback from this workflow already exists; skipping.")
        return 0

    prompt = (
        f"{args.prompt}\n\n"
        f"Use the repository custom agent '{agent_name}' and skill '{skill_name}'. "
        f"The verified GitHub login of this contribution's author is '@{args.author}'. "
        "When referring to the author, use that exact handle; do not infer an author from issue or pull-request numbers. "
        "Return JSON only, with this exact shape: "
        '{"summary":"overall Markdown review", "comments":[{"path":"repository-relative path", "line":123, "body":"concise Markdown feedback", "suggestion":"exact replacement text"}]}. '
        "The summary is always published as the overall review comment. Add a comments item only for a changed new-file line visible in the supplied diff. "
        "Use an optional 'suggestion' only when you can provide an exact replacement; it becomes an apply-able GitHub suggested change. "
        "For a multi-line suggestion, also provide 'start_line' and ensure every line from start_line through line is a changed new-file line. "
        "Use no Markdown code fence and do not include 'side' or 'start_side' fields."
    )
    result = subprocess.run(
        ["opencode", "run", "--agent", agent_name, prompt, "--file", str(args.input)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    output = result.stdout.strip()
    if not output:
        print("OpenCode returned no feedback.", file=sys.stderr)
        return 1

    if reviews_url:
        try:
            summary, comments = parse_review_output(output, changed_lines_by_path(args.input.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 1
        github_request(
            reviews_url,
            token,
            method="POST",
            body={
                "commit_id": args.head_sha,
                "event": "COMMENT",
                "body": f"{marker}\n{summary}",
                "comments": comments,
            },
        )
        print(f"Posted agentic review with {len(comments)} inline comment(s).")
    else:
        github_request(args.comments_url, token, method="POST", body={"body": f"{marker}\n{output}"})
        print("Posted agentic feedback.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        print(f"GitHub API request failed: {error.code} {error.read().decode()}", file=sys.stderr)
        raise SystemExit(1)