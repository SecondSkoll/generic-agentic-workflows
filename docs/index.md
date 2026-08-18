# Generic Agentic Workflows

Generic Agentic Workflows provides reusable GitHub Actions workflows that use
read-only OpenCode agents for pull-request documentation reviews, issue
feedback, a manually dispatched issue-to-pull-request implementation path, and
a manually dispatched or reusable release project review.

The reusable caller owns triggers, concurrency, permissions, and secret
forwarding. The called workflow validates configuration, composes prompts,
invokes the model when required, validates its output, publishes bounded
feedback, and uploads redacted provenance.

## Available workflows

| Workflow | Interface | Required publication permissions |
| --- | --- | --- |
| Documentation review | Direct pull-request trigger or reusable call | `contents: read`, `pull-requests: write` |
| Issue feedback | Direct issue trigger or reusable call | `contents: read`, `issues: write` |
| Issue implementation | Manual dispatch only | `contents: write`, `issues: write`, `pull-requests: write` |
| Release project review | Manual dispatch or reusable call | `contents: read`, `issues: write` |

Configuration comes from one of three trusted sources: a supplied `default`
profile, a repository-owned `local` bundle, or an organization-managed
`central` bundle. Remote sources require immutable commit pins. Start new
deployments in validation-only mode before allowing model invocation or
publication.

## Documentation map

- **Learning → [Tutorials](tutorial/index.md):** complete a guided first
  validation-only deployment and verify its result.
- **Tasks → [How-to guides](how-to/index.md):** deploy, configure, operate, and
  maintain workflows and configuration bundles.
- **Lookup → [Reference](reference/index.md):** find exact inputs, manifest
  fields, versions, limits, and operational behavior.
- **Understanding → [Explanation](explanation/index.md):** understand the trust
  boundaries and the reasons for the defensive controls.
- **Maintaining → [Developer documentation](developer/index.md):** understand
  implementation plans and maintain the workflow code safely.

```{toctree}
:maxdepth: 2
:caption: Tutorials

tutorial/index
```

```{toctree}
:maxdepth: 2
:caption: How-to guides

how-to/index
```

```{toctree}
:maxdepth: 2
:caption: Reference

reference/index
```

```{toctree}
:maxdepth: 2
:caption: Explanation

explanation/index
```

```{toctree}
:maxdepth: 2
:caption: Developer documentation

developer/index
```
