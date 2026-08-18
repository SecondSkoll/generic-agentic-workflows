# Developer documentation

Developer documentation is for contributors who maintain Generic Agentic
Workflows itself. It records implementation decisions and safe procedures for
changing workflow-owned capabilities. For deploying or operating the workflows
in another repository, use the [how-to guides](../how-to/index.md) instead.

## Maintainer guides

- [Add a preflight or midflight command](adding-a-command.md) — safely add a
  reviewed command capability to the release-project-review workflow.

## Operations

- [Operate agentic workflows](operations-guide.md) — release, recover, roll
  back, and safely roll out workflows.
- [Run issue implementation](running-issue-implementation.md) — manually
  request an initial implementation for an open issue.

```{toctree}
:maxdepth: 1

adding-a-command
operations-guide
running-issue-implementation
```