# generic-agentic-workflows

Reusable GitHub Actions workflows that use OpenCode to provide pull-request
documentation reviews, issue feedback, a manually dispatched
issue-to-pull-request implementation path, and a manually dispatched/reusable
release project-review that creates at most one release-readiness issue.

Note: Issue implementation is not currently set up for calling as a remote action.

## Start here

1. Add `OPENROUTER_API_KEY` as a repository Actions secret. Keep its scope,
   credit limit, and lifetime minimal.
2. Copy a wrapper from [`docs/examples/`](docs/examples/README.md), pin the
   reusable workflow to a reviewed revision, and grant only the required job
   permissions.
3. Select one configuration pathway: **default**, **local**, or **central**.
   The [configuration guide](docs/configuration.md) includes complete
   SHA-pinned examples for the review, feedback, and release-project-review
   workflows.
4. Roll out in order: `validate_only: true`, then `dry_run: true`, then normal
   publication.

## Configuration pathways

| Source | Best for | Trust boundary |
| --- | --- | --- |
| `default` | Using a supplied profile unchanged | This repository at a full pinned commit SHA. |
| `local` | Repository-specific instructions | The caller repository's trusted checkout. |
| `central` | Shared organization profiles | An allowlisted central repository at a full pinned commit SHA. |

`default` resolves profiles from this repository. `central` resolves profiles
from the separate allowlisted `SecondSkoll/generic-agentic-workflows-config`
repository, so each source is pinned independently.

All configuration is a hash-verified bundle. Callers cannot provide arbitrary
prompt text, file paths, model identifiers, URLs, branches, or tags. Remote
sources require a 40-character commit SHA; resolution failures fail closed.

## Workflows

| Workflow | Interface | Required publication permissions |
| --- | --- | --- |
| `.github/workflows/opencode-documentation-review.yml` | Direct PR trigger or reusable call | `contents: read`, `pull-requests: write` |
| `.github/workflows/opencode-issue-feedback.yml` | Direct issue trigger or reusable call | `contents: read`, `issues: write` |
| `.github/workflows/opencode-issue-implementation.yml` | Manual dispatch only | `contents: write`, `issues: write`, `pull-requests: write` |
| `.github/workflows/opencode-release-project-review.yml` | Manual dispatch or reusable call only | `contents: read`, `issues: write` (plus a target-scoped `release_target_token` for cross-repository reviews) |

The reusable caller owns triggers, concurrency, permissions, and secret
forwarding. The callee validates configuration, composes prompts, invokes the
model, validates output, publishes feedback, and uploads redacted provenance.

The release project-review workflow examines a published GitHub release
identified by a release ID or tag and creates at most one `release-readiness`
issue in the reviewed repository. It is manual/reusable only; it never runs
automatically on a release event. The supplied `release-project-review`
profile is the default for direct runs and for reusable calls that select
`configuration_source: default`. For cross-repository reviews, forward a
target-scoped token (`contents: read`, `issues: write` on the target only) as
`release_target_token`; the caller's `GITHUB_TOKEN` is never assumed to have
access outside its repository.

## Safety model

- PR feedback uses a trusted base checkout with `pull_request_target`; it does
  not check out or execute untrusted PR code.
- Issue, PR, and release text is explicitly delimited as untrusted data.
- Policy layers can only narrow capabilities and limits.
- Output must satisfy a versioned contract before GitHub publication.
- Provenance artifacts record safe hashes and metadata, never credentials,
  complete prompts, diffs, release bodies, or raw model responses.
- The implementation workflow blocks edits to workflows, automation,
  dependencies, and agent/configuration instructions before it can push.
- The release project-review workflow checks out exactly the resolved
  immutable target commit (read-only), collects a bounded allowlisted release
  context, and the workflow (not the model) owns the issue destination,
  labels, idempotency marker, and publication.

## Further documentation

- [Deploy a workflow](docs/deploying-a-workflow.md) — step-by-step consumer
  setup for supplied, local, and central configuration sources.
- [Create a configuration bundle](docs/creating-a-configuration-bundle.md) —
  author, hash, validate, and publish a reviewed profile.
- [`bundle.json` reference](docs/bundle-json-reference.md) — manifest fields,
  valid workflow mappings, and safety constraints.
- [Security model](docs/security-model.md) — trust boundaries, defensive
  controls, and their rationale.
- [Configuration guide](docs/configuration.md) — setup, all pathways, bundle
  layout, and operational safeguards.
- [Configuration reference](docs/configuration-reference.md) — every caller
  input and bundle field, with examples.
- [Consumer examples](docs/examples/README.md) — ready-to-copy wrappers,
  including complete `default`, `local`, and `central` source examples.
- [Operations guide](docs/operations/operations-guide.md) — rollback,
  reliability controls, and test matrix.

> OpenCode configuration (`.opencode/`) is loaded once at startup and is not
> hot-reloaded. After changing any configuration in `.opencode/`, quit and
> restart OpenCode for the change to take effect.
