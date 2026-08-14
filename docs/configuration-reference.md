# Configuration reference

This reference describes every supported configuration field for reusable
pull-request review and issue-feedback workflows, plus the files that make up
a configuration bundle. Remote bundles are hash-verified; caller-local bundles
may omit the optional hash lock. For setup and rollout instructions, see
the [configuration guide](configuration.md).

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
| `configuration_source` | string | Both | `default` | `default`, `local`, or `central`. Selects the supplied bundle, a trusted bundle in the caller repository, or an allowlisted central bundle. |
| `configuration_ref` | string | Both | Empty | Required for `default` and `central`; omitted for `local`. Must be a lowercase, 40-character Git commit SHA, for example `0123456789abcdef0123456789abcdef01234567`. |
| `configuration_profile` | string | Both | Required | Bundle directory/profile name. It must match `[a-z0-9][a-z0-9-]{0,62}`, for example `documentation-review`. |
| `focus` | string | Both | Empty | Optional allowlisted focus. For PR review and issue feedback: `documentation`, `security`, `tests`, or `general`. For release project review: `release-notes`, `rollout`, `rollback`, `acceptance`, `dependencies`, `owners`, `risk`, `operational-readiness`, or `general`. It is not arbitrary prompt text. |
| `max_comments` | number | PR review | `10` for reusable calls | Optional number of PR inline comments, from `0` through `20`. The chosen bundle can apply a lower ceiling; omitting the value lets the profile limit apply. |
| `max_issues` | number | Issue feedback | `100` for reusable calls | Optional batch limit from `1` through `100`. The issue-feedback workflow uses the event issue unless a target is supplied. |
| `dry_run` | boolean | Both | `false` | Resolves configuration, invokes the model, and validates output, but does not publish feedback. Cannot be `true` with `validate_only`. |
| `validate_only` | boolean | Both | `false` | Resolves and validates invocation/configuration without invoking a model or publishing. Cannot be `true` with `dry_run`. |
| `pull_number` | number | PR review | `0` | Optional positive PR number when no pull-request event provides the target, such as a wrapper invoked with `workflow_call`. |
| `issue_number` | number | Issue feedback | `0` | Optional positive issue number when no issue event provides the target. |
| `target_repository` | string | Release project review | `${{ github.repository }}` | Strict canonical `owner/repo` of the release to review. Rejects URLs, `owner/repo@ref`/`owner/repo:ref` syntax, paths, and expressions. |
| `release_id` | string | Release project review | Empty | Positive decimal GitHub release ID. The reusable workflow accepts a string because workflow-job outputs are strings; it validates the value as a positive integer. Exactly one of `release_id`/`release_tag` is required. |
| `release_tag` | string | Release project review | Empty | Conservative tag selector (`[A-Za-z0-9._-]{1,128}`, no `..`). Resolved through the GitHub REST API; never used as a Git ref. Exactly one of `release_id`/`release_tag` is required. |
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

`bundle.json` is a JSON object. The following example is a valid
issue-feedback profile using some fictitious values:

```json
{
  "schema_version": 1,
  "profile_name": "product-issue-feedback",
  "allowed_workflows": ["issue-feedback"],
  "agent_file": "agent.md",
  "skill_files": ["skills/triage/SKILL.md"],
  "prompt_template": "prompts/feedback.md",
  "model_profile": "issue-feedback-readonly",
  "output_contract": "issue-feedback-markdown-v1",
  "limits": {"max_comments": 5},
  "policy": {
    "capabilities": {
      "filesystem": "read-issue-context-only",
      "github_write": "issue-comment-only",
      "delegation": "deny"
    }
  }
}
```

| Field | Required | Type | Description and example |
| --- | --- | --- | --- |
| `schema_version` | Yes | integer | Bundle schema version. The supported value is `1`. |
| `profile_name` | Yes | string | Must equal the selected profile directory name and match `[a-z0-9][a-z0-9-]{0,62}`. Example: `product-issue-feedback`. |
| `allowed_workflows` | Yes | non-empty string array | Workflows that may use the bundle: `pr-documentation-review`, `issue-feedback`, `issue-implementation`, or `release-project-review`. Example: `["issue-feedback"]`. |
| `agent_file` | Yes | safe relative path | Primary agent Markdown file. Example: `agent.md`. Its YAML front matter must declare a unique `name`. |
| `additional_agent_files` | No | unique array of safe relative paths | Extra agent Markdown files, used by the implementation profile. Example: `["executor.md"]`. Each requires unique `name` front matter and may not repeat `agent_file`. |
| `skill_files` | Yes (may be empty) | unique array of safe relative paths | Skill Markdown files. Example: `["skills/triage/SKILL.md"]`. Each requires YAML front matter with a unique `name`. |
| `prompt_template` | Yes | safe relative path | Prompt template file. Example: `prompts/feedback.md`. |
| `model_profile` | Yes | string | Reviewed model-profile identifier, matching the profile-name grammar. Example: `issue-feedback-readonly`. Callers cannot override it. |
| `output_contract` | Yes | string | Workflow-owned output contract: `pr-review-json-v1`, `issue-feedback-markdown-v1`, `issue-implementation-decision-v1`, or `release-project-issue-v1`. |
| `context_policy` | No | string | PR-review-only context collection policy. The currently supported value is `pr-review-on-demand-v1`; omit it for issue workflows. |
| `limits` | No | object | Profile limits. For review profiles, use `{"max_comments": 10}` to cap caller-selected comments at 10. An empty object is valid. |
| `policy` | No | object | Bundle policy overlay. It can further restrict behavior, for example the `capabilities` object in the preceding example. It must be a JSON object. |

The available supplied profiles demonstrate the supported workflow mappings:

| Profile | `allowed_workflows` | `output_contract` | Typical limit |
| --- | --- | --- | --- |
| `documentation-review` | `["pr-documentation-review"]` | `pr-review-json-v1` | `{"max_comments": 10}` |
| `issue-feedback` | `["issue-feedback"]` | `issue-feedback-markdown-v1` | `{"max_comments": 5}` |
| `default-implementation` | `["issue-implementation"]` | `issue-implementation-decision-v1` | `{}` |
| `release-project-review` | `["release-project-review"]` | `release-project-issue-v1` | `{}` |

## `hashes.json`

For remote profiles, `hashes.json` maps every declared content file to its
lowercase SHA-256 digest. It is optional for local profiles; when supplied,
the same all-and-only mapping is enforced. Do not include `bundle.json` or
`hashes.json` itself. For the previous
issue-feedback example, its shape is:

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
python3 scripts/update_hashes.py --profile product-issue-feedback
```

Hashes are required to fetch remote profiles. They are optional for local
configuration, which is read from the caller repository's trusted checkout.

## Agent and skill front matter

The agent and every skill require YAML front matter with `name`. This minimal
agent is suitable for an issue-feedback profile:

```markdown
---
name: product-issue-feedback
description: Provide concise, actionable feedback for product issues
mode: primary
model: openrouter/openai/gpt-5.6-luna
permission:
  edit: deny
  bash: deny
  read: allow
---

# Product issue feedback agent

Return only the response required by the configured output contract.
```

For PR review profiles, agent front matter must remain read-only: requesting
`permission.edit: allow` is rejected. A corresponding minimal skill is:

```markdown
---
name: triage
description: Classify issue feedback by clarity and reproducibility
---

# Triage guidance

Ask for missing reproduction steps, expected behavior, and actual behavior.
```

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