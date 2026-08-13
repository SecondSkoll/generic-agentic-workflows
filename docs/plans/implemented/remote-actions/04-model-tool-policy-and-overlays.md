# Plan 4: Model, Tool, and Policy Governance

## Objective

Make organization-wide model and capability choices reusable without letting a
consumer, remote prompt, or untrusted event grant itself additional privileges.
Configuration layers must merge predictably and restrictively.

## Scope

This plan defines policy files, model profile selection, agent capability
validation, configuration precedence, and the protections required for the
write-capable issue-implementation workflow.

## Policy Layers

Resolve policy in this fixed precedence order:

1. **Built-in workflow safety policy** — shipped with the reusable workflow;
   cannot be overridden. It defines mandatory output contracts, trusted source
   requirements, and prohibitions such as executing PR-head code in
   `pull_request_target`.
2. **Organization policy** — a pinned, allowlisted central policy document that
   defines which remote sources, models, tools, and data classifications are
   permitted.
3. **Bundle profile policy** — a validated configuration bundle that selects
   from organization-approved policies and may make them stricter.
4. **Consumer local overlay** — a trusted file in the caller's default/base
   revision that may reduce scope, limits, or publication behavior.
5. **Typed invocation inputs** — workflow-call or dispatch inputs that may make
   policy stricter only.

The implementation must never use “last value wins” merging for a capability.
Instead, use restrictive operations: set intersection for allowlists, minimum
for quotas, logical AND for permissions, and explicit rejection for conflicting
required values.

## Model Profile Registry

Define model profiles in workflow- or organization-controlled policy, for
example:

```json
{
  "review-readonly": {
    "provider_model": "openrouter/vendor/model",
    "max_tokens": 8000,
    "temperature_max": 0.2,
    "timeout_seconds": 180,
    "max_retries": 1,
    "allowed_workflows": ["pr-documentation-review", "issue-feedback"],
    "data_classification": "repository-content"
  }
}
```

A profile bundle references a profile name only. It cannot define provider
credentials, API base URLs, headers, or arbitrary model strings. The workflow
receives credentials from GitHub Secrets and configures the provider through
its standard secure interface.

Policy validation must confirm:

- the requested model profile exists and supports the feedback kind;
- all profile limits are at or below organization ceilings;
- a consumer may choose a lower-cost/lower-capability approved profile only
  when organization policy permits it;
- retries, token budgets, and timeouts have bounded defaults;
- provider errors do not leak credentials in logs or artifacts.

## Tool and Agent Capability Policy

Parse agent front matter and treat each capability as a request, not an
authorization. Produce an effective capability set by intersecting the agent,
profile, organization, and workflow policies.

Recommended base profiles:

| Workflow | Filesystem | Shell | Network | GitHub write | Delegation |
| --- | --- | --- | --- | --- | --- |
| PR review | read limited trusted checkout/diff | deny | provider only | review/comment API only | deny by default |
| Issue feedback | read issue context only | deny | provider only | issue comment API only | deny by default |
| Issue implementation | repository workspace allowlist | narrowly allow validation commands | provider and GitHub API only | scoped branch/PR/issue actions | planner-to-executor only |

For issue implementation, retain the existing prohibitions on workflow,
automation, dependency, and agent-instruction changes. Enforce these controls
outside the prompt as well: validate changed paths before commit, prevent
workflow-file modifications, and restrict git operations to the generated
branch.

## Consumer Local Overlays

A consumer overlay supports repository-specific requirements without copying
central agents or skills. It must be loaded only from the trusted local
revision and use a small schema, for example:

```json
{
  "max_comments": 5,
  "allowed_focus": ["documentation"],
  "publication": {"allow": true},
  "test_commands": ["python -m unittest"]
}
```

An overlay may:

- lower limits and disable optional features;
- add approved repository-specific test commands for implementation profiles;
- choose an approved profile from a central allowlist;
- narrow allowed paths or labels.

It may not:

- select an unapproved remote repository or mutable ref;
- grant tools, network access, write permissions, or model access;
- introduce secrets or environment interpolation;
- weaken output/safety requirements;
- specify arbitrary shell commands except under an explicitly designed,
  allowlisted implementation test-command policy.

## Resolution Report

The policy engine should emit a structured effective-policy report including:

- each layer identity, source, commit SHA, and hash;
- selected model profile and effective token/time/retry limits;
- requested and effective tool capabilities;
- allowed source repositories and resolved bundle identity;
- effective publication mode and output contract;
- every restrictive change or rejected conflict.

Redact all credentials and avoid embedding full untrusted issue/diff content.

## Implementation Steps

1. Define policy schemas and a dependency-free validation/merge module.
2. Add immutable built-in safety policy constants to the runner/workflow
   package.
3. Add organization policy loading through the same pinned mechanism used for
   bundles, with a bootstrap allowlist owned by the workflow.
4. Validate agent front matter against the effective policy before OpenCode
   starts.
5. Pass only effective model settings and tool permissions to OpenCode; do not
   pass raw policy documents or unfiltered capability requests.
6. Add consumer overlay resolution from a fixed trusted local path.
7. Add issue-implementation pre-commit changed-path checks and policy-aware
   validation command handling.
8. Include effective policy identity in provenance artifacts and appropriate
   idempotency markers.

## Tests

- Restrictive merges across all five layers.
- Conflicting values, attempted privilege escalation, and unknown profile
  rejection.
- Token/time/retry minimum behavior and allowlist intersection.
- Agent requesting forbidden edit, shell, network, or delegation capabilities.
- Local overlay loaded from trusted revision only.
- Implementation diff rejection for workflows, dependencies, automation, and
  agent instruction files.
- Redaction of secret-shaped values in policy reports and error messages.

## Acceptance Criteria

- A remote bundle can choose only an approved named model profile.
- No configuration layer can broaden a capability granted by a higher layer.
- Review workflows stay read-only regardless of template or agent content.
- Implementation workflow changes are independently constrained and validated.
- Maintainers can inspect the final model, tool, and policy decisions for each
  run.
