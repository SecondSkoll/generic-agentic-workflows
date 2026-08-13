# Configure agentic workflows

This guide configures the reusable PR-review and issue-feedback workflows.
Choose exactly one configuration source: `default`, `local`, or `central`.
Start every new configuration with `validate_only: true`, then `dry_run: true`.

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
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<reviewed-workflow-sha>
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

Available supplied profiles are `documentation-review`, `issue-feedback`, and
`default-implementation` (the implementation workflow is manual-dispatch-only).

## Pathway 2: caller `local` bundle

Use `local` for repository-specific instructions. Store the bundle on the
protected default branch: the workflow resolves it from the caller's trusted
checkout. Local configuration does **not** use `configuration_ref`.

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<reviewed-workflow-sha>
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
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<reviewed-workflow-sha>
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

See the [central configuration example](examples/central-configuration/README.md)
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

## Rollout and operations

After a successful validation run, replace `validate_only: true` with
`dry_run: true`, inspect the configuration, policy, provenance, and output,
then remove `dry_run`. Each run uploads redacted short-retention artifacts;
use them to diagnose failures and roll back by pinning a known-good workflow
or remote bundle SHA. See the [operations guide](operations/operations-guide.md).
