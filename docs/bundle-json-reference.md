# `bundle.json` reference

`bundle.json` is the manifest for a configuration bundle. It identifies the
reviewed content files and selects only workflow-owned model and output
options. The resolver supports schema version `1` and rejects unknown or
invalid combinations before model invocation.

For the authoring sequence, see [creating a configuration bundle](creating-a-configuration-bundle.md).

## Complete example

```json
{
  "schema_version": 1,
  "profile_name": "team-docs-review",
  "allowed_workflows": ["pr-documentation-review"],
  "agent_file": "agent.md",
  "additional_agent_files": [],
  "skill_files": ["skills/documentation/SKILL.md"],
  "prompt_template": "prompts/review.md",
  "model_profile": "review-readonly",
  "output_contract": "pr-review-json-v1",
  "context_policy": "pr-review-on-demand-v1",
  "limits": {"max_comments": 5},
  "policy": {
    "capabilities": {
      "filesystem": "read-trusted-checkout-diff",
      "github_write": "review-comment-only",
      "delegation": "deny"
    }
  }
}
```

## Fields

| Field | Required | Type | Valid values and rules |
| --- | --- | --- | --- |
| `schema_version` | Yes | integer | Must be `1`. |
| `profile_name` | Yes | string | Must match `[a-z0-9][a-z0-9-]{0,62}` and exactly match the bundle directory name and selected `configuration_profile`. |
| `allowed_workflows` | Yes | non-empty array of strings | Every value must be one of `pr-documentation-review`, `issue-feedback`, `issue-implementation`, or `release-project-review`. The current workflow must be present. |
| `agent_file` | Yes | safe relative path | Primary agent Markdown file, for example `agent.md`. It requires front matter with a safe unique `name`. |
| `additional_agent_files` | No | array of unique safe relative paths | Additional agent files, normally for implementation. They cannot repeat `agent_file`; each requires a unique front-matter `name`. |
| `skill_files` | Yes | array of unique safe relative paths | Skill Markdown files. The array may be empty. Each file requires front matter with a unique `name`. |
| `prompt_template` | Yes | safe relative path | Prompt template Markdown file, for example `prompts/review.md`. |
| `model_profile` | Yes | string | Registered profile name matching the profile-name grammar. The available names are listed below; a caller cannot override this value. |
| `output_contract` | Yes | string | One of the supported workflow-owned contracts listed below. |
| `context_policy` | No | string | PR documentation review only. The supported value is `pr-review-on-demand-v1`. Omit it for all other workflows. |
| `limits` | No | JSON object | Optional profile limits. A PR-review profile may set `{"max_comments": 10}` or a lower ceiling. `{}` is valid. |
| `policy` | No | JSON object | Restrictive policy overlay. It can narrow capabilities or quotas but cannot broaden built-in workflow policy. |
| `preflight_commands` | No | array of strings | Release-project-review only; at most three commands. The workflow runs them, if allowed, from its fixed local allowlist in the immutable checkout. The model never executes them. |

## Content-path requirements

The following fields are content paths: `agent_file`, `additional_agent_files`,
`skill_files`, and `prompt_template`.

Each path must:

- be a non-empty relative POSIX path inside the profile directory;
- use forward slashes and no control characters;
- contain no absolute prefix, backslash, `.`, `..`, empty segment, or trailing
  slash;
- identify a normal, non-symlink file; and
- appear exactly once as a key in `hashes.json`.

The resolver allows at most 64 declared files, 1 MiB per content file, and 8
MiB of total bundle content.

## Workflow mappings

These combinations are supported by the supplied profiles and policy registry.

| Workflow | Compatible `model_profile` | Required `output_contract` | Notes |
| --- | --- | --- | --- |
| `pr-documentation-review` | `review-readonly` | `pr-review-json-v1` | May use `context_policy: "pr-review-on-demand-v1"`; review agent cannot request edit permission. |
| `issue-feedback` | `issue-feedback-readonly` | `issue-feedback-markdown-v1` | Review agent cannot request edit permission. |
| `issue-implementation` | `implementation-planner` | `issue-implementation-decision-v1` | May declare a delegated executor in `additional_agent_files`. |
| `release-project-review` | `release-project-review-readonly` | `release-project-issue-v1` | May declare up to three `preflight_commands`; review agent cannot request edit permission. |

The manifest parser recognizes all listed contracts. Effective-policy resolution
enforces the workflow-specific contract and model-profile compatibility, so a
mismatched pair fails before the provider is called.

## `policy` overlay

`policy` is optional, but when present it is restrictive only. A common shape
is:

```json
{
  "policy": {
    "capabilities": {
      "filesystem": "read-trusted-checkout-diff",
      "github_write": "review-comment-only",
      "delegation": "deny"
    },
    "max_tokens": 4000
  }
}
```

Capability axes are `filesystem`, `shell`, `network`, `github_write`, and
`delegation`. The built-in workflow policy is the upper bound. A bundle may
set an axis to `deny` or repeat the applicable scoped grant, but it may not
substitute a broader or different non-deny grant. Numeric limits similarly
cannot exceed the built-in or model-profile ceilings.

## `preflight_commands`

A release review can request up to three reviewed preflight commands:

```json
{
  "preflight_commands": [
    "python3 -m unittest -q"
  ]
}
```

Commands are not general shell access. The runner validates them against a
fixed local-only allowlist, runs them in the immutable target checkout with a
minimal environment and bounded output, and treats results as untrusted
evidence. A failed preflight supports a finding only if the output explains a
concrete release-management consequence and owner/action.

## Relationship to `hashes.json`

`hashes.json` must map every declared content path—and only those paths—to a
lowercase SHA-256 digest. It does not include `bundle.json` or `hashes.json`.
Use the project helper after any content change:

```text
uv run scripts/update_hashes.py --profile <profile-name>
```

Hash validation covers bytes, not semantic equivalence. Reformatting front
matter, changing whitespace, or modifying a prompt requires regenerating the
corresponding digest.

## Rejected values

The manifest cannot introduce credentials, provider URLs, headers, arbitrary
model identifiers, new output contracts, or arbitrary executable locations.
The resolver also rejects an unsupported schema version, duplicate files,
workflow mismatch, missing required front matter, invalid paths, unknown
model/profile names, stale hashes, and policy escalation attempts. These
failures are intentional and fail closed: the workflow does not fall back to a
different source or revision.
