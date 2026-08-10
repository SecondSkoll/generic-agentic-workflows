#!/usr/bin/env python3
"""Run an OpenCode review using repository-provided agent customisations.

The script is intentionally dependency-free so it can run in a GitHub Actions
runner.  It validates the selected agent and skill files, asks OpenCode for
feedback, and posts at most one marked comment for each feedback type.
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
    """Return the required `name` from a Markdown file's YAML front matter."""
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


def already_commented(comments_url: str, token: str, marker: str) -> bool:
    parsed_url = urllib.parse.urlparse(comments_url)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="Diff or issue prompt file passed to OpenCode")
    parser.add_argument("--prompt", required=True, help="Review instruction passed to OpenCode")
    parser.add_argument("--agent-file", type=Path, required=True)
    parser.add_argument("--skill-file", type=Path, required=True)
    parser.add_argument("--comments-url", required=True, help="GitHub issue/PR comments API URL")
    parser.add_argument("--feedback-kind", required=True, help="Stable identifier used to avoid duplicate comments")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN must be set")
    for path in (args.input, args.agent_file, args.skill_file):
        if not path.is_file():
            parser.error(f"Required file not found: {path}")

    try:
        agent_name = front_matter_name(args.agent_file)
        skill_name = front_matter_name(args.skill_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    marker = f"<!-- agentic-workflow:{args.feedback_kind}:v1 -->"
    if already_commented(args.comments_url, token, marker):
        print("Feedback from this workflow already exists; skipping.")
        return 0

    prompt = (
        f"{args.prompt}\n\n"
        f"Use the repository custom agent '{agent_name}' and skill '{skill_name}'. "
        "Return concise, actionable Markdown feedback only."
    )
    result = subprocess.run(
        ["opencode", "run", "--agent", agent_name, "-f", str(args.input), prompt],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    review = result.stdout.strip()
    if not review:
        print("OpenCode returned no feedback.", file=sys.stderr)
        return 1

    github_request(args.comments_url, token, method="POST", body={"body": f"{marker}\n{review}"})
    print("Posted agentic feedback.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        print(f"GitHub API request failed: {error.code} {error.read().decode()}", file=sys.stderr)
        raise SystemExit(1)