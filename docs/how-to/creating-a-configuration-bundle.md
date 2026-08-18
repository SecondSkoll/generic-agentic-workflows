# Create a configuration bundle

A configuration bundle is a versioned set of reviewed agent
instructions for one workflow profile. Bundles let maintainers change
workflow-specific guidance without allowing a caller to supply arbitrary
prompts, model identifiers, file paths, or network locations.

Use this guide for a repository-owned `local` bundle or for a profile published
to the allowlisted `central` configuration repository. Read the
[`bundle.json` reference](../reference/bundle-json-reference.md) before choosing field
values.

## Before you begin

- Select the one workflow that will use the profile:
  `pr-documentation-review`, `issue-feedback`, `issue-implementation`, or
  `release-project-review`.
- Select a registered, compatible `model_profile` and the workflow's required
  output contract. The profile cannot introduce a new model identifier or
  contract.
- Use a lowercase profile name matching `[a-z0-9][a-z0-9-]{0,62}`.
- Keep bundle changes in a protected branch and review them like application
  code. For a local bundle, use the repository's trusted default branch; the
  PR head does not select configuration.

## 1. Create the directory structure

For a profile named `team-docs-review`, create this directory in the
configuration root:

```text
.opencode/configuration/team-docs-review/
  bundle.json
  agent.md
  prompts/review.md
  skills/documentation/SKILL.md
```

`bundle.json` declares the content files; optional `hashes.json` records their
integrity digests. An implementation profile may also declare an additional
subagent file, for example `executor.md`.

All declared paths must be relative POSIX paths inside the profile directory.
Absolute paths, `..`, empty path segments, backslashes, and symlinks are
rejected.

## 2. Write `bundle.json`

Start with a minimal manifest appropriate for the workflow. This is a
PR-documentation-review example:

```json
{
  "schema_version": 1,
  "profile_name": "team-docs-review",
  "allowed_workflows": ["pr-documentation-review"],
  "agent_file": "agent.md",
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

The `profile_name` must match both the directory name and the wrapper's
`configuration_profile`. `allowed_workflows` must contain the workflow being
resolved. See the complete [`bundle.json` reference](../reference/bundle-json-reference.md)
for valid values and workflow-specific constraints.

## 3. Add the agent, skills, and prompt

Every agent and skill file must begin with YAML front matter containing a
unique `name`.

```markdown
---
name: team-docs-review
description: Review documentation changes for accuracy and usability
mode: primary
model: openrouter/openai/gpt-5.6-luna
permission:
  edit: deny
  bash: deny
  read: allow
---

# Documentation review agent

Return only the response required by the configured output contract.
```

The agent's front-matter model must remain consistent with the registered
`model_profile`; do not add provider credentials, API endpoints, or arbitrary
model values. Review bundles (`pr-documentation-review`, `issue-feedback`, and
`release-project-review`) cannot request `edit: allow`.

A skill requires at least a `name`:

```markdown
---
name: documentation
description: Check reader-facing documentation for clarity and completeness
---

# Documentation guidance

Identify concrete, actionable documentation problems.
```

Write the declared prompt template as reviewed instructions. Treat issue text,
PR text, diffs, release metadata, and repository content as untrusted data;
do not instruct the model to treat that content as policy or commands.

## 4. Generate `hashes.json` when required or desired

```{note}
This is only required when the bundle is contributed to a repository configured for remote retrieval of configuration bundles.
```

Hash every file declared by `agent_file`, `additional_agent_files`,
`skill_files`, and `prompt_template`. Do **not** hash `bundle.json` or
`hashes.json` itself. Remote profiles require this file; caller-local profiles
may omit it when repository checkout trust is sufficient.

From the repository root, run:

```text
uv run scripts/update_hashes.py --profile team-docs-review
```

The resulting file maps each declared relative path to a lowercase 64-character
SHA-256 digest:

```json
{
  "agent.md": "<sha256>",
  "prompts/review.md": "<sha256>",
  "skills/documentation/SKILL.md": "<sha256>"
}
```

Run the command after **every** content edit, including changes to front
matter. A missing, stale, duplicate, or undeclared hash entry makes resolution
fail closed.

## 5. Validate the bundle locally

Resolve the profile directly before committing it. For the example above:

```text
uv run python scripts/agentic_configuration.py \
  --workflow pr-documentation-review \
  --bundle-root .opencode/configuration \
  --configuration-profile team-docs-review \
  --result /tmp/team-docs-review.json
```

Use the matching workflow identifier for every other profile. The resolver
checks schema version, profile/workflow compatibility, path safety, required
front matter, known output contract, each declared content hash, and rejects
unknown manifest keys so a newer field cannot be silently ignored.

Then run the relevant project tests and inspect the generated resolution JSON.
Do not work around a supplied hash mismatch or policy failure by weakening
restrictions; fix the manifest or file content that caused it.

## 6. Publish the bundle through the intended source

### Local source

1. Commit the bundle under `.opencode/configuration/<profile>/` on the
   protected default branch of the consumer repository.
2. Set the wrapper inputs to `configuration_source: local` and
   `configuration_profile: <profile>`.
3. Omit `configuration_ref`.

### Central source

1. Commit the bundle under `.opencode/configuration/<profile>/` in the
   approved central configuration repository.
2. Review and record the resulting full commit SHA.
3. Set `configuration_source: central`, `configuration_ref` to that SHA, and
   `configuration_profile` to the profile name in each consumer wrapper.
4. If the central repository is private, forward the read-only
   `central_config_token` secret.

### Supplied default source

Do not copy a supplied profile merely to use it unchanged. Select
`configuration_source: default`, provide the reviewed SHA of this workflow
repository as `configuration_ref`, and select the supplied profile name.

## 7. Roll out and maintain it

Deploy a changed bundle with `validate_only`, then `dry_run`, and finally
normal publication. For remote profiles, promote the new commit SHA explicitly
in each wrapper. For local profiles, merge only to the trusted default branch.

When changing a hash-locked or remote bundle, update hashes, rerun resolution
and tests, and review
the provenance artifact after deployment. OpenCode loads `.opencode/`
configuration at startup; restart it after configuration changes when using it
interactively.
