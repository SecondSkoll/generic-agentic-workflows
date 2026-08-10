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

For issue feedback, structure the output as follows in Markdown:

---
# Agentic review

Thank the contributor using the exact verified GitHub handle supplied in the
calling prompt. Do not use the literal `{author}` placeholder and do not infer
the handle from an issue or pull-request number.

For example, when the prompt identifies the contributor as `@octocat`:

Thank you for your contribution, `@octocat`!
---

## Pull-request review output

When the calling prompt requires JSON, return JSON only—no Markdown code fence
or surrounding prose. Use this shape:

```json
{
  "summary": "Thank the contributor and provide the overall Markdown review.",
  "comments": [
    {
      "path": "README.md",
      "line": 42,
      "body": "Explain why this change is needed.",
      "suggestion": "Replacement text that a reviewer can apply."
    }
  ]
}
```

Only include a `comments` item when its `path` and new-file `line` identify a
changed line in the supplied diff. Put feedback that cannot be tied to a
changed line in `summary` instead.

Include `suggestion` only when you can safely provide the exact replacement.
The runner turns it into a GitHub `suggestion` block so the reviewer can apply
it from the PR. Keep the explanation in `body`; place only replacement text in
`suggestion`. For a multi-line replacement, include `start_line` as well as
`line`, and make sure every line in that inclusive range is a changed new-file
line in the supplied diff.
