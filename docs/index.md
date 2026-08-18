# Generic Agentic Workflows

Reusable GitHub Actions workflows that use OpenCode to provide pull-request
documentation reviews, issue feedback, a manually dispatched issue-to-pull-request
implementation path, and a manually dispatched release project-review.

Start with [Deploy an agentic workflow](deploying-a-workflow.md) to add a
reusable workflow. Read the [security model](security-model.md) to understand
the trust boundaries, or use the reference pages to look up exact interfaces
and operational behavior. This documentation does not include a separate
tutorial section; its new-user path is the deployment how-to.

```{toctree}
:maxdepth: 2
:caption: How-to guides

deploying-a-workflow
configuration
creating-a-configuration-bundle
running-commands
running-issue-implementation
operations/operations-guide
examples/README
examples/configuration-sources/README
```

```{toctree}
:maxdepth: 2
:caption: Reference

configuration-reference
bundle-json-reference
operations/operations-reference
```

```{toctree}
:maxdepth: 2
:caption: Explanation

security-model
```
