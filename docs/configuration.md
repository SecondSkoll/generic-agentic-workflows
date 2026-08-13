# Configure agentic workflows

This guide configures the reusable PR-review and issue-feedback workflows.
Choose exactly one configuration source: `default`, `local`, or `central`.
Start every new configuration with `validate_only: true`, then `dry_run: true`.

For field-by-field definitions and complete bundle-file examples, see the
[configuration reference](configuration-reference.md).

## Prerequisites

1. Pin the reusable workflow in `uses:` to a reviewed commit SHA (or an
   organization-approved protected release tag).
2. Store `OPENROUTER_API_KEY` as an Actions secret. Restrict and rotate it.
3. Grant only the permissions for the selected workflow:

   | Workflow | Publication permissions |
   | --- | --- |
   | PR review | `contents: read`, `pull-requests: write` |
   | Issue feedback | `contents: read`, `issues: write` |

`validate_only` does not invoke a model or write to GitHub. `dry_run` can
invoke the model but cannot publish. The two modes are mutually exclusive.

## Pathway 1: supplied `default` profile

Use `default` when a supplied profile needs no repository-specific changes.
Because it is remote for a reusable call, `configuration_ref` is required and
must be a lowercase 40-character commit SHA.

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: default
      configuration_ref: 0123456789abcdef0123456789abcdef01234567
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Available supplied profiles are `documentation-review`, `issue-feedback`,
`default-implementation`, and `release-project-review` (the implementation and
release-project-review workflows are manual-dispatch/reusable only).

## Pathway 2: caller `local` bundle

Use `local` for repository-specific instructions. Store the bundle on the
protected default branch: the workflow resolves it from the caller's trusted
checkout. Local configuration does **not** use `configuration_ref`.

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: local
      configuration_profile: acme-docs-review
      focus: documentation
      max_comments: 5
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Create `.opencode/configuration/acme-docs-review/` in the caller repository:

```text
.opencode/configuration/acme-docs-review/
  bundle.json
  hashes.json
  agent.md
  prompts/review.md
  skills/documentation/SKILL.md
```

`bundle.json` declares the workflow, agent, skills, prompt, model profile,
output contract, and limits. `hashes.json` contains lowercase SHA-256 values
for every declared content file. Agent and skill Markdown files require YAML
front matter with `name`. Update hashes whenever content changes:

```text
python3 scripts/update_hashes.py --profile acme-docs-review
```

Use the supplied `.opencode/configuration/documentation-review/` bundle as the
working schema-v1 reference.

## Pathway 3: allowlisted `central` bundle

Use `central` when multiple repositories share organization-reviewed profiles.
The source alias—not a caller-provided URL—maps to a repository and root in
the workflow release. Pin the remote bundle with a full commit SHA.

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: central
      configuration_ref: 0123456789abcdef0123456789abcdef01234567
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      # Only for a private central repository; needs contents: read.
      central_config_token: ${{ secrets.CENTRAL_CONFIG_TOKEN }}
```

The current `central` alias resolves approved bundles in
`SecondSkoll/generic-agentic-workflows-config` under `.opencode/configuration`.
Its `configuration_ref` pins that repository and is independent of the SHA
that pins the reusable workflow in `uses:`.
See the copy-ready [central example](examples/configuration-sources/central/)
and the [central configuration guide](examples/central-configuration/README.md)
for its repository layout and release process.

## Common rules

- `configuration_profile` must match `[a-z0-9][a-z0-9-]{0,62}`.
- Remote sources (`default` and `central`) require an exact full commit SHA.
- A source failure, missing profile, stale hash, unsafe path, invalid
  front matter, or workflow mismatch stops the run; there is no fallback.
- Callers may set only typed inputs such as source, profile, focus, and bounded
  counts. Prompt text, paths, model IDs, URLs, and mutable refs are rejected.
- PR inline comments must target added diff lines; invalid locations become
  summary feedback instead of failing publication.

## Release project-review

The release project-review workflow (`opencode-release-project-review.yml`)
is manual/reusable only (`workflow_dispatch` and `workflow_call`); it never
runs automatically on a release event. It examines a published GitHub release
identified by exactly one of `release_id` or `release_tag` and creates at most
one `release-readiness` issue in the reviewed repository. The supplied
`release-project-review` profile is the default for direct runs and for
reusable calls that select `configuration_source: default`.

Target selection:

- `target_repository` is a strict canonical `owner/repo` (no URLs, ref
  syntax, paths, or expressions). It defaults to `${{ github.repository }}`.
- Exactly one of `release_id` (positive GitHub release ID) or `release_tag`
  (conservative tag grammar, resolved through the GitHub REST API and never
  used as a Git ref) is required.
- `focus` is an allowlisted release-management value only; it is not
  arbitrary prompt text. Allowed values: `release-notes`, `rollout`,
  `rollback`, `acceptance`, `dependencies`, `owners`, `risk`,
  `operational-readiness`, or `general`.

Cross-repository credentials:

- Same-repository runs use the job token.
- Cross-repository runs require an explicitly forwarded, separately scoped
  `release_target_token` (GitHub App installation token or fine-grained token)
  with `contents: read` and `issues: write` on the target repository only.
  Read access is verified before checkout; the same target-scoped token is
  used for issue creation. The caller's `GITHUB_TOKEN` is never assumed to
  have access outside its repository.

Roll out in order: `validate_only: true`, then `dry_run: true`, then normal
publication. The workflow checks out exactly the resolved immutable target
commit (read-only) and the workflow—not the model—owns the issue destination,
the `release-readiness` label allowlist, the idempotency marker, and
publication.

## Rollout and operations

After a successful validation run, replace `validate_only: true` with
`dry_run: true`, inspect the configuration, policy, provenance, and output,
then remove `dry_run`. Each run uploads redacted short-retention artifacts;
use them to diagnose failures and roll back by pinning a known-good workflow
or remote bundle SHA. See the [operations guide](operations/operations-guide.md).
