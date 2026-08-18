# Operate agentic workflows

Use this guide to release, recover, roll back, and safely roll out the reusable
workflows. For version tables, provenance fields, idempotency markers,
reliability limits, and failure behavior, see the [operations
reference](operations-reference.md).

## Release process

1. Tag a protected release on a protected branch; never release from a PR head.
2. Have consumers pin `uses:` to a reviewed commit SHA or protected release
   tag, never `main` or another mutable branch.
3. Publish a compatibility table mapping the workflow release to bundle,
   contract, policy, command-registry, and marker schema versions.
4. Roll out local bundles first, then remote bundles behind the organization
   allowlist. Use `validate_only` first, then `dry_run`, then normal
   publication; enable issue implementation last.

## Respond to an incident

1. **Stop the affected workflow.** Remove the consumer wrapper trigger or set
   `validate_only: true`.
2. **Capture provenance.** Download the `resolved-invocation-*`,
   `resolved-configuration-*`, and `resolved-agentic-provenance.json` artifacts
   from the failed run before they expire.
3. **Identify the layer.** Use the provenance record's `result` and error fields
   to distinguish resolution (`ConfigurationError`), policy (`PolicyError`),
   contract (`ContractError`), provider, and publication failures.
4. **Do not auto-rollback.** Explicitly pin a known-good commit SHA; do not let
   automation select a bundle revision.

## Roll back manually

1. Identify the last known-good bundle commit SHA from the compatibility table
   or a successful run's provenance artifacts.
2. Update the consumer wrapper's `configuration_ref` to that 40-character SHA
   for a remote bundle, or revert local bundle files on the trusted default
   branch.
3. Re-run with `validate_only: true`, then `dry_run: true`, then normal
   publication.
4. Confirm that the new provenance record contains the intended `resolved_sha`
   and `configuration_digest`.

Automatic, unreviewed rollback is intentionally not supported.

## Prevent duplicate release-readiness issues

The `release-project-review` runner searches open and closed issues for its
deterministic idempotency marker before creating an issue. Two publication runs
that start simultaneously can still race past that search. Add a `concurrency`
group to the consumer dispatch wrapper:

```yaml
concurrency:
  group: release-review-${{ github.repository }}-${{ inputs.release_id || inputs.release_tag }}
  cancel-in-progress: false
```

For a scheduled external-target review, include the target repository in the
group key. If a same-repository wrapper cannot use `concurrency`, use a single
dispatcher job to serialize publication.

## Roll out release project review

The supplied release project-review examples begin in validation-only mode.
Promote them in this order:

1. Set `validate_only: true` to resolve and validate configuration and policy.
   This does not fetch a release, check out a target, execute commands, invoke
   the model, or publish. It still rejects an invalid `midflight_commands` ID,
   count, phase, or overlap.
2. Set `dry_run: true` to fetch the release, check out the immutable target
   commit, run the preflight, invoke the configured model phases, and validate
   the decision without searching for or creating an issue.
3. Remove `dry_run` to search for the idempotency marker and, for a
   `CREATE_ISSUE` decision, create at most one `release-readiness` issue in the
   canonical target repository.

The supplied schema-2 bundle has an empty `midflight_commands` list. No command
is currently approved for that phase. Roll back by emptying the list or pinning
the last known-good workflow and bundle SHAs. Never fall back to mutable refs,
an unknown schema, an unisolated executor, or a one-stage decision after a
midflight failure.

For cross-repository review, forward `release_target_token`, scoped to
`contents: read` and `issues: write` on the target repository only. The
caller's `GITHUB_TOKEN` is not assumed to have access outside its repository.

Before enabling the separate `external-release-project-review.yml` example,
replace both `OWNER/REPOSITORY` placeholders and configure
`release_target_token`. Its scheduled run reviews only the newest non-draft
target release published during the preceding 24 hours. Manual dispatch
requires `target_repository` and optionally accepts `release_id`; omitting the
ID selects the target's latest published release.
