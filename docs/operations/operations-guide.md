# Agentic workflow operations guide

This guide covers release/versioning, incident response, manual rollback, the
operational test matrix and configuration provenance. It applies to the
reusable workflows in `.github/workflows/opencode-*.yml` and the
dependency-free Python modules in `scripts/`.

## Versioning and independent schema versions

The following are versioned **independently**. Publish a compatibility table
with every release:

| Artifact | Version source | Current |
| --- | --- | --- |
| Reusable workflows | immutable commit pin in consumer `uses:` | per release tag |
| Bundle manifest schema | `bundle.json` `schema_version` | `1` |
| Output contracts | contract identifier suffix, e.g. `-v1` | `pr-review-json-v1`, `issue-feedback-markdown-v1`, `issue-implementation-decision-v1` |
| Policy schema | `.opencode/policy/organization-policy.json` `schema_version` | `1` |
| Feedback marker | marker schema version field | `v2` |

Release process:

1. Tag a protected release on a protected branch; never release from a PR head.
2. Consumers pin `uses:` to a reviewed commit SHA or protected release tag, never
   `main` or a mutable branch.
3. Publish a compatibility table mapping workflow release to bundle, contract,
   policy, and marker schema versions.
4. Roll out in this order: local bundles, then remote bundles behind the
   organization allowlist; `validate_only` first, then `dry_run`, then normal
   publication; issue implementation last.

## Configuration provenance

Every run writes a redacted `resolved-agentic-configuration.json` (bundle), an
`effective-policy.json` (policy), and a `resolved-agentic-provenance.json`
record to `$RUNNER_TEMP`, then uploads them as short-retention artifacts
(14 days). Provenance is emitted on **every** path, including
`validate_only`, configuration/policy resolution failures (a minimal redacted
attempted-resolution record), contract-validation failures, and provider
failures. The provenance record contains:

- `workflow_version`, `workflow_name`, `caller_repository`
- `target` (`kind`, `number`, `head_sha`)
- `bundle` (`source_alias`, `repository`, `resolved_sha`, `profile`,
  `manifest_sha256`)
- `prompt_template_sha256`, `output_contract`, `model_profile`,
  `effective_policy_sha256`
- `mode` (`publish`|`dry-run`|`validate-only`)
- `result` (`validated`|`generated`|`published`|`skipped`|`failed`)
- `configuration_digest` (deterministic SHA-256 over stable config fields)

It **never** contains provider credentials, token values, full model prompts,
complete issue text, unredacted diffs, or raw model responses. For resolution
failures that occur before a complete record exists, the runner emits a minimal
redacted attempted-resolution record with `result: "failed"` and a non-secret
error message.

The configuration digest backs the v2 idempotency marker. A configuration
change (different profile, manifest hash, prompt template hash, output
contract, or model profile) produces a different digest, which triggers a
fresh review for the same PR head or issue.

## Idempotency and feedback identity

| Workflow | Marker | Suppression rule |
| --- | --- | --- |
| PR review | `<!-- agentic-workflow:pr-documentation-review:v2:<digest>:<head_sha> -->` | same digest AND same head SHA |
| Issue feedback | `<!-- agentic-workflow:issue-feedback:v2:<digest> -->` | same digest |
| Issue implementation status | `<!-- agentic-workflow:issue-implementation-status:v1 -->` | existing status comment suppresses re-selection |

To intentionally re-review after a configuration change, update the pinned
bundle SHA (remote) or the local bundle content; the new digest produces a new
marker. To re-review the same configuration, delete the prior comment/review or
dispatch with `fail_if_reviewed` semantics disabled.

## Incident response

1. **Stop the bleeding.** Disable the affected reusable workflow in the
   consumer repository (remove the wrapper trigger or set `validate_only: true`).
2. **Capture provenance.** Download the `resolved-invocation-*`,
   `resolved-configuration-*`, and `resolved-agentic-provenance.json` artifacts
   from the failed run before they expire.
3. **Identify the layer.** The provenance record's `result` and error fields
   point to the failing layer: resolution (`ConfigurationError`), policy
   (`PolicyError`), contract (`ContractError`), provider, or publication.
4. **Do not auto-rollback.** Pin to a known-good commit SHA explicitly; never
   let automation switch the bundle ref for you.

## Manual rollback

1. Identify the last known-good bundle commit SHA from the compatibility table
   or the provenance artifacts of a successful run.
2. Update the consumer wrapper's `configuration_ref` to that 40-character SHA
   (remote) or revert the local bundle files in the trusted default branch.
3. Re-run with `validate_only: true`, then `dry_run: true`, then normal
   publication.
4. Confirm the new provenance record shows the intended `resolved_sha` and
   `configuration_digest`.

Automatic, unreviewed rollback is intentionally not supported.

## Operational test matrix

| Scenario | Expected result |
| --- | --- |
| Local bundle | Validated configuration and normal publication; v2 marker carries the digest. |
| Pinned remote bundle | Matching provenance record with `resolved_sha`; normal publication. |
| Mutable/unknown source | Resolution fails before OpenCode with a redacted `ConfigurationError`. |
| PR-controlled config attempt | Trusted base/allowlisted configuration remains in use; PR head content never selects the bundle. |
| `validate_only` | Artifact and summary only (including a `validated` provenance record); no model/provider call or GitHub write. |
| `dry_run` | Optional model call, but no GitHub write, git push, or PR creation. |
| Policy escalation attempt | Rejected with a redacted `PolicyError`; no publication. |
| Provider/API transient error | Bounded retry (5xx/network only); diagnosable failed run; no partial feedback. |
| Configuration rollback | Next run uses only the explicitly updated pinned SHA. |
| Implementation modifies denied paths | Push/PR creation blocked by `enforce_changed_paths`; status comment reports failure. |

## Runtime reliability controls

| Control | Bound |
| --- | --- |
| Remote fetch retries | `FETCH_MAX_RETRIES = 3` (transient only) |
| Remote fetch timeout | `FETCH_TIMEOUT_SECONDS = 30` per request |
| Remote response size | `FETCH_RESPONSE_BYTES = 4 MiB` per file |
| Bundle file count | `MAX_BUNDLE_FILES = 64` |
| Per-file content size | `MAX_FILE_BYTES = 1 MiB` |
| Total bundle content | `MAX_TOTAL_BYTES = 8 MiB` |
| Provider wall-clock | `effective_policy.timeout_seconds` (180–300s) |
| Provider retries | `effective_policy.max_retries` (default 1) |
| Prompt template size | `MAX_TEMPLATE_BYTES = 32 KiB` |
| Rendered prompt size | `MAX_RENDERED_BYTES = 96 KiB` |
| Untrusted content size | `MAX_UNTRUSTED_BYTES = 32 KiB` (truncated with a marker) |
| Artifact retention | 14 days |

Retry is bounded and applies only to transient GitHub/provider failures. The
runner does **not** retry manifest validation, malformed model output, rejected
policy, or permission errors.

## Caching

Remote bundle content is cached under `$RUNNER_TEMP/agentic-bundle-cache`,
keyed by `(repository, resolved_sha, profile)`. A cache hit is re-validated
through the full hash and schema pipeline exactly like a fresh fetch. A
corrupted cache entry is removed and refetched. Cache scope is a single
workflow run.

## Failure modes

- **Resolution/validation failure:** fail closed. No fallback to a different
  ref, local profile, or stale configuration.
- **Provider failure:** redacted operational failure; no partial feedback is
  posted.
- **Publication failure:** provenance record preserved with `result: "failed"`;
  GitHub retries/maintainers investigate.
- **Duplicate feedback:** skipped predictably; existing marker match recorded
  as `result: "skipped"`.
- **Rollback:** maintainer-controlled pinned SHA change only; no automatic
  unreviewed rollback.
