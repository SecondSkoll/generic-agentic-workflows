---
name: implementation-guardrails
description: Decision fields and handoff boundaries for issue implementation
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: github
---

# Implementation guardrails skill

Provide the canonical decision and blocker fields and the handoff boundaries
used by the issue-implementation workflow.

## Decision output

When a small, safe first pass is possible, return:

```
IMPLEMENTATION_DECISION: IMPLEMENT
```

When the issue is ambiguous, unsafe, out of scope, or needs a maintainer
decision, return exactly these two single-line fields:

```
IMPLEMENTATION_DECISION: BLOCKED
IMPLEMENTATION_BLOCKER: <specific missing information or decision needed>
```

## Prohibitions

Do not edit GitHub Actions workflows, repository automation, dependency
lockfiles, or agent/skill instructions. Do not install packages, run
destructive commands, commit, push, create a pull request, or comment on the
issue. Keep all investigation limited to files needed to assess the issue.
