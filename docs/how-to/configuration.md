# Configure agentic workflows

This guide helps maintainers select and maintain a trusted configuration source
for an agentic workflow.
Choose exactly one configuration source: `default`, `local`, or `central`.
Start every new configuration with `validate_only: true`, then `dry_run: true`.

For field-by-field definitions and complete bundle-file examples, see the
[configuration reference](../reference/configuration-reference.md).

## Prerequisites

1. Pin the reusable workflow in `uses:` to a reviewed commit SHA (or an
   organization-approved protected release tag).
2. Store `OPENROUTER_API_KEY` as an Actions secret. Restrict and rotate it.
3. Grant only the permissions for the selected workflow:

   | Workflow | Publication permissions |
   | --- | --- |
   | PR review | `contents: read`, `issues: read`, `pull-requests: write` |
   | Issue feedback | `contents: read`, `issues: write` |

`validate_only` does not invoke a model or write to GitHub. `dry_run` can
invoke the model but cannot publish. The two modes are mutually exclusive.
For a dry run that generates feedback, download the workflow's
short-retention `resolved-invocation-*` artifact. Its
`agentic-publication-preview*.json` file contains the validated payload that
would have been posted: the review body and inline comments, an issue comment,
or a release-readiness issue. No preview file is written for `validate_only`,
model/contract failures, or a release review that decides `NO_ISSUE`.

## Pathway 1: supplied `default` profile

Use `default` when a supplied profile needs no repository-specific changes.
Because it is remote for a reusable call, `configuration_ref` is required and
must be a lowercase 40-character commit SHA.

Use [Option A in the deployment guide](deploy-a-workflow.md#option-a-supplied-default-profile)
for the canonical wrapper YAML and end-to-end deployment steps.

Available supplied profiles are `documentation-review`, `issue-feedback`,
`default-implementation`, and `release-project-review` (the implementation and
release-project-review workflows are manual-dispatch/reusable only).

## Pathway 2: caller `local` bundle

Use `local` for repository-specific instructions. Store the bundle on the
protected default branch: the workflow resolves it from the caller's trusted
checkout. Local configuration does **not** use `configuration_ref`.

Use [Option B in the deployment guide](deploy-a-workflow.md#option-b-repository-owned-local-bundle)
for the canonical wrapper YAML. Then create and maintain the bundle described
below.

The bundle layout, authoring, and hashing procedure is in [Create a
configuration bundle](creating-a-configuration-bundle.md); field and hash rules
are in the [`bundle.json` reference](../reference/bundle-json-reference.md).

## Pathway 3: allowlisted `central` bundle

Use `central` when multiple repositories share organization-reviewed profiles.
The source alias—not a caller-provided URL—maps to a repository and root in
the workflow release. Pin the remote bundle with a full commit SHA.

Use [Option C in the deployment guide](deploy-a-workflow.md#option-c-organization-managed-central-bundle)
for the canonical wrapper YAML and token requirements.

The current `central` alias resolves approved bundles in
`SecondSkoll/generic-agentic-workflows-config` under `.opencode/configuration`.
Its `configuration_ref` pins that repository and is independent of the SHA
that pins the reusable workflow in `uses:`. See the copy-ready
[central example](examples/configuration-sources/central/) and the
[bundle authoring guide](creating-a-configuration-bundle.md) for its
repository layout and release process.

## Common rules

- `configuration_profile` must match `[a-z0-9][a-z0-9-]{0,62}`.
- Remote sources (`default` and `central`) require an exact full commit SHA.
- A source failure, missing profile, stale hash, unsafe path, invalid
  front matter, or workflow mismatch stops the run; there is no fallback.
- Callers may set only typed inputs such as source, profile, focus, and bounded
  counts. Prompt text, paths, model IDs, URLs, and mutable refs are rejected.
- PR inline comments must target added diff lines; invalid locations become
  summary feedback instead of failing publication.
- For suggested changes, the workflow checks the current content of the
  addressed new-file range. It demotes entirely blank ranges and replacements
  identical to the current range to summary feedback rather than publishing
  them as apply-able suggestions.

## Release project-review

The release project-review workflow (`opencode-release-project-review.yml`)
is manual/reusable only (`workflow_dispatch` and `workflow_call`); it never
runs automatically on a release event. It examines a published GitHub release
identified by exactly one of `release_id` or `release_tag` and creates at most
one `release-readiness` issue in the reviewed repository. The supplied
`release-project-review` profile is the default for direct runs and for
reusable calls that select `configuration_source: default`.

Select a canonical target repository, exactly one release selector, and an
optional allowlisted focus as described under [Caller
inputs](../reference/configuration-reference.md#caller-inputs).

Cross-repository runs require a target-scoped `release_target_token` with
`contents: read` and `issues: write`; the caller's `GITHUB_TOKEN` is never
assumed to have cross-repository access. See the `release_target_token` row in
[Caller inputs](../reference/configuration-reference.md#caller-inputs).

Roll out in order: `validate_only: true`, then `dry_run: true`, then normal
publication. The workflow checks out exactly the resolved immutable target
commit (read-only) and the workflow—not the model—owns the issue destination,
the `release-readiness` label allowlist, the idempotency marker, and
publication.

To configure and roll out preflight or midflight commands, see [Run commands
from a configuration bundle](running-commands.md).

## Rollout and operations

Follow [Roll out safely](deploy-a-workflow.md#roll-out-safely) to promote a
configuration through validation and dry run. For incident response and
rollback, use the [operations guide](../developer/operations-guide.md).
