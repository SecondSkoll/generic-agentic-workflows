# Deploy an agentic workflow

This guide deploys one of the reusable workflows in a repository that consumes
this project. It covers all supported configuration sources: the supplied
`default` profiles, repository-owned `local` bundles, and organization-managed
`central` bundles.

For the individual input definitions, see the [configuration reference](../reference/configuration-reference.md).
For authoring a local or central profile, see [creating a configuration bundle](creating-a-configuration-bundle.md).

## 1. Choose the workflow

| Workflow | Reusable workflow | Typical trigger | Required caller permissions |
| --- | --- | --- | --- |
| Documentation review | `opencode-documentation-review.yml` | `pull_request` | `contents: read`, `issues: read`, `pull-requests: write` |
| Issue feedback | `opencode-issue-feedback.yml` | `issues` | `contents: read`, `issues: write` |
| Release project review | `opencode-release-project-review.yml` | `workflow_dispatch`, schedule, or a release wrapper | `contents: read`, `issues: write` |
| Changelog update | `opencode-changelog-update.yml` | `pull_request_target` with `types: [labeled]` | `contents: write`, `pull-requests: write` |

The issue-implementation workflow is manual-dispatch only. It is not available
as a remote reusable call. To use it in this repository, follow [Run issue
implementation](../developer/running-issue-implementation.md).

Use the copy-ready wrappers under [configuration-source examples](examples/configuration-sources/index.md)
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
      issues: read
      pull-requests: write
    with:
      configuration_source: default
      configuration_ref: <same-reviewed-40-character-sha>
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      review_request_string: AI REVIEW REQUESTED
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
      issues: read
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
      issues: read
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

`review_request_string` is optional and defaults to `AI REVIEW REQUESTED`.
Change it when the consumer repository uses a different case-sensitive phrase
to request another review after matching feedback has already been published.

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
   For changelog update, validation-only is narrower: it validates invocation
   inputs and stops before fetching the PR or resolving the bundle.
2. Review the run summary and redacted configuration/provenance artifacts.
3. Replace `validate_only: true` with `dry_run: true`. This can invoke the
   model (both phases when `midflight_commands` is configured), run the
   configured commands, and validate its output, but it does not publish
   feedback.
4. Inspect the generated result and artifacts.
5. Remove `dry_run` to allow publication.

`validate_only` and `dry_run` are mutually exclusive. At any stage, return to
a known-good workflow or remote-bundle SHA to roll back explicitly. For command
and midflight rollout and rollback behavior, see [Run commands from a
configuration bundle](running-commands.md) and the [operations
guide](../developer/operations-guide.md).

## 6. Verify ongoing operation

Each run uploads its available short-retention (14-day) redacted artifacts.
Configuration and policy files are absent when the run stops before those
stages, including changelog `validate_only`. Use the available files to confirm
the selected profile, remote SHA, output contract, model profile, and result
without exposing credentials, full prompts, raw model output, or full untrusted
content. See the [operations guide](../developer/operations-guide.md) for
rollback, incident response, and reliability limits.

## 7. Changelog update from a labelled pull request

The `opencode-changelog-update.yml` reusable workflow updates a designated
changelog file from an open pull request when a configurable label is added.
A consumer caller uses `pull_request_target: types: [labeled]`. Unlike
`pull_request`, this event makes the base repository's token and configured
secret available for fork PRs, which is necessary to publish the proposed
content as a comment. The workflow remains safe because it checks out the
trusted base, loads helper code from the pinned reusable-workflow revision,
and treats the PR head as data; it never checks out or executes PR-controlled
code or configuration.

The caller supports these changelog-specific defaults and selectors:

| Input | Default | Behavior |
| --- | --- | --- |
| `label` | `update-changelog` | Exact label required on the `labeled` event. |
| `target_file` | `CHANGELOG.md` | Repository-relative file that the agent may edit. |
| `configuration_source` | `default` | `default`, `local`, or the allowlisted `central` source. |
| `configuration_ref` | Empty | Required full commit SHA for `default` and `central`; omit for `local`. |
| `configuration_profile` | `changelog-update` | Bundle that selects the reviewed prompt, agent, skills, model profile, contract, and policy. Use a custom reviewed local or central profile to customize that combination. |
| `pull_number` | `0` | Optional override when event context does not provide a PR number; the labelled-PR caller normally supplies the event number. |
| `dry_run` | `false` | Runs the guard, model, and output validation and creates a preview artifact, but does not commit or comment. |
| `validate_only` | `false` | Validates invocation inputs only, without fetching the PR, resolving the bundle, invoking the model, or publishing. |

The job requires `contents: write` and `pull-requests: write`, plus the required
`OPENROUTER_API_KEY` secret. Forward the optional `central_config_token` only
when a private central bundle needs it. See
`docs/how-to/examples/configuration-sources/default/.github/workflows/changelog-update.yml`
for a minimal caller. Its all-zero SHA values are deliberately invalid: the
example is not runnable until both are replaced with a reviewed 40-character
commit SHA.

The reusable workflow re-validates the event, action, exact label, PR number,
and open PR state authoritatively. Ordinary issues, other event actions,
closed PRs, and label mismatches are rejected before model invocation or
publication; the reusable workflow has no direct event trigger of its own.

* **Same-repo PRs**: the workflow commits only the designated target file to
  the PR source branch through the GitHub Contents API, with the workflow
  marker in the commit message. Any extraneous edit fails before publication.
* **Fork PRs**: the workflow never pushes. It posts (or updates) a single
  marker PR comment containing the proposed target-file content for review.
* **Re-adding the label**: the workflow exits safely when, for a same-repo PR,
  its marker commit is the latest commit with no newer comment, or, for a fork
  PR, its marker comment is the newest comment and no newer fork commit
  followed it. Otherwise, it incorporates the current PR head: same-repo PRs
  receive another target-file commit and fork PRs regenerate the proposal and
  update the existing marker comment in place. A newer fork commit after the
  marker comment triggers a regeneration that PATCHes the existing marker
  comment.
* `dry_run` produces a publication preview only; `dry_run` and `validate_only`
  are mutually exclusive.
