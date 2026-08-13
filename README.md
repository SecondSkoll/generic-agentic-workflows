# generic-agentic-workflows

Reusable GitHub Actions workflows that use OpenCode to provide pull-request
documentation reviews, issue feedback, and a manually dispatched,
issue-to-pull-request implementation path.

## Start here

1. Add `OPENROUTER_API_KEY` as a repository Actions secret. Keep its scope,
   credit limit, and lifetime minimal.
2. Copy a wrapper from [`docs/examples/`](docs/examples/README.md), pin the
   reusable workflow to a reviewed revision, and grant only the required job
   permissions.
3. Select one configuration pathway: **default**, **local**, or **central**.
   The [configuration guide](docs/configuration.md) includes complete
   SHA-pinned examples for the review and feedback workflows.
4. Roll out in order: `validate_only: true`, then `dry_run: true`, then normal
   publication.

## Configuration pathways

| Source | Best for | Trust boundary |
| --- | --- | --- |
| `default` | Using a supplied profile unchanged | This repository at a full pinned commit SHA. |
| `local` | Repository-specific instructions | The caller repository's trusted checkout. |
| `central` | Shared organization profiles | An allowlisted central repository at a full pinned commit SHA. |

Note: `default` and `central` currently point to this repository. `central` should eventually be aimed elswhere to provide a shared, and more agile source or profiles.

All configuration is a hash-verified bundle. Callers cannot provide arbitrary
prompt text, file paths, model identifiers, URLs, branches, or tags. Remote
sources require a 40-character commit SHA; resolution failures fail closed.

## Workflows

| Workflow | Interface | Required publication permissions |
| --- | --- | --- |
| `.github/workflows/opencode-documentation-review.yml` | Direct PR trigger or reusable call | `contents: read`, `pull-requests: write` |
| `.github/workflows/opencode-issue-feedback.yml` | Direct issue trigger or reusable call | `contents: read`, `issues: write` |
| `.github/workflows/opencode-issue-implementation.yml` | Manual dispatch only | `contents: write`, `issues: write`, `pull-requests: write` |

The reusable caller owns triggers, concurrency, permissions, and secret
forwarding. The callee validates configuration, composes prompts, invokes the
model, validates output, publishes feedback, and uploads redacted provenance.

## Safety model

- PR feedback uses a trusted base checkout with `pull_request_target`; it does
  not check out or execute untrusted PR code.
- Issue and PR text is explicitly delimited as untrusted data.
- Policy layers can only narrow capabilities and limits.
- Output must satisfy a versioned contract before GitHub publication.
- Provenance artifacts record safe hashes and metadata, never credentials,
  complete prompts, diffs, or raw model responses.
- The implementation workflow blocks edits to workflows, automation,
  dependencies, and agent/configuration instructions before it can push.

## Further documentation

- [Configuration guide](docs/configuration.md) — setup, all pathways, bundle
  layout, and operational safeguards.
- [Configuration reference](docs/configuration-reference.md) — every caller
  input and bundle field, with examples.
- [Consumer examples](docs/examples/README.md) — ready-to-copy wrappers,
  including complete `default`, `local`, and `central` source examples.
- [Central configuration example](docs/examples/central-configuration/README.md)
  — remote bundle layout and release process.
- [Operations guide](docs/operations/operations-guide.md) — rollback,
  reliability controls, and test matrix.
