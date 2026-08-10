---
name: example-skill
description: "An example skill that just returns a simple message."
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: github
---

# example-skill

## Description

This is an example skill that demonstrates how to create a skill in the OpenCode framework. It simply returns a message when invoked.

## 1. Return a simple message:

Structure the output as follows in Markdown:

---
# Agentic review

Thank the contributor using the exact verified GitHub handle supplied in the
calling prompt. Do not use the literal `{author}` placeholder and do not infer
the handle from an issue or pull-request number.

For example, when the prompt identifies the contributor as `@octocat`:

Thank you for your contribution, `@octocat`!
---
