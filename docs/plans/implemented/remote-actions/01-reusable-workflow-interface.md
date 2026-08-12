# Plan 1: Reusable Workflow Interface

## Objective

Expose the existing pull-request review, issue-feedback, and issue-
implementation automations as secure reusable GitHub Actions workflows. A
consumer repository should retain control of event triggers, permissions, and
secret forwarding while selecting an approved agent configuration profile.

## Scope

This plan changes workflow interfaces and consumer integration. It does not
implement remote bundle fetching or prompt rendering; those are defined in
Plans 2 and 3.

Affected files:

- `.github/workflows/opencode-review.yml`
- `.github/workflows/opencode-issue-feedback.yml`
- `.github/workflows/opencode-issue-implementation.yml`
- new consumer-wrapper examples under `docs/examples/`
- `README.md`

## Design Decisions

### Caller-owned security boundary

The caller owns:

- event triggers and event filtering
- `permissions:` declarations
- concurrency controls
- forwarding provider secrets with `secrets:`
- whether publication, validation-only, or dry-run behavior is requested

The reusable workflow must document required permissions, but must not assume
that a caller grants broader privileges. Workflow code should fail with a clear
error if the token cannot perform its required GitHub operation.

### Immutable configuration selector

Use the following inputs for reusable workflows:

| Input | Type | Required | Purpose |
| --- | --- | --- | --- |
| `configuration_source` | string | no | `local` or an approved remote source alias; default `local`. |
| `configuration_ref` | string | no | Full 40-character commit SHA for a remote bundle. |
| `configuration_profile` | string | yes | Bundle profile name, constrained to a safe identifier. |
| `focus` | string | no | Allowlisted review focus; no arbitrary prompt text. |
| `max_comments` | number | no | Bounded feedback count, e.g. $0$–$20$. |
| `dry_run` | boolean | no | Resolve, validate, and generate output without publishing. |
| `validate_only` | boolean | no | Resolve and validate configuration only. |
| workflow-specific target | string/number | varies | PR or issue target where event context is unavailable. |

Do not expose raw prompt, agent path, skill path, model identifier, arbitrary
URL, or mutable reference inputs.

### Event context handling

Keep the current direct triggers for backward compatibility. For reusable
calls, workflows must accept only the minimal event-derived identifiers needed
to fetch a target (for example, a pull-request number) and re-query GitHub for
authoritative state. Do not trust a caller-provided head SHA, author login,
issue body, repository name, or API URL.

For the PR review workflow, preserve the `pull_request_target` safety model:
checkout the base SHA, fetch only the PR ref for diff generation, and never
execute code from the PR head.

## Implementation Steps

1. Add `on.workflow_call` to each existing workflow.
   - Define typed inputs and a `OPENROUTER_API_KEY` secret contract.
   - Keep existing direct triggers unchanged initially.
   - Set an output only where a caller needs stable metadata, such as a
     feedback publication state or generated PR URL.
2. Add an initial `Resolve invocation` step.
   - Normalize direct-trigger values and reusable-workflow inputs into
     environment variables.
   - Validate profile identifiers, booleans, numeric bounds, and target
     numbers before any checkout or model invocation.
   - Reject `configuration_ref` unless it is a full SHA when remote sourcing is
     enabled.
3. Refactor each workflow's target discovery.
   - PR reviews: resolve the PR through the API, then use the returned base
     SHA, head SHA, and author login.
   - Issue feedback: define whether the reusable form reviews one supplied open
     issue or all open issues; support one mode explicitly rather than using an
     ambiguous empty target.
   - Issue implementation: preserve the current selection logic, but make the
     label/request marker configurable only through a validated profile.
4. Add mode gates.
   - `validate_only` stops after configuration resolution and writes a result
     artifact/job summary.
   - `dry_run` can call OpenCode but cannot post comments, create branches,
     push commits, or open PRs.
   - Normal execution retains existing publication behavior.
5. Create minimal local wrapper examples.
   - Each wrapper declares its own trigger and least-privilege permissions.
   - Each calls a pinned workflow revision and forwards only the provider
     secret needed by the callee.
6. Document the caller/callee responsibility split and migration path.

## Example Consumer Wrapper Shape

The examples should demonstrate a thin wrapper conceptually equivalent to:

```yaml
jobs:
  review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-review.yml@<pinned-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: central
      configuration_ref: <configuration-bundle-sha>
      configuration_profile: documentation-review
      focus: documentation
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Use real release or commit pins in published documentation, not placeholders
that users may copy into production.

## Validation and Tests

- Verify each workflow remains valid YAML and retains its existing direct
  trigger behavior.
- Add fixture-based tests for input normalization and input rejection.
- Add an Actions test harness or manual verification workflow covering direct
  invocation and `workflow_call` invocation.
- Confirm a caller with read-only permissions can use `validate_only` but
  cannot accidentally publish feedback.
- Confirm missing caller permissions produce actionable errors.
- Confirm PR review execution still checks out only the trusted base revision.

## Acceptance Criteria

- A consumer can invoke each supported workflow through `workflow_call`.
- Callers control permissions and secret forwarding.
- Interfaces expose only typed, allowlisted customizations.
- Existing local workflows continue to work without consumer changes.
- Dry-run and validation-only modes cannot write GitHub state.
