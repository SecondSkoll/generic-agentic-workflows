# Configure agentic workflows

This guide explains how to configure the reusable OpenCode workflows for a
consumer repository. It covers the supported configuration paths, safe rollout,
and the controls that keep pull-request and issue content from selecting or
executing workflow configuration.

> **Start safely:** validate a configuration with `validate_only: true`, run it
> once with `dry_run: true`, and enable publication only after reviewing the
> generated artifacts and job summary.

## Choose a configuration model

| Model | Use it when | Configuration lives in | Recommended? |
| --- | --- | --- | --- |
| Supplied default profile (`default`) | The repository can use one of this repository's standard profiles unchanged. | This repository, fetched remotely at a pinned commit. | Yes, for initial adoption. |
| Caller-local bundle (`local`) | The calling repository needs its own instructions, skill, or prompt. | `.opencode/configuration/<profile>/` in the calling repository's trusted checkout. | Yes, for repository-specific behavior. |
| Centrally managed bundle (`central`) | Multiple repositories share centrally reviewed configuration. | An allowlisted central repository, pinned to a commit. | Yes, for organization-wide reuse. |

Configuration is selected only through typed workflow inputs. A caller cannot
pass a raw prompt, arbitrary agent/skill path, model ID, repository URL, branch,
or tag. The profile name is constrained to lowercase letters, numbers, and
hyphens; remote configuration requires an exact 40-character commit SHA.

## Prerequisites

1. Pin the reusable workflow to a reviewed immutable commit SHA (or an
   organization-approved protected release tag).
2. Add `OPENROUTER_API_KEY` as an Actions secret in the consumer repository.
   Restrict, rotate, and set a low credit limit on this provider key where the
   provider supports those controls.
3. Grant only the permissions required by the selected workflow:

   | Workflow | Minimum permissions for publication |
   | --- | --- |
   | PR documentation review | `contents: read`, `pull-requests: write` |
   | Issue feedback | `contents: read`, `issues: write` |
   | Issue implementation | `contents: write`, `issues: write`, `pull-requests: write` |

4. Copy the appropriate thin wrapper from [`examples/`](examples/README.md) to
   `.github/workflows/` in the consumer repository and adjust its trigger.

The wrapper owns triggers, concurrency, permissions, and secret forwarding. The
reusable workflow owns configuration resolution, validation, model invocation,
and publication.

## 1. Use a supplied default profile

`default` resolves a standard profile supplied by this repository. Because a
consumer calls the reusable workflow remotely, this source is remote too and
must be pinned with `configuration_ref`. The supplied profiles are:

| Profile | Workflow | Purpose |
| --- | --- | --- |
| `documentation-review` | PR documentation review | Reviews changed documentation and emits a structured PR review. |
| `issue-feedback` | Issue feedback | Produces issue feedback for open issues. |
| `default-implementation` | Issue implementation | Plans and prepares a constrained implementation for a selected issue. |

For initial adoption, use a wrapper like this:

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<reviewed-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: default
      configuration_ref: <pinned-workflow-sha>
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      validate_only: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Replace `<reviewed-sha>` before use. After a successful validation run, change
the mode in stages:

1. Remove `validate_only` and set `dry_run: true`. This may invoke the model but
   cannot create a review, comment, branch, commit, or pull request.
2. Review the effective policy, resolved configuration, provenance artifact, and
   dry-run output.
3. Remove `dry_run` to allow publication.

`validate_only` and `dry_run` are mutually exclusive. A validation-only run
stops before model invocation and GitHub writes.

### Direct workflows

The workflows included in this repository retain direct-event triggers. Direct
runs use `local`, which resolves the profile from this repository's trusted
checkout.

## 2. Create a caller-local bundle

Use a bundle when the supplied default behavior needs repository-specific instructions.
Store it on the protected default branch so a pull request cannot choose or
modify the configuration that reviews it.

### Directory layout

Create `.opencode/configuration/acme-docs-review/`:

```text
.opencode/configuration/acme-docs-review/
  bundle.json
  hashes.json
  agent.md
  prompts/
    review.md
  skills/
    documentation/
      SKILL.md
```

Use the existing
[documentation-review bundle](../.opencode/configuration/documentation-review/)
as the canonical working example.

### Manifest

`bundle.json` describes only declared, hashed content:

```json
{
  "schema_version": 1,
  "profile_name": "acme-docs-review",
  "allowed_workflows": ["pr-documentation-review"],
  "agent_file": "agent.md",
  "skill_files": ["skills/documentation/SKILL.md"],
  "prompt_template": "prompts/review.md",
  "model_profile": "review-readonly",
  "output_contract": "pr-review-json-v1",
  "limits": {"max_comments": 10}
}
```

The resolver requires all of the following:

- `schema_version` is currently `1`.
- `profile_name` matches the selected profile directory and has the allowed
  lowercase identifier form.
- The requested workflow is in `allowed_workflows`.
- `agent_file`, each `skill_files` entry, and `prompt_template` are contained
  paths inside the bundle. Absolute paths, `..`, backslashes, control
  characters, and symlinks are rejected.
- Agent and skill files have YAML front matter with a `name`.
- Prompt templates use only the documented brace tokens:
  `{{repository}}`, `{{feedback_kind}}`, `{{author_login}}`,
  `{{target_number}}`, `{{target_title}}`, `{{focus}}`,
  `{{max_comments}}`, `{{allowed_locations}}`, and
  `{{untrusted_content}}`.
- The model profile and output contract are allowed for the selected workflow.

The profile template changes only the profile-owned prompt section. It cannot
replace the workflow-owned safety instructions or output contract suffix.

### Hash declared content

`hashes.json` maps every declared content file to a lowercase SHA-256 digest:

```json
{
  "agent.md": "<sha256>",
  "prompts/review.md": "<sha256>",
  "skills/documentation/SKILL.md": "<sha256>"
}
```

Regenerate the values whenever a declared file changes:

```text
python3 scripts/update_hashes.py --profile acme-docs-review
```

Commit the manifest, content, and updated hashes together in a reviewed change.
The resolver verifies UTF-8 encoding, file and total-size limits, path
containment, front matter, workflow compatibility, and every digest before
OpenCode runs. A missing, stale, or extra declared path fails closed; it never
falls back to another profile or an unhashed file.

Reference the bundle from the wrapper:

```yaml
with:
  configuration_source: local
  configuration_profile: acme-docs-review
  focus: documentation
  max_comments: 10
  validate_only: true
```

A caller-local profile update changes the deterministic configuration digest. The next
run therefore performs a new review rather than treating prior feedback as
current.

## 3. Use centrally managed remote bundles

Remote configuration is supported as a **bundle**, not as arbitrary individual
agent, prompt, or skill URLs. This distinction is intentional: a bundle has a
manifest, immutable source revision, content hashes, and one validation path.

The reusable workflow supports `default`, `local`, and the built-in `central`
alias. `default` is this repository's supplied standard-profile source; `local`
is always the calling repository's trusted checkout; and `central` maps to a
centrally controlled repository and root path in the workflow release.
Consumers cannot supply a URL or repository name. The current example mapping
and complete central repository layout are documented in
[`examples/central-configuration/`](examples/central-configuration/README.md).

Use a full commit SHA and run validation first:

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<reviewed-sha>
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
      central_config_token: ${{ secrets.CENTRAL_CONFIG_TOKEN }}
```

`central_config_token` is optional. Supply it only when the central repository
is private and the default workflow token cannot read it. It needs only
`contents: read` access to that repository. It is used for configuration fetches
only: it is not logged, written to artifacts, included in prompts, or exposed
to OpenCode.

A remote resolution fails before model invocation if any of these are true:

- the source alias is unknown;
- the SHA is absent, shortened, mutable, or malformed;
- the selected profile is absent or incompatible with the workflow;
- a fetch, schema, path, size, UTF-8, front-matter, or hash check fails.

There is no fallback to local configuration, a default branch, a previous
revision, or stale cache content. Remote content is fetched through GitHub's
Contents API with bounded retries, response size, and timeout controls. It is
cached only for the current workflow run and revalidated on every cache hit.

### Remote files for agents and skills

Do not put `raw.githubusercontent.com`, `github.com/blob`, directory URLs, or
any other remote file URL in a workflow input, issue, pull request, prompt, or
model output. They are unsupported configuration selectors and are rejected by
the design.

If agents and skills need to be shared, place them in an approved remote bundle
and pin the *bundle* commit as shown above. Direct per-file remote references
are a future extension described in
[`plans/allow-remote-files.md`](plans/allow-remote-files.md); they are not an
active interface in this release. This preserves a single, reviewable trust
boundary while the referenced agent, skill, and prompt files remain hash
verified.

## Security model

Agentic workflows process text that an attacker may control: pull-request
diffs, issue bodies, titles, comments, and sometimes model output. Treat all of
that content as untrusted data, not configuration or commands.

### Main controls and their purpose

| Control | Mitigation | Why it matters |
| --- | --- | --- |
| Trusted base checkout for PR review | Uses `pull_request_target` only to publish feedback, while checking out the trusted base revision. | Prevents pull-request code and workflow changes from executing with the review token. |
| Typed, allowlisted selection | Inputs select only a profile, allowed focus, bounded counts, and an approved source alias. | Prevents prompt injection from becoming URL, path, model, or tool selection. |
| Immutable configuration | `default` and `central` bundles require an approved alias and full SHA; caller-local configuration comes from the trusted checkout. | Makes configuration reviewable and prevents branch/tag drift. |
| Schema, path, and hash validation | Bundle paths are contained; symlinks and traversal are rejected; all declared files are SHA-256 checked. | Stops substitution, undeclared content, and filesystem escape. |
| Fixed prompt composition | Workflow safety constraints and output suffixes are fixed around the validated profile template; untrusted content is explicitly delimited. | A profile or issue cannot remove safety rules or redefine the required result format. |
| Restrictive policy merge | Higher-level policy ceilings cannot be broadened by a bundle, overlay, agent, or invocation. | Agent text cannot grant itself tools, access, or quotas. |
| Output-contract validation | Model responses must satisfy the workflow's versioned contract before publication. | Prevents malformed or instruction-bearing output from reaching GitHub unchanged. |
| Least-privilege, ephemeral tokens | Uses `github.token` where possible; provider and optional central tokens are secrets. | Limits blast radius and avoids long-lived broad credentials. |
| Implementation path enforcement | The implementation workflow blocks changes to workflows, automation, dependencies, and agent/configuration instructions. | Prevents an implementation request from self-escalating its automation. |
| Redacted provenance | Short-retention artifacts record source, SHA, hashes, policy, mode, and result—not tokens, prompts, diffs, or raw responses. | Supports auditing and incident response without expanding data exposure. |

### Why this is safer than a broad GitHub-app-style integration

This design intentionally keeps the automation boundary narrow. Rather than
installing a broadly authorized integration that can act across repositories or
letting users select arbitrary content at run time, each consumer pins a
specific workflow revision, declares the exact job permissions, forwards only
required secrets, and selects from a constrained configuration interface.

The workflow is also deliberately separated into validation, generation, and
publication phases. A configuration failure, policy violation, malformed model
response, or provider failure fails closed and does not publish partial
feedback. For pull requests, the workflow never checks out or executes the
untrusted head with write-capable `pull_request_target` credentials. These are
architecture-level safeguards; they complement—not replace—normal branch
protection, code review, secret rotation, Actions allowlists, and review of any
agent-generated pull request.

## Review artifacts, operations, and incident response

Each run uploads short-retention (14-day) artifacts including resolved
configuration, effective policy, and
`resolved-agentic-provenance.json`. The provenance record includes the
configuration source, resolved SHA, profile, manifest/prompt/policy hashes,
output contract, model profile, mode, result, and configuration digest. It
excludes credentials, complete prompts, raw issue content, diffs, and raw model
responses.

If a run behaves unexpectedly:

1. Disable its wrapper trigger or temporarily set `validate_only: true`.
2. Download the resolution, configuration, policy, and provenance artifacts
   before they expire.
3. Identify whether resolution, policy, output contract, provider, or
   publication failed.
4. Roll back manually by pinning a known-good workflow or remote bundle SHA;
   do not automate rollback to an unreviewed revision.
5. Re-enable progressively: `validate_only`, `dry_run`, then publication.

See the [operations guide](operations/operations-guide.md) for the full
reliability bounds, test matrix, versioning, and rollback procedure.
