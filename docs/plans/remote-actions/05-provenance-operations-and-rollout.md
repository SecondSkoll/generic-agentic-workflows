# Plan 5: Provenance, Operations, Documentation, and Rollout

## Objective

Make remote configuration runs reproducible, observable, supportable, and safe
to adopt. This plan covers artifacts, idempotency, failure handling, release
strategy, documentation, and operational acceptance testing.

## Scope

This plan builds on the reusable interfaces, resolver, prompt composer, and
policy engine defined in Plans 1–4. It does not define their internal schemas
beyond the fields needed for audit and runtime operations.

## Provenance Record

Generate a `resolved-agentic-configuration.json` record for every invocation,
including validation-only and failed resolution attempts where safe. It should
contain:

```json
{
  "workflow_version": "<pinned workflow revision>",
  "workflow_name": "pr-documentation-review",
  "caller_repository": "owner/repository",
  "target": {"kind": "pull_request", "number": 42, "head_sha": "..."},
  "bundle": {
    "source_alias": "central",
    "repository": "organization/agentic-configurations",
    "resolved_sha": "<40-char SHA>",
    "profile": "documentation-review",
    "manifest_sha256": "..."
  },
  "prompt_template_sha256": "...",
  "output_contract": "pr-review-json-v1",
  "model_profile": "review-readonly",
  "effective_policy_sha256": "...",
  "mode": "publish|dry-run|validate-only",
  "result": "validated|generated|published|skipped|failed"
}
```

Never record provider credentials, token values, full model prompts, complete
issue text, unredacted diffs, or raw model responses unless a separate,
explicitly approved diagnostic system manages their retention.

## Artifacts and Job Summaries

- Upload the provenance record as a short-retention artifact, such as 7–30
  days, appropriate to organizational policy.
- Emit a concise job summary with source repository, resolved SHA, profile,
  output contract, effective model profile, mode, and result.
- For validation failures, explain which non-secret rule failed and identify the
  profile/source involved.
- Link generated PRs, reviews, or issue comments only after successful
  publication.

Ensure artifacts are created even when `validate_only` is selected. For
resolution failures that occur before a complete record exists, emit a minimal
redacted attempted-resolution record.

## Idempotency and Feedback Identity

Extend the existing feedback marker carefully. It must continue to prevent
unintended duplicate feedback for the same PR head or issue request while
allowing an intentional re-review when a configuration version changes.

Recommended marker components:

- feedback kind and marker schema version;
- PR head SHA when applicable;
- resolved bundle manifest hash or a short deterministic configuration digest;
- output contract version.

Keep a compatibility check for existing `v1` markers during migration. Decide
per workflow whether a profile update should trigger new feedback by comparing
configuration digest and event revision. Document that behavior so maintainers
are not surprised by repeat comments.

## Runtime Reliability Controls

### Limits and timeouts

Set conservative, centrally configurable limits for:

- remote fetch count, response sizes, bundle file count, and total bytes;
- OpenCode wall-clock time, model retries, and generated output bytes;
- issue iteration count per run and comments per target;
- artifact retention and log verbosity.

Use bounded retry behavior only for transient GitHub/provider failures. Do not
retry manifest validation, malformed model output, rejected policy, or
permission errors.

### Caching

Cache only immutable content keyed by source repository, resolved commit SHA,
and manifest hash. Cache scope should be limited to a workflow run unless
cross-run caching can verify integrity before use. A cache hit must undergo
hash and schema validation just like a fresh fetch.

### Failure modes

- **Resolution/validation failure:** fail closed; do not use a different ref,
  local profile, or stale configuration automatically.
- **Provider failure:** report a redacted operational failure; avoid posting
  partial feedback.
- **Publication failure:** preserve the provenance record and return a failed
  status so GitHub retries/maintainers can investigate.
- **Duplicate feedback:** skip predictably and record the existing marker
  match.
- **Rollback:** permit a maintainer-controlled change to a known good pinned
  SHA; never perform automatic unreviewed rollback.

## Release and Versioning Strategy

1. Version the reusable workflows with immutable commit pins and protected
   release tags for human-friendly consumption.
2. Version manifest, prompt contract, policy schema, and feedback markers
   independently. Publish compatibility tables.
3. Release local-bundle support first, then remote bundles behind an
   organization allowlist.
4. Pilot in one low-risk consumer repository using `validate_only`, then
   `dry_run`, then normal PR review publishing.
5. Expand to issue feedback after operational telemetry is stable.
6. Enable issue implementation last, with manual dispatch first and explicit
   maintainer approval gates where appropriate.
7. Announce deprecation dates for legacy custom file variables only after
   migration examples and tooling are available.

## Documentation Deliverables

Update `README.md` and add focused guides for:

- local versus remote bundle configuration;
- source trust rules, SHA pinning, and allowed repositories;
- reusable workflow consumer wrappers and required permissions;
- typed prompt overrides, profile selection, dry-run, and validation-only
  modes;
- provider secret ownership and secret redaction;
- provenance artifact interpretation and idempotency behavior;
- incident response, manual rollback, and release upgrade steps;
- migration from `CUSTOM_AGENT_FILE` and `CUSTOM_SKILL_FILE`.

Provide a complete example central configuration repository with documentation
review, issue feedback, and issue implementation profiles. Its release process
should require code review, protected branches/tags, and an easy way to pin a
consumer to a known revision.

## Operational Test Matrix

Before broad rollout, verify:

| Scenario | Expected result |
| --- | --- |
| Local legacy configuration | Existing behavior plus deprecation warning. |
| Local bundle | Validated configuration and normal publication. |
| Pinned remote bundle | Matching provenance record and normal publication. |
| Mutable/unknown source | Resolution fails before OpenCode. |
| PR-controlled config attempt | Trusted base/allowlisted configuration remains in use. |
| `validate_only` | Artifact and summary only; no model/provider call or GitHub write. |
| `dry_run` | Optional model call, but no GitHub write, git push, or PR creation. |
| Policy escalation attempt | Rejected with a redacted validation error. |
| Provider/API transient error | Bounded retry and diagnosable failed run. |
| Configuration rollback | Next run uses only the explicitly updated pinned SHA. |

## Acceptance Criteria

- Each run leaves enough redacted provenance to reproduce its configuration.
- Operational failures are safe, bounded, and actionable.
- Users have documented examples for local and SHA-pinned remote consumption.
- Rollout proceeds from validation to low-risk publication before write-capable
  issue implementation.
- Legacy configuration deprecation is measurable, documented, and reversible
  before removal.
