# generic-agentic-workflows

Reusable GitHub Actions workflows for agentic pull-request and issue feedback using OpenCode.

## Workflows

* `.github/workflows/opencode-review.yml` reviews pull-request diffs for documentation impact.
* `.github/workflows/opencode-issue-feedback.yml` checks every open issue when an issue changes and on a daily schedule. It does not comment again when it finds its existing feedback marker on that issue.
* `.github/workflows/opencode-issue-implementation.yml` runs on weekdays and can be dispatched manually. It selects one open issue that has either an `[[AI REVIEW REQUESTED]]` (or `[[AI IMPLEMENTATION REQUESTED]]`) comment or an `ai-review-requested` label, unless the manual dispatch supplies an open issue number. It skips issues that already have an implementation-status comment from `github-actions[bot]`, asks OpenCode to prepare an initial implementation, then the workflow commits, pushes, creates the PR, and posts its link—or a failure status—to the issue.

Both workflows invoke `scripts/run_agentic_feedback.py`. The script validates the selected repository customisations, runs OpenCode, and writes one marked GitHub comment per feedback type. For pull requests it creates one GitHub review: its summary is an overall review comment and valid feedback locations are attached directly to changed new-file lines.

The implementation workflow uses `.opencode/agents/issue-implementation.md` as a planner. It treats the issue context as untrusted, creates a concise plan, and hands it to `.opencode/agents/executor.md` for the focused implementation and validation. The workflow keeps the generated issue context outside the checkout, then commits, pushes, creates the PR, and posts status only after it verifies an implementation diff. Neither agent may edit workflows, automation, dependencies, or agent instructions. Review the generated PR normally before merging it.

### Contributor handles

The workflows pass the authoritative GitHub login from the event or issue API to
the feedback runner: `${{ github.event.pull_request.user.login }}` for pull
requests and `.user.login` for each issue. The runner tells OpenCode to use
that exact `@handle`. Skills should follow this instruction rather than using
an unresolved placeholder such as `{author}` or deriving a name from an issue
number.

### Customise the agent and skill

Each workflow sets the following job environment variables. Change their paths to choose custom guidance stored in the repository's trusted default branch:

```yaml
CUSTOM_AGENT_FILE: .opencode/agents/docs-impact-eval.md
CUSTOM_SKILL_FILE: .opencode/skills/basic-review/SKILL.md
FEEDBACK_KIND: pr-documentation-review # or issue-feedback
```

Agent and skill files must start with YAML front matter containing a `name`. The agent name is passed to OpenCode; the skill file is validated so workflow configuration cannot silently drift from the repository customisation.

### Pull-request review feedback format

The PR review runner asks OpenCode for JSON with an overall `summary` and zero
or more inline `comments`:

```json
{
	"summary": "Overall Markdown review.",
	"comments": [
		{
			"path": "docs/configuration.md",
			"line": 42,
			"body": "State the default value explicitly.",
			"suggestion": "The default timeout is 30 seconds."
		}
	]
}
```

Each inline location must reference an added line in the supplied diff. The
runner validates these locations before calling GitHub; feedback with an
invalid location is retained in the overall review body rather than causing
the review to fail.

### On-demand PR review context

The initial PR review packet deliberately contains only verified PR metadata,
the unified diff, and allowed inline locations—never full repository files.
For bundles that opt into `pr-review-on-demand-v1`, the agent may return a
structured request for affected-file contents, then a bounded base-tree
manifest, and finally selected files from that manifest. Effective policy
controls these capabilities and strict round, file-count, and byte limits;
workflow callers cannot choose files or supply prompt text. Requested content
is read as Git objects from the trusted base (or the fetched PR-head ref only
for changed-file text), is treated as untrusted data, and is never executed.
The `pull_request_target` workflow still checks out only the trusted base
revision. Provenance records counts and byte totals, not file contents, diffs,
prompts, or model responses.

When an inline item includes a `suggestion`, the runner produces GitHub's
`suggestion` block, which gives reviewers an **Apply suggestion** control. The
`body` explains the recommendation and `suggestion` contains only the exact
replacement. For a multi-line replacement, include `start_line` and `line`; the
inclusive range must consist entirely of added lines in the diff.

## Secrets and least-privilege tokens

The OpenCode provider credential is configured as the repository Actions secret `OPENROUTER_API_KEY`. Generate an OpenRouter key restricted to only the required provider/model where the provider supports it, with a small credit limit and an expiry or rotation policy. Store it under **Settings → Secrets and variables → Actions → New repository secret**. Do not place API keys in workflow YAML, issue text, pull-request content, or agent/skill files.

For GitHub comments, these workflows use the ephemeral `${{ github.token }}` automatically supplied by Actions. No personal access token is needed for a workflow in this repository:

| Workflow | Required job permissions |
| --- | --- |
| Pull-request review | `contents: read`, `pull-requests: write` |
| Issue feedback | `contents: read`, `issues: write` |
| Issue implementation | `contents: write`, `issues: write`, `pull-requests: write` |

If an external integration cannot use `github.token`, create a **fine-grained personal access token** for only this repository, set the shortest practical expiration, and grant only:

* **Pull requests: Read and write** for PR comments, or
* **Issues: Read and write** for issue comments.

Add it as an Actions secret such as `AGENTIC_FEEDBACK_TOKEN`, pass it to `GITHUB_TOKEN` only in the comment-producing step, and remove it when no longer required. Do not grant repository administration, workflow, or broad organization permissions.

> **Security note:** The PR workflow uses `pull_request_target` so it can comment with write permission, but it checks out the trusted base revision and only reads the untrusted PR diff. Do not change it to check out or execute pull-request code.

## Reusable workflows (`workflow_call`)

Each workflow also exposes an `on.workflow_call` interface so consumer repositories can call it through a thin local wrapper. The caller owns event triggers, `permissions:` declarations, concurrency controls, and provider secret forwarding; the reusable workflow owns input validation, model invocation, and publication.

### Caller/callee responsibility split

| Concern | Owned by |
| --- | --- |
| Event triggers and filtering | Caller |
| `permissions:` declarations | Caller |
| Provider secret forwarding | Caller |
| Input validation and bounds | Callee |
| Configuration profile selection | Caller |
| Model invocation and output parsing | Callee |
| Publication (comments, reviews, PRs) | Callee, gated by `dry_run`/`validate_only` |
| Provenance artifact | Callee |

### Typed inputs

| Input | Type | Required | Purpose |
| --- | --- | --- | --- |
| `configuration_source` | string | no | `local` or an approved remote source alias; default `local`. |
| `configuration_ref` | string | no | Full 40-character commit SHA for a remote bundle. |
| `configuration_profile` | string | yes | Bundle profile name, constrained to `[a-z0-9][a-z0-9-]{0,62}`. |
| `focus` | string | no | Allowlisted review focus; no arbitrary prompt text. |
| `max_comments` | number | no | Bounded feedback count, `0`–`20` (PR review only). |
| `max_issues` | number | no | Bounded issue count, `1`–`100` (issue feedback only). |
| `request_label` | string | no | Validated label/marker for issue selection (implementation only). |
| `dry_run` | boolean | no | Resolve, validate, and generate output without publishing. |
| `validate_only` | boolean | no | Resolve and validate configuration only. |
| `pull_number` / `issue_number` | number | no | Target number when event context is unavailable. |

The reusable workflows do **not** expose raw prompt, agent path, skill path, model identifier, arbitrary URL, or mutable reference inputs. Any attempt to supply them is rejected before checkout or model invocation by the `Resolve invocation` step (`scripts/resolve_invocation.py`).

### Using a non-local `configuration_source`

`configuration_source: local` uses configuration from the trusted checkout selected by the reusable workflow. To use shared configuration from somewhere else, the source must be an approved alias built into the reusable workflow release, such as `central`; callers cannot pass a URL, repository name, branch, tag, raw file URL, or PR ref.

> **Current implementation note:** this repository allowlists `local` in `scripts/resolve_invocation.py` and the `central` remote alias in `scripts/agentic_configuration.py`. The remote examples below show the caller syntax for an approved remote alias. Unknown aliases fail closed during `Resolve invocation`.

When an approved remote alias is available:

1. Ask the workflow owner which `configuration_source` aliases are approved and which repository/root each alias maps to.
2. Review the configuration bundle in that source. A profile is expected to be a named bundle such as `.opencode/configuration/documentation-review/` containing its manifest, agent, skills, prompts, and content hashes.
3. Pin `configuration_ref` to the exact 40-character commit SHA that contains the reviewed bundle. Do not use `main`, a tag, a release name, a pull-request ref, or a shortened SHA.
4. Set `configuration_profile` to the desired profile name. It must match `[a-z0-9][a-z0-9-]{0,62}`.
5. Use `validate_only: true` first, then `dry_run: true`, then normal publication.

Example PR review wrapper using an approved remote source:

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<pinned-workflow-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: central
      configuration_ref: 0123456789abcdef0123456789abcdef01234567
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
      dry_run: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

The reusable workflow resolves the alias and SHA before any model invocation. If the alias is unknown, the SHA is not exactly 40 hexadecimal characters, the profile name is invalid, or the bundle does not match the workflow, the run stops instead of falling back to local configuration.

### Typed input examples

Pull-request review supports `focus`, `max_comments`, `dry_run`, `validate_only`, and `pull_number`:

```yaml
with:
  configuration_source: local
  configuration_profile: documentation-review
  focus: security          # one of documentation, security, tests, general
  max_comments: 5          # 0-20; PR review only
  pull_number: 123         # useful for workflow_dispatch or non-PR triggers
  dry_run: true            # generate output but do not publish a review
```

Issue feedback supports `focus`, `max_issues`, `dry_run`, `validate_only`, and `issue_number`:

```yaml
with:
  configuration_source: local
  configuration_profile: issue-feedback
  focus: general
  max_issues: 25           # 1-100; issue feedback only
  issue_number: 456        # optional single open issue target
  validate_only: true      # resolve and validate only; no checkout or model call
```

Issue implementation supports `request_label`, `issue_number`, `dry_run`, and `validate_only`:

```yaml
with:
  configuration_source: local
  configuration_profile: default-implementation
  request_label: ai-implementation-requested
  dry_run: true            # cannot create branches, push commits, open PRs, or comment
```

Do not set `dry_run` and `validate_only` together; the resolver rejects that combination. Inputs are workflow-specific: for example, `max_comments` is valid only for PR review, and `request_label` is valid only for issue implementation.

### Modes

* `validate_only` stops after configuration resolution and writes a job summary and artifact; it performs no checkout, model invocation, or GitHub write.
* `dry_run` may call OpenCode but cannot post comments, create branches, push commits, or open PRs.
* Normal execution retains existing publication behavior.

### Minimal consumer wrapper

See [`docs/examples/`](docs/examples/README.md) for complete wrappers. A review wrapper is conceptually equivalent to:

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<pinned-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: local
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Use a real reviewed commit SHA or protected release tag in the `uses:` line, not a placeholder. A caller with read-only permissions can use `validate_only: true` but cannot accidentally publish feedback.

### Migration from direct triggers

Existing direct triggers (`pull_request_target`, `issues`, `workflow_dispatch`) continue to work without consumer changes. To migrate a consumer repository to the reusable form:

1. Pin this repository to a reviewed commit SHA in your wrapper's `uses:` line.
2. Copy the relevant wrapper from `docs/examples/` into `.github/workflows/` in your repository.
3. Adjust the `on:` trigger and `permissions:` to match your policy.
4. Forward only the provider secret the callee needs (`OPENROUTER_API_KEY`).
5. Start with `validate_only: true`, then `dry_run: true`, then normal publication before enabling write-capable issue implementation.

## Configuration bundles (Plan 2)

A configuration bundle is a versioned directory under
`.opencode/configuration/<profile>/` containing:

- `bundle.json` — schema-v1 manifest (`profile_name`, `allowed_workflows`,
  `agent_file`, `skill_files`, `prompt_template`, `model_profile`,
  `output_contract`, `limits`).
- `agent.md` and one or more `skills/<name>/SKILL.md` files with required
  front-matter `name`.
- `prompts/<name>.md` — a brace-token template using only documented variables
  (`{{repository}}`, `{{feedback_kind}}`, `{{author_login}}`,
  `{{target_number}}`, `{{target_title}}`, `{{focus}}`, `{{max_comments}}`,
  `{{allowed_locations}}`, `{{untrusted_content}}`).
- `hashes.json` — a SHA-256 digest for every declared content path.

The resolver (`scripts/agentic_configuration.py`) normalizes and contains
every path (rejecting absolute paths, `..`, backslashes, control characters,
and symlinks), enforces UTF-8, per-file, and total-byte bounds, verifies every
hash, validates front matter, and rejects workflow mismatches. Remote bundles
are pinned to a full 40-character commit SHA through an allowlisted alias;
mutable refs, URLs, and unknown aliases fail closed with no fallback. See
[`docs/examples/central-configuration/README.md`](docs/examples/central-configuration/README.md)
for a complete remote-bundle example.

### Legacy `CUSTOM_AGENT_FILE` / `CUSTOM_SKILL_FILE` (deprecated)

The legacy variables remain functional for local sources during the migration
window and emit a deprecation warning. They will be removed in the next major
workflow release after the published migration window. See
[`docs/operations/migration-and-deprecation.md`](docs/operations/migration-and-deprecation.md).

## Prompt templates and output contracts (Plan 3)

The runner composes the model prompt from five fixed, ordered sections:
(1) workflow-owned safety constraints, (2) the validated profile template,
(3) typed runtime context, (4) delimited untrusted content, and
(5) a workflow-owned output suffix. A profile may customize section 2 only;
sections 1 and 5 are immutable. Untrusted issue/PR content is wrapped in
`<untrusted-issue-content>...</untrusted-issue-content>` markers and treated
as data only. Output is published only after passing a versioned contract
(`pr-review-json-v1`, `issue-feedback-markdown-v1`, or
`issue-implementation-decision-v1`).

## Model/tool policy (Plan 4)

`scripts/agentic_policy.py` merges five policy layers (built-in safety,
organization, bundle profile, consumer overlay, typed invocation inputs) using
restrictive operations only: set intersection for allowlists, minimum for
quotas, logical AND for permissions, and explicit rejection for conflicting
required values. No layer can broaden a capability granted by a higher layer.
Review workflows stay read-only regardless of agent or template content. The
issue-implementation workflow machine-enforces changed-path constraints before
any push or PR creation, rejecting changes to workflows, automation,
dependencies, and agent/skill/configuration instructions. The effective policy
is inspectable in the `effective-policy.json` artifact and job summary.

## Provenance and operations (Plan 5)

Every run writes a redacted `resolved-agentic-provenance.json` record (source,
resolved SHA, profile, manifest/prompt/policy hashes, output contract, model
profile, mode, result, and a deterministic configuration digest) and uploads
it as a short-retention artifact. Provenance is emitted on every path,
including `validate_only` and resolution/policy/contract/provider failures (a
minimal redacted attempted-resolution record). A v2 idempotency marker
carries the configuration digest so a profile update triggers re-review;
legacy v1 markers are still parsed during migration. For incident response,
manual rollback, release/versioning, the operational test matrix, and runtime
reliability controls, see
[`docs/operations/operations-guide.md`](docs/operations/operations-guide.md).


## TEST

This is a test for a PR.