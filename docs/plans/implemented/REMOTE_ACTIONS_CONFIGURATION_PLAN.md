# Remote Actions Configuration Plan

## Detailed Plan Documents

This document is the architecture overview. The implementation-ready plans are
split by responsibility and should be delivered in their numbered order:

1. [Reusable workflow interface](docs/plans/remote-actions/01-reusable-workflow-interface.md)
  — callable workflow inputs, caller-owned permissions, modes, and wrappers.
2. [Configuration bundles and resolution](docs/plans/remote-actions/02-configuration-bundles-and-resolution.md)
  — manifest contract, remote source trust, validation, and migration.
3. [Prompt templates and output contracts](docs/plans/remote-actions/03-prompt-templates-and-output-contracts.md)
  — constrained prompt customization, trusted composition, and parsing.
4. [Model, tool, and policy governance](docs/plans/remote-actions/04-model-tool-policy-and-overlays.md)
  — restrictive policy layers, capabilities, and consumer overlays.
5. [Provenance, operations, and rollout](docs/plans/remote-actions/05-provenance-operations-and-rollout.md)
  — audit records, reliability, documentation, release, and adoption.

## Goal

Allow repositories to consume these agentic workflows and their guidance from a
trusted remote source while preserving the current secure default: agent and
skill instructions are taken from the checked-out trusted revision. The feature
should make organization-wide updates easy without allowing pull-request,
issue, or dispatch input to choose executable or instruction-bearing content.

## Recommended Change: Publish Reusable Workflows

Expose the review, issue-feedback, and issue-implementation workflows through
`workflow_call` in addition to their current local triggers. Consumer
repositories would create thin local wrappers that supply their trigger,
permissions, and a configuration profile.

Recommended callable inputs:

- `configuration_ref`: an immutable commit SHA in the configuration repository.
- `configuration_profile`: a named policy/profile, such as
  `documentation-review` or `issue-triage`.
- `prompt_overrides`: a small, explicitly supported set of non-sensitive
  strings, such as review focus or output tone.
- `model_profile`: a name selected from an allowlisted model configuration.
- `dry_run`: generate and store feedback without publishing it.

Keep all write permissions and secrets declared by the caller. Document the
minimum permissions for every reusable workflow and reject configurations that
would require broader permissions.

## Recommended Change: Introduce a Remote Configuration Bundle

Replace the paired `CUSTOM_AGENT_FILE` and `CUSTOM_SKILL_FILE` settings with
one versioned configuration bundle. A bundle can reside in either:

1. the trusted default branch of the consumer repository (the current default),
   or
2. a dedicated configuration repository controlled by the organization.

A bundle manifest should specify the allowed agent, skill(s), prompt template,
model profile, output schema, and optional policy files. Example fields:

- `schema_version`
- `profile_name`
- `agent_file`
- `skill_files`
- `prompt_template`
- `model_profile`
- `allowed_workflows`
- `required_output_contract`
- `content_hashes`

This eliminates path drift between workflows and makes a tested configuration
profile a single reusable unit. Continue supporting the existing environment
variables during a migration period, but translate them internally to an
implicit local bundle.

## Recommended Change: Pin and Verify Remote Content

Remote guidance is part of the security boundary, so a branch, tag, URL, issue
body, pull-request body, or workflow-dispatch text must never select it.

- Require a full commit SHA for remote bundle references in production.
- Permit tags or branches only in an explicitly marked development mode, then
  resolve and record the resulting SHA before OpenCode runs.
- Fetch content using GitHub's authenticated API or a sparse checkout; do not
  download arbitrary URLs.
- Verify that every manifest path is repository-relative, regular text content,
  within an expected size limit, and matches its declared SHA-256 hash.
- Verify agent/skill YAML front matter, unique names, allowed modes and
  permissions, and schema compatibility before invoking OpenCode.
- Print and include in feedback markers the bundle repository, resolved SHA,
  profile, and content hash (never secrets).

For `pull_request_target`, resolve the configuration only from the base
repository's trusted default/base revision or an organization allowlist. Never
use a configuration reference contained in the pull request.

## Recommended Change: Make Prompts Templated and Constrained

Prompt customization is valuable, but free-form remote prompts can weaken
output contracts or safety instructions. Add prompt templates to the bundle
and render them with a limited set of typed variables:

- repository and workflow identity
- verified contributor login
- issue or pull-request metadata
- changed-line locations and diff/issue context
- configured review focus and response style

Use a template format without arbitrary code execution. Treat issue, comment,
and PR text as untrusted data, delimit it clearly, and do not allow it to
substitute instructions. The runner should append its non-negotiable output
contract after rendering the template, rather than trusting the template to
preserve it.

Provide structured, allowlisted overrides rather than a raw `prompt` input.
For example, `focus: documentation|security|tests` and
`max_comments: 0..20` are safer and easier to audit than unrestricted text.

## Recommended Change: Centralize Model and Tool Policy

Remote profiles should be able to select an approved model configuration, not
an arbitrary provider string or credentials. Create a model-policy file that
maps profile names to approved provider/model identifiers and limits such as:

- maximum tokens, runtime, and retry count
- temperature and reasoning settings
- permitted OpenCode tools and filesystem scope
- whether editing, shell access, network access, or delegation is permitted
- applicable workflows and data classification

The reusable workflows must retain the provider credential in GitHub Secrets.
Configuration files must never carry credentials, headers, tokens, or secret
interpolation syntax. Keep the review workflows read-only; give implementation
profiles the narrowest tools and repository permissions required.

## Recommended Change: Add Policy Layers and Organization Defaults

Support a deterministic precedence model so consumers can customize behavior
without bypassing central guardrails:

1. built-in workflow safety policy (not overrideable),
2. organization-approved remote policy bundle,
3. consumer repository local overlay, and
4. typed workflow-call or dispatch inputs.

The merge must be restrictive: a lower layer may narrow allowed tools, models,
or scopes, but cannot grant a capability forbidden above it. The validation
step should produce a resolved configuration report showing every source and
its final effective value.

This enables useful shared capabilities beyond agents and skills: common output
schemas, comment wording, label mappings, review filters, escalation rules,
branch naming, test commands, and organization-wide compliance guidance.

## Recommended Change: Add Integrity, Provenance, and Auditability

Create a machine-readable resolved-configuration artifact for every run. It
should include the workflow version, caller repository, remote repository,
resolved SHA, profile, manifest/content hashes, model profile, prompt-template
identifier, validation result, and publication outcome.

Also:

- Add the resolved bundle identity to idempotency markers so feedback can be
  traced to the configuration that generated it.
- Upload the report as a short-retention Actions artifact and emit a job
  summary for maintainers.
- Optionally use GitHub attestations or signed releases for centrally published
  bundles.
- Record rejected resolution attempts without exposing sensitive prompt data.

This makes prompt or policy changes reviewable and supports incident response,
rollback, and reproducibility.

## Recommended Change: Improve Safe Operations and Failure Handling

Add operational controls around remote resolution and model execution:

- Cache immutable bundles by repository, commit SHA, and manifest hash within a
  workflow run; validate cached content before reuse.
- Define conservative timeouts, size limits, retry behavior, and GitHub API
  rate-limit handling.
- Fail closed when remote configuration cannot be fetched or validated; do not
  silently fall back to a different profile.
- Support a named last-known-good SHA for an intentional maintainer-controlled
  rollback, never an automatic unreviewed fallback.
- Provide `validate-only` and `dry-run` modes that resolve and render a bundle
  but do not invoke the model or publish feedback.
- Preserve the existing duplicate-feedback behavior, while including a
  configuration version in the feedback identity where re-review on a policy
  update is desired.

## Recommended Change: Define a Configuration Contract and Test Suite

Extract resolution and validation from `scripts/run_agentic_feedback.py` into
a dependency-free module with clear interfaces. Test it independently from
GitHub publication and OpenCode execution.

Add tests for:

- local-bundle backward compatibility
- remote SHA pinning and allowlisted source repositories
- manifest schema validation, path traversal, symlinks, oversized files, and
  content-hash mismatches
- invalid YAML front matter, duplicate agent/skill names, and disallowed
  capabilities
- template rendering, escaping, and prompt-injection delimiters
- configuration precedence and restrictive merging
- idempotency/provenance markers and failure modes
- `pull_request_target` behavior proving PR-controlled configuration cannot be
  used

Use fixtures for remote bundle responses so tests do not require network access.

## Recommended Change: Documentation and Examples

Update `README.md` with:

- a minimal local configuration example (current behavior)
- a remotely hosted, SHA-pinned bundle example
- a consumer repository wrapper for each reusable workflow
- the bundle-manifest schema and supported typed overrides
- the trust model and explicit warning against mutable refs/untrusted inputs
- required permissions, secret ownership, audit records, and rollback steps
- a migration guide from `CUSTOM_AGENT_FILE` and `CUSTOM_SKILL_FILE`

Publish one secure example configuration repository or release directory with a
review profile, issue-feedback profile, implementation profile, and release
notes for configuration changes.

## Phased Delivery

### Phase 1: Contract and local compatibility

Define the manifest schema, local bundle resolver, validation module, typed
prompt variables, and tests. Keep the existing local file environment variables
working as a deprecated compatibility path.

### Phase 2: Remote immutable bundles

Add GitHub API/sparse-checkout resolution, SHA pinning, organization allowlists,
content hashes, resolved-configuration artifacts, and `validate-only` mode.

### Phase 3: Reusable workflows and centralized policy

Add `workflow_call` interfaces, consumer wrapper examples, policy-layer
merging, approved model profiles, and organization-managed shared bundles.

### Phase 4: Hardening and rollout

Add signed/attested bundle support if needed, rollout controls, telemetry for
validation failures, documented rollback, and deprecation dates for the legacy
pair of file paths.

## Acceptance Criteria

The feature is ready when a consumer can call a reusable workflow using a
centrally maintained, SHA-pinned profile; the run verifies and records exactly
which instructions were used; local configurations remain supported during
migration; and no untrusted event data can alter the agent, skill, prompt,
model, tools, or remote content source.
