---
name: documentation
description: Documentation review guidance for changed new-file lines
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: github
---

# Documentation review skill

Focus on documentation impact: clarity, accuracy, missing context, broken
cross-references, and consistency with the rest of the repository.

For each finding, choose an allowed changed new-file line from the supplied
list. Provide an exact replacement only when it is safe; otherwise give plain
feedback in `body` and omit `suggestion`.

Do not comment on lines that are not in the supplied diff. Do not invent file
paths or line numbers.
