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
| Bundle manifest schema | `bundle.json` `schema_version` | `1`, `2` |
| Command registry | `scripts/agentic_commands.py` `REGISTRY_VERSION` | `1` |
| Output contracts | contract identifier suffix, e.g. `-v1` | `pr-review-json-v1`, `issue-feedback-markdown-v1`, `issue-implementation-decision-v1`, `release-project-issue-v1`, `release-project-analysis-handoff-v1` (non-publishing) |
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
  `manifest_sha256`, `schema_version`)
- `prompt_template_sha256`, `output_contract`, `model_profile`,
  `effective_policy_sha256`
- `mode` (`publish`|`dry-run`|`validate-only`)
- `result` (`validated`|`generated`|`published`|`skipped`|`failed`)
- `configuration_digest` (deterministic SHA-256 over stable config fields)
- for midflight runs: `registry_version`, `command_list_sha256`,
  `model_phase_count`, `isolation_profile`, and `phases` (per-phase status and
  result hash) — enough to establish which reviewed commands and phases ran
  without retaining raw prompts, model responses, command output, release
  bodies, or secrets.

It **never** contains provider credentials, token values, full model prompts,
complete issue text, unredacted diffs, raw model responses, raw command
output, or release bodies/notes. For resolution failures that occur before a
complete record exists, the runner emits a minimal redacted
attempted-resolution record with `result: "failed"` and a non-secret error
message.

For a feedback-generating `dry_run`, the same short-retention artifact also
includes `agentic-publication-preview*.json`. This is the validated payload
that publication would use (the comment/review/issue body, plus any inline
review comments). Treat it as workflow output with the same access controls as
the target content; it intentionally is not included in redacted provenance.

The configuration digest backs the v2 idempotency marker. A configuration
change (different profile, manifest hash, prompt template hash, output
contract, or model profile) produces a different digest, which triggers a
fresh review for the same PR head or issue.

## Idempotency and feedback identity

| Workflow | Marker | Suppression rule |
| --- | --- | --- |
| PR review | `<!-- agentic-workflow:pr-documentation-review:v2:<digest>:<head_sha> -->` | same digest AND same head SHA |
| Issue feedback | `<!-- agentic-workflow:issue-feedback:v2:<digest> -->` | same digest |
| Issue implementation status | `<!-- agentic-workflow:issue-implementation-status:v1 -->` | records the result of an explicitly dispatched issue run |
| Release project review | `<!-- agentic-workflow:release-project-review:v2:<idempotency_key>:<target_commit_sha> -->` | same idempotency key (canonical target repository, release ID, target commit SHA, configuration digest, workflow version) |

The release project-review idempotency key is derived from the canonical
target repository, the GitHub release ID, the immutable target commit SHA, the
configuration digest, and the workflow version. A change in any of those
fields produces a fresh marker and may create one new release-readiness issue;
the same tuple produces at most one issue.

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

## Concurrency and duplicate-issue prevention

The release project-review runner owns the deterministic idempotency marker
(canonical target repository, release ID, target commit SHA, configuration
digest, workflow version) and searches open and closed issues for it before
creating an issue. Even so, two publication runs that start simultaneously for
the same release/config can race past the marker search before either creates
its issue. To prevent duplicate release-readiness issues:

- Wrap consumer dispatch wrappers in a `concurrency` group keyed on the
  canonical target repository and release selector (release ID or tag), with
  `cancel-in-progress: false` so a run in flight is not cancelled mid-
  publication. For example:

  ```yaml
  concurrency:
    group: release-review-${{ github.repository }}-${{ inputs.release_id || inputs.release_tag }}
    cancel-in-progress: false
  ```

- For scheduled external-target review, include the target repository in the
  group key so concurrent reviews of different targets are not serialized.
- The midflight command/model phases do not weaken the existing idempotency
  identity: the configuration digest includes the command-list hash and
  effective-policy hash, so a midflight configuration change produces a fresh
  marker and may legitimately create a new issue. Concurrency control must
  therefore key on the same release/config identity the marker uses.
- If a same-repository wrapper cannot use `concurrency`, prefer a single
  dispatcher job that serializes release-review publication.

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
| Release project review (same repo) | At most one `release-readiness` issue in the reviewed repository; the resolved immutable target commit is checked out read-only. |
| Release project review (external target) | Requires an explicitly forwarded `release_target_token` with `contents: read` and `issues: write` on the target only; read access is verified before checkout and publication. |
| Release project review idempotency | An existing matching marker suppresses duplicate issue creation (`result: "skipped"`). |

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

## Release project-review rollout

The supplied release project-review examples run on published releases and
start in `validate_only` mode. Promote in this order:

1. `validate_only: true` — resolve and validate configuration and policy only;
   no release fetch, checkout, command execution, model invocation, or
   publication. Configuration and policy resolution run before the
   validate-only stop, so an invalid `midflight_commands` ID, count, phase, or
   overlap is caught without a release fetch.
2. `dry_run: true` — fetch the release, check out the immutable target commit,
   run the preflight, both model phases, and any midflight commands, validate
   the contract decision, but do not search for or create an issue.
3. publish — search for the idempotency marker; create at most one
   `release-readiness` issue in the canonical target repository when the
   decision is `CREATE_ISSUE`.

`midflight_commands` ships disabled (empty) in the supplied schema-2 bundle.
Opt in a single low-risk command only after dry-run evaluation confirms
isolation, latency, bounded evidence, phase quality, and redaction. Rollback
is configuration-first: pin the last known-good workflow/bundle SHA or empty
`midflight_commands`. Never fall back automatically to mutable refs, an
unknown schema, an unisolated executor, or a one-stage model decision after a
midflight failure.

For cross-repository reviews, forward a target-scoped token as
`release_target_token` (a GitHub App installation token or fine-grained token
with `contents: read` and `issues: write` on the target repository only). The
caller's `GITHUB_TOKEN` is never assumed to have access outside its
repository; read access is verified before checkout and publication, and the
same target-scoped token is used for issue creation.

The separate `external-release-project-review.yml` example runs daily for a
cross-repository check. Before enabling it, replace both `OWNER/REPOSITORY`
placeholders and configure `release_target_token`. The job reviews only the
newest non-draft target release published during the preceding 24 hours.
It also supports manual dispatch with a required `target_repository` and an
optional `release_id`; supplying an ID skips discovery, while omitting it
reviews the target's latest published release.
