# Deploy an agentic workflow

This guide deploys one of the reusable workflows in a repository that consumes
this project. It covers all supported configuration sources: the supplied
`default` profiles, repository-owned `local` bundles, and organization-managed
`central` bundles.

For the individual input definitions, see the [configuration reference](configuration-reference.md).
For authoring a local or central profile, see [creating a configuration bundle](creating-a-configuration-bundle.md).

## 1. Choose the workflow

| Workflow | Reusable workflow | Typical trigger | Required caller permissions |
| --- | --- | --- | --- |
| Documentation review | `opencode-documentation-review.yml` | `pull_request` | `contents: read`, `pull-requests: write` |
| Issue feedback | `opencode-issue-feedback.yml` | `issues` | `contents: read`, `issues: write` |
| Release project review | `opencode-release-project-review.yml` | `workflow_dispatch`, schedule, or a release wrapper | `contents: read`, `issues: write` |

The issue-implementation workflow is manual-dispatch only. It is not available
as a remote reusable call.

Use the copy-ready wrappers under [configuration-source examples](examples/configuration-sources/README.md)
when one matches the intended deployment.

## 2. Prepare credentials and permissions

1. Add `OPENROUTER_API_KEY` as an Actions secret in the consumer repository.
   Restrict its scope, credit limit, and lifetime.
2. Grant the calling job only the permissions in the table above. The caller,
   not the reusable workflow, owns its `permissions:` declaration.
3. If using a private central configuration repository, add a
   `CENTRAL_CONFIG_TOKEN` secret with read-only `contents: read` access to that
   repository.
4. For a cross-repository release review, provide a separate
   `release_target_token` scoped only to the reviewed target with
   `contents: read` and `issues: write`. Do not rely on the caller's
   `GITHUB_TOKEN` for another repository.

## 3. Select a configuration source

Choose exactly one source for a workflow invocation.

| Source | Use it when | `configuration_ref` | Bundle location |
| --- | --- | --- | --- |
| `default` | A supplied profile is sufficient | Required: reviewed lowercase 40-character commit SHA | This workflow repository |
| `local` | The consuming repository owns its instructions | Omit it | Consumer repository's trusted default-branch checkout |
| `central` | An organization shares approved profiles | Required: reviewed lowercase 40-character commit SHA | Allowlisted central repository |

A profile name must match `[a-z0-9][a-z0-9-]{0,62}`. Remote references are
immutable commit SHAs: branches, tags, URLs, and partial SHAs are rejected.
The remote bundle pin is independent of the commit SHA used to pin the
reusable workflow in `uses:`.

### Option A: supplied `default` profile

Choose `default` to use a supplied profile unchanged. Pin both the reusable
workflow and the supplied bundle revision to a reviewed commit SHA. The
profiles currently supplied are `documentation-review`, `issue-feedback`,
`default-implementation`, and `release-project-review`.

```yaml
jobs:
  review:
    uses: SecondSkoll/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: default
      configuration_ref: <same-reviewed-40-character-sha>
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

### Option B: repository-owned `local` bundle

Choose `local` when the repository needs its own reviewed instructions. Put
the bundle in `.opencode/configuration/<profile>/` on the protected default
branch. No `configuration_ref` is accepted or needed: the workflow reads the
trusted caller checkout rather than pull-request content.

```yaml
jobs:
  review:
    uses: SecondSkoll/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: local
      configuration_profile: team-docs-review
      focus: documentation
      max_comments: 5
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Create and validate the bundle before enabling the wrapper. The [bundle
authoring guide](creating-a-configuration-bundle.md) shows the required
layout and maintenance process.

### Option C: organization-managed `central` bundle

Choose `central` for a profile reviewed once and used by multiple repositories.
The source is an alias to the fixed allowlisted repository
`SecondSkoll/generic-agentic-workflows-config`; callers cannot provide a
repository name or URL. Pin its bundle commit independently.

```yaml
jobs:
  review:
    uses: SecondSkoll/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: central
      configuration_ref: <reviewed-central-40-character-sha>
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      central_config_token: ${{ secrets.CENTRAL_CONFIG_TOKEN }} # Required only if central is private
```

## 4. Add a wrapper workflow

Create a workflow under `.github/workflows/` in the consumer repository. The
wrapper owns event filtering, concurrency, permissions, and secret forwarding;
the reusable workflow owns configuration validation, model execution, output
validation, publication, and provenance artifacts.

For example, a documentation-review wrapper can use `pull_request` and call
the `default` example above. For an issue-feedback wrapper, replace the
reusable workflow path with `opencode-issue-feedback.yml`, grant
`issues: write`, and use `max_issues` instead of `max_comments`.

Do not expose prompt text, agent or skill paths, model IDs, URLs, repository
names, branches, tags, or mutable references as wrapper inputs. The reusable
interface intentionally accepts only typed, bounded selectors.

## 5. Roll out safely

Promote the wrapper through these stages:

1. Commit it with `validate_only: true`. This resolves the invocation, bundle,
   and effective policy and validates them—including any `midflight_commands`
   IDs, counts, phases, and overlap—without calling a model, fetching a
   release, checking out a target, running commands, or writing to GitHub.
2. Review the run summary and redacted configuration/provenance artifacts.
3. Replace `validate_only: true` with `dry_run: true`. This can invoke the
   model (both phases when `midflight_commands` is configured), run the
   configured commands, and validate its output, but it does not publish
   feedback.
4. Inspect the generated result and artifacts.
5. Remove `dry_run` to allow publication.

`validate_only` and `dry_run` are mutually exclusive. At any stage, return to
a known-good workflow or remote-bundle SHA to roll back explicitly. For
release review, `midflight_commands` ships disabled; opt in a single low-risk
command only after dry-run evaluation, and roll back by emptying
`midflight_commands` or pinning the last known-good bundle SHA. Never fall back
automatically to an unisolated executor or a one-stage model decision after a
midflight failure.

## 6. Verify ongoing operation

Each run uploads short-retention (14-day) redacted artifacts, including the
resolved configuration, effective policy, and provenance record. Use them to
confirm the selected profile, remote SHA, output contract, model profile, and
result without exposing credentials, full prompts, raw model output, or full
untrusted content. See the [operations guide](operations/operations-guide.md)
for rollback, incident response, and reliability limits.
