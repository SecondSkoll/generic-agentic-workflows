# generic-agentic-workflows

Reusable GitHub Actions workflows that use **read only** OpenCode agents
to provide pull-request documentation reviews, issue feedback, a manually
dispatched issue-to-pull-request implementation path, and a manually
dispatched/reusable release project-review that creates at most one
release-readiness issue.

Note: Issue implementation is not currently set up for calling as a remote action.
See [Run issue implementation](docs/developer/running-issue-implementation.md) for the
manual procedure.

## Start here

1. Add `OPENROUTER_API_KEY` as a repository Actions secret. Keep its scope,
   credit limit, and lifetime minimal.
2. Copy a wrapper from [the consumer examples](docs/how-to/examples/index.md), pin the
   reusable workflow to a reviewed revision, and grant only the required job
   permissions.
3. Select one configuration pathway: **default**, **local**, or **central**.
   The [configuration guide](docs/how-to/configuration.md) includes complete
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

Remote configuration is a hash-verified bundle. Caller-local bundles may omit
`hashes.json`; their declared content is still validated and hashed when
resolved. Callers cannot provide arbitrary prompt text, file paths, model
identifiers, URLs, branches, or tags. Remote sources require a 40-character
commit SHA; resolution failures fail closed.

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

### Tutorials

- [Set up your first workflow](docs/tutorial/set-up-your-first-workflow.md) —
  add and verify a safe validation-only documentation-review workflow.

### How-to guides

- [Deploy a workflow](docs/how-to/deploy-a-workflow.md) — step-by-step consumer
  setup for supplied, local, and central configuration sources.
- [Configuration guide](docs/how-to/configuration.md) — select and maintain a trusted
  configuration source.
- [Create a configuration bundle](docs/how-to/creating-a-configuration-bundle.md) —
  author, hash, validate, and publish a reviewed profile.
- [Run commands from a configuration bundle](docs/how-to/running-commands.md) —
  configure and roll out approved release-review preflight commands.
- [Consumer examples](docs/how-to/examples/index.md) — ready-to-copy wrappers,
  including complete `default`, `local`, and `central` source examples.

### Reference

- [Configuration reference](docs/reference/configuration-reference.md) — caller inputs,
  bundle layout, supplied profiles, and output contracts.
- [`bundle.json` reference](docs/reference/bundle-json-reference.md) — manifest fields,
  valid workflow mappings, and safety constraints.
- [Operations reference](docs/reference/operations-reference.md) — versions,
  provenance, markers, reliability limits, and failure modes.

### Explanation

- [Security model](docs/explanation/security-model.md) — trust boundaries, defensive
  controls, and their rationale.

### Developer documentation

- [Developer documentation](docs/developer/index.md)
- [Operations guide](docs/developer/operations-guide.md)
- [Run issue implementation](docs/developer/running-issue-implementation.md)

> OpenCode configuration (`.opencode/`) is loaded once at startup and is not
> hot-reloaded. After changing any configuration in `.opencode/`, quit and
> restart OpenCode for the change to take effect.

## Building the documentation

The documentation is built with the [Canonical Sphinx
Stack](https://github.com/canonical/sphinx-stack) (tag 2.0). The Sphinx
configuration lives in [`docs/conf.py`](docs/conf.py) and the entry point is
[`docs/index.md`](docs/index.md).

Use the Canonical Sphinx Stack Makefile from the repository root. It creates
`docs/.venv` and installs `docs/requirements.txt`. Read the Docs instead uses
uv to install the locked `docs` extra from `pyproject.toml`.

```
make -C docs install
```

Build the HTML documentation (treats warnings as errors and keeps going to
report all of them):

```
make -C docs html
```

Check external links:

```
make -C docs linkcheck
```
