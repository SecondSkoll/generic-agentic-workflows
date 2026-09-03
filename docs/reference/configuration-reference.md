# Configuration reference

This reference describes caller inputs, bundle layout, supplied profiles,
the `hashes.json` overview, and output contracts for reusable pull-request
review, issue-feedback, release-project-review, and changelog-update workflows. Remote bundles
are hash-verified; caller-local bundles may omit the optional hash lock. For
setup and rollout instructions, see the [configuration
guide](../how-to/configuration.md).

Configuration has two layers:

1. **Caller inputs** in the `with:` section choose a trusted configuration
   bundle and set bounded run options.
2. **Bundle files** select reviewed instructions, a model profile, an output
   contract, and policy limits. They are maintained by the bundle owner, not
   supplied by the caller at run time.

## Caller inputs

The following fields are available to callers of the reusable workflows.
Fields not applicable to the selected workflow are rejected.

| Field | Type | Applies to | Default | Valid values and behavior |
| --- | --- | --- | --- | --- |
| `configuration_source` | string | Both | `default` | `default`, `local`, or `central`. Selects the supplied bundle, a trusted bundle in the caller repository, or an allowlisted central bundle. For a direct event run, `local` reads the trusted checkout of the repository containing the workflow. |
| `configuration_ref` | string | Both | Empty | Required for `default` and `central`; omitted for `local`. Must be a lowercase, 40-character Git commit SHA, for example `0123456789abcdef0123456789abcdef01234567`. |
| `configuration_profile` | string | All workflows | Workflow-specific | Bundle directory/profile name. It must match `[a-z0-9][a-z0-9-]{0,62}`. Changelog update defaults to `changelog-update`. |
| `focus` | string | Both | Empty | Optional allowlisted focus. For PR review and issue feedback: `documentation`, `security`, `tests`, or `general`. For release project review: `release-notes`, `rollout`, `rollback`, `acceptance`, `dependencies`, `owners`, `risk`, `operational-readiness`, or `general`. It is not arbitrary prompt text. |
| `max_comments` | number | PR review | `10` for reusable calls | Optional number of PR inline comments, from `0` through `20`. The chosen bundle can apply a lower ceiling; omitting the value lets the profile limit apply. |
| `max_issues` | number | Issue feedback | `100` for reusable calls | Optional batch limit from `1` through `100`. The issue-feedback workflow uses the event issue unless a target is supplied. |
| `dry_run` | boolean | Both | `false` | Resolves configuration, invokes the model, and validates output, but does not publish feedback. Cannot be `true` with `validate_only`. |
| `validate_only` | boolean | All workflows | `false` | Validates without invoking a model or publishing. Changelog update validates invocation inputs and stops before PR fetch and bundle resolution; other reusable workflows also resolve and validate configuration. Cannot be `true` with `dry_run`. |
| `pull_number` | number | PR review, changelog update | `0` | Optional positive PR number when no pull-request event provides the target. Changelog update requires an effective PR number, normally from the caller's event context. |
| `review_request_string` | string | PR review | `'AI REVIEW REQUESTED'` | Whitespace is stripped. The result must be non-empty, contain no control characters, and be at most 128 characters. When matching prior feedback exists, a non-bot issue comment containing this substring case-sensitively and created strictly after the bot's latest issue comment or PR review authorizes a new review. Rejected for other workflows. |
| `issue_number` | number | Issue feedback | `0` | Optional positive issue number when no issue event provides the target. |
| `target_repository` | string | Release project review | `${{ github.repository }}` | Strict canonical `owner/repo` of the release to review. Required for `workflow_call`; a direct dispatch defaults to the caller repository. Rejects URLs, `owner/repo@ref`/`owner/repo:ref` syntax, paths, and expressions. |
| `release_id` | string | Release project review | Empty | Positive decimal GitHub release ID. The reusable workflow accepts a string because workflow-job outputs are strings; it validates the value as a positive integer. Exactly one of `release_id`/`release_tag` is required. |
| `release_tag` | string | Release project review | Empty | Conservative tag selector (`[A-Za-z0-9._-]{1,128}`, no `..`). Resolved through the GitHub REST API; never used as a Git ref. Exactly one of `release_id`/`release_tag` is required. |
| `label` | string | Changelog update | `update-changelog` | Pull-request label that triggers a changelog update. The reusable workflow re-validates the exact label, `labeled` action, and open PR state. |
| `target_file` | string | Changelog update | `CHANGELOG.md` | Repository-relative POSIX path to the changelog file to update. Rejects absolute paths, backslashes, control characters, `.`/`..`/empty segments, trailing slashes, `.github`/`.opencode` first segments, and paths over 512 bytes. |
| `OPENROUTER_API_KEY` | secret | All model-backed workflows | Required | Provider credential forwarded by the caller. The changelog workflow declares it as required even for `validate_only`. |
| `central_config_token` | secret | All workflows using central configuration | Optional | Read token for a private central configuration repository; otherwise resolution uses `github.token`. |
| `release_target_token` | secret | Release project review | Optional | Target-scoped GitHub token (`contents: read`, `issues: write` on the target only) for cross-repository reviews. Falls back to `github.token` for same-repo runs. |

### PR review caller example

This example uses the supplied `documentation-review` profile. Substitute the
placeholder SHA with the reviewed workflow/bundle revision used by the
organization.

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      issues: read
      pull-requests: write
    with:
      configuration_source: default
      configuration_ref: 0123456789abcdef0123456789abcdef01234567
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 5
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

### Issue-feedback caller example

```yaml
jobs:
  feedback:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-issue-feedback.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      issues: write
    with:
      configuration_source: local
      configuration_profile: product-issue-feedback
      focus: general
      max_issues: 25
      dry_run: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

`central_config_token` is an optional secret, rather than a `with:` field. It
may be forwarded only when the allowlisted central configuration repository is
private; it needs read access to that repository.

## Bundle layout

For a `local` profile named `product-issue-feedback`, create this layout in the
caller repository's trusted default-branch checkout:

```text
.opencode/configuration/product-issue-feedback/
  bundle.json
  agent.md
  prompts/feedback.md
  skills/triage/SKILL.md
```

`hashes.json` is optional for a `local` profile. When supplied, every content
path declared in `bundle.json` must occur exactly once in it and no undeclared
hash entries are permitted. It is required for remote profiles. Paths must be
safe, relative files inside the profile directory; absolute paths, `..`
traversal, and symlinks are rejected.

## `bundle.json` fields

The [`bundle.json` reference](bundle-json-reference.md#fields) defines the
manifest fields, schema versions, content-path rules, and workflow mappings.
Use it as the canonical field reference for local and remote bundles.

## Supplied profiles

The available supplied profiles demonstrate the supported workflow mappings:

| Profile | `allowed_workflows` | `output_contract` | Typical limit |
| --- | --- | --- | --- |
| `documentation-review` | `["pr-documentation-review"]` | `pr-review-json-v1` | `{"max_comments": 10}` |
| `issue-feedback` | `["issue-feedback"]` | `issue-feedback-markdown-v1` | `{"max_comments": 5}` |
| `default-implementation` | `["issue-implementation"]` | `issue-implementation-decision-v1` | `{}` |
| `release-project-review` | `["release-project-review"]` | `release-project-issue-v1` | `{}` |
| `changelog-update` | `["pr-changelog-update"]` | `pr-changelog-update-v1` | `{}` |

## `hashes.json`

For normative hash rules, see [Relationship to
`hashes.json`](bundle-json-reference.md#relationship-to-hashesjson). For the
previous issue-feedback example, its shape is:

```json
{
  "agent.md": "203fc61798cf669d88bca3356b438e6605e6ce687e8831801ca8f961d35a7e1a",
  "prompts/feedback.md": "2bbbd5d18d256befe9c4c6bf202cdcb2c9fcb4a4a1b75c38ee01d7f2fd616348",
  "skills/triage/SKILL.md": "a81ae45c2f03255acc9d04ae76e712965289cd036a1844bfaaaf2049b74d291d"
}
```

These are real values from the supplied `issue-feedback` profile. Generate
actual values after every content edit rather than copying them:

```text
uv run scripts/update_hashes.py --profile product-issue-feedback
```

## Agent and skill front matter

The agent and every skill require YAML front matter with a unique `name`.
Review-profile agents must remain read-only; requesting
`permission.edit: allow` is rejected. See [Create a configuration
bundle](../how-to/creating-a-configuration-bundle.md#add-the-agent-skills-and-prompt)
for complete agent and skill examples.

## Values callers cannot configure

To preserve the trust boundary, reusable callers cannot set raw prompt text,
agent or skill paths, model IDs, output contracts, URLs, repository names,
branches, tags, or mutable references. Choose a reviewed profile instead. A
missing file, invalid front matter, mismatched hash, invalid path, or workflow
mismatch fails the run without falling back to another source.

## Release project-review contract

The `release-project-issue-v1` output contract accepts exactly one of:

```json
{"decision":"NO_ISSUE","summary":"..."}
```

or:

```json
{"decision":"CREATE_ISSUE","title":"...","body":"...","labels":["release-readiness"]}
```

The parser rejects malformed JSON, unknown top-level keys, unknown labels,
destination/endpoint fields (e.g. `repository`, `endpoint`, `url`, `assignees`,
`milestone`), oversize or empty content, empty evidence, and code-only
findings. The runner owns the destination repository, the `release-readiness`
label allowlist, the idempotency marker, and publication; the model may never
select an endpoint, repository, labels, or credentials.

When a schema-2 release bundle declares `midflight_commands`, the runner first
runs a fresh model phase under the non-publishing
`release-project-analysis-handoff-v1` contract. That contract accepts only
`assessment`, `validation_questions`, and `relevant_evidence`, rejects
command/control fields (`command`, `commands`, `args`, `shell`,
`environment`, `working_directory`, `url`, `repository`, `endpoint`,
`credentials`, `decision`, `title`, `body`, `labels`), and has no publication
path. The validated handoff is inserted into the phase-2 prompt as delimited
untrusted data; it is never appended to system instructions.
