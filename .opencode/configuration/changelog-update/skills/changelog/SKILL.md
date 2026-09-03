---
name: changelog
description: Decision fields and editing boundaries for changelog updates
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: github
---

# Changelog guardrails skill

Provide the canonical decision fields and editing boundaries used by the
pr-changelog-update workflow.

## Decision output

When a changelog entry is warranted and can be written safely, return:

```
CHANGELOG_DECISION: UPDATED
CHANGELOG_SUMMARY: <short summary of the changelog entry added>
```

When the pull request does not warrant a changelog entry, return:

```
CHANGELOG_DECISION: NO_CHANGE
CHANGELOG_SUMMARY: <short summary of why no entry is needed>
```

When the request is ambiguous, unsafe, out of scope, or needs a maintainer
decision, return exactly these two single-line fields:

```
CHANGELOG_DECISION: BLOCKED
CHANGELOG_BLOCKER: <specific missing information or decision needed>
```

## Editing boundaries

Edit only the designated target file declared in the prompt. Preserve the
existing changelog format, heading style, and ordering. Do not create, rename,
or delete other files. Do not modify unrelated content in the target file beyond
the new changelog entry.

## Prohibitions

Do not edit GitHub Actions workflows, repository automation, dependency
lockfiles, or agent/skill instructions. Do not install packages, run commands,
commit, push, open or comment on a pull request, or contact external services.
