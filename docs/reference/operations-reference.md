# Operations reference

This reference describes the versions, provenance, identity, reliability, and
failure behavior of the agentic workflows. For operational procedures, see the
[operations guide](operations-guide.md).

## Independently versioned artifacts

| Artifact | Version source | Current value |
| --- | --- | --- |
| Reusable workflows | Immutable commit pin in consumer `uses:` | Per release tag |
| Bundle manifest schema | `bundle.json` `schema_version` | `1`, `2` |
| Command registry | `scripts/agentic_commands.py` `REGISTRY_VERSION` | `1` |
| Output contracts | Contract identifier suffix, such as `-v1` | `pr-review-json-v1`, `issue-feedback-markdown-v1`, `issue-implementation-decision-v1`, `release-project-issue-v1`, `release-project-analysis-handoff-v1` (non-publishing) |
| Policy schema | `.opencode/policy/organization-policy.json` `schema_version` | `1` |
| Feedback marker | Marker schema version field | `v2` |

## Configuration provenance

Every run writes `resolved-agentic-configuration.json`,
`effective-policy.json`, and `resolved-agentic-provenance.json` to
`$RUNNER_TEMP`, then uploads them as 14-day artifacts. Provenance is emitted on
every path, including `validate_only`, resolution failures, contract failures,
and provider failures.

The provenance record contains:

- `workflow_version`, `workflow_name`, and `caller_repository`;
- `target` (`kind`, `number`, and `head_sha`);
- `bundle` (`source_alias`, `repository`, `resolved_sha`, `profile`,
  `manifest_sha256`, and `schema_version`);
- `prompt_template_sha256`, `output_contract`, `model_profile`, and
  `effective_policy_sha256`;
- `mode` (`publish`, `dry-run`, or `validate-only`);
- `result` (`validated`, `generated`, `published`, `skipped`, or `failed`);
- `configuration_digest`; and
- for midflight runs, `registry_version`, `command_list_sha256`,
  `model_phase_count`, `isolation_profile`, and per-phase status and result
  hashes.

It never contains provider credentials, token values, complete prompts,
complete issue text, unredacted diffs, raw model responses, raw command output,
or release bodies. Early resolution failures produce a minimal redacted record
with `result: "failed"` and a non-secret error message.

A feedback-generating `dry_run` artifact also includes
`agentic-publication-preview*.json`, the validated payload that publication
would use. It is not part of redacted provenance and requires the same access
controls as the target content.

## Idempotency markers

| Workflow | Marker | Suppression rule |
| --- | --- | --- |
| PR review | `<!-- agentic-workflow:pr-documentation-review:v2:<digest>:<head_sha> -->` | Same digest and head SHA |
| Issue feedback | `<!-- agentic-workflow:issue-feedback:v2:<digest> -->` | Same digest |
| Issue implementation status | `<!-- agentic-workflow:issue-implementation-status:v1 -->` | Records the result of an explicitly dispatched issue run |
| Release project review | `<!-- agentic-workflow:release-project-review:v2:<idempotency_key>:<target_commit_sha> -->` | Same canonical target, release ID, target commit SHA, configuration digest, and workflow version |

A changed remote bundle SHA or changed local bundle content creates a new
configuration digest. A new digest intentionally creates a new feedback
identity.

## Operational test matrix

| Scenario | Expected result |
| --- | --- |
| Local bundle | Validated configuration and normal publication; the v2 marker carries the digest. |
| Pinned remote bundle | Provenance includes the matching `resolved_sha`; publication proceeds normally. |
| Mutable or unknown source | Resolution fails before OpenCode with a redacted `ConfigurationError`. |
| PR-controlled configuration attempt | Trusted base or allowlisted configuration remains in use; PR head content does not select the bundle. |
| `validate_only` | Artifact and summary only, including validated provenance; no provider call or GitHub write. |
| `dry_run` | Optional model call; no GitHub write, git push, or PR creation. |
| Policy escalation attempt | Rejected with a redacted `PolicyError`; no publication. |
| Provider or API transient error | Bounded retry for network and 5xx errors; failed run is diagnosable; no partial feedback. |
| Configuration rollback | The next run uses only the explicitly updated pinned SHA. |
| Implementation modifies denied paths | `enforce_changed_paths` blocks push and PR creation; the status comment reports failure. |
| Release project review, same repository | At most one `release-readiness` issue; the immutable target commit is checked out read-only. |
| Release project review, external target | Requires `release_target_token` with target-only `contents: read` and `issues: write`; access is verified before checkout and publication. |
| Release project review idempotency | A matching marker suppresses duplicate issue creation with `result: "skipped"`. |

## Runtime reliability controls

| Control | Bound |
| --- | --- |
| Remote fetch retries | `FETCH_MAX_RETRIES = 3` for transient failures only |
| Remote fetch timeout | `FETCH_TIMEOUT_SECONDS = 30` per request |
| Remote response size | `FETCH_RESPONSE_BYTES = 4 MiB` per file |
| Bundle file count | `MAX_BUNDLE_FILES = 64` |
| Per-file content size | `MAX_FILE_BYTES = 1 MiB` |
| Total bundle content | `MAX_TOTAL_BYTES = 8 MiB` |
| Provider wall-clock time | `effective_policy.timeout_seconds` (180–300 seconds) |
| Provider retries | `effective_policy.max_retries` (default `1`) |
| Prompt template size | `MAX_TEMPLATE_BYTES = 32 KiB` |
| Rendered prompt size | `MAX_RENDERED_BYTES = 2 MiB` |
| Untrusted content size | `MAX_UNTRUSTED_BYTES = 1200 KiB`, truncated with a marker |
| Artifact retention | 14 days |

Retries apply only to transient GitHub or provider failures. Manifest
validation, malformed model output, rejected policy, and permission errors are
not retried.

## Caching

Remote bundle content is cached under `$RUNNER_TEMP/agentic-bundle-cache`, keyed
by repository, resolved SHA, and profile. Cache hits pass through the same hash
and schema validation as fresh fetches. A corrupted entry is removed and
refetched. The cache lasts for one workflow run.

## Failure modes

| Failure | Behavior |
| --- | --- |
| Resolution or validation | Fails closed without falling back to another ref, local profile, or stale configuration. |
| Provider | Records a redacted operational failure and posts no partial feedback. |
| Publication | Preserves provenance with `result: "failed"` for investigation. |
| Duplicate feedback | Skips publication and records `result: "skipped"`. |
| Rollback | Requires a maintainer-controlled pinned SHA change; automatic unreviewed rollback is unsupported. |
