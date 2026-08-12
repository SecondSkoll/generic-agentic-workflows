# Migration and deprecation

This document covers migrating from the legacy Plan 1 `CUSTOM_AGENT_FILE` and
`CUSTOM_SKILL_FILE` variables to versioned configuration bundles (Plan 2), and
the published deprecation/removal schedule.

## Legacy variables

The reusable workflows historically accepted two repository environment
variables:

```yaml
CUSTOM_AGENT_FILE: .opencode/agents/default-agent.md
CUSTOM_SKILL_FILE: .opencode/skills/basic-review/SKILL.md
```

These remain functional during the documented migration window for local
sources only. When the workflow detects that both files exist and no
`configuration_profile` bundle is available, it builds a synthetic legacy
bundle via `scripts/agentic_configuration.py --legacy-agent-file
--legacy-skill-file` and emits a deprecation warning:

```
::warning::CUSTOM_AGENT_FILE/CUSTOM_SKILL_FILE are deprecated; use a
configuration bundle (configuration_profile). These variables will be removed
in the next major workflow release after the published migration window.
```

Legacy resolution:

- Validates the files live inside the trusted checkout and are not symlinks.
- Parses agent/skill front matter (required `name`).
- Enforces the same review-workflow read-only rules (no `edit: allow` for
  PR review or issue feedback).
- Produces a provenance record. The legacy path emits the **v1** idempotency
  marker (`<!-- agentic-workflow:<kind>:v1[:<head_sha>] -->`) for back-compat,
  while the integrated bundle path emits the **v2** marker carrying the
  configuration digest. Existing v1 markers are still parsed during migration
  but never match a v2 digest, so migrating from legacy variables to a bundle
  intentionally triggers re-review.

## Deprecation timeline

| Phase | Status | Behavior |
| --- | --- | --- |
| Phase 1 (current) | Available | Legacy variables work for local sources with a deprecation warning. Telemetry: warning emitted to the job log. |
| Phase 2 | Announced | Deprecation date published in this document and the README. Bundles are the documented default. |
| Phase 3 | Removed | Legacy variables rejected with an actionable error in the next major workflow release. |

> **Deprecation date:** the legacy variables will be removed in the first major
> workflow release after **the migration window closes**. Consumers must move
> to a `configuration_profile` bundle before that release. The exact removal
> release will be pinned in the compatibility table at least one minor release
> before removal.

## Migration steps

1. Create a local bundle directory under `.opencode/configuration/<profile>/`
   with `bundle.json`, `agent.md`, `skills/<name>/SKILL.md`, `prompts/<name>.md`,
   and `hashes.json`. See `.opencode/configuration/documentation-review/` for a
   complete example.
2. Set `configuration_profile: <profile>` in your wrapper's `with:` block and
   remove `CUSTOM_AGENT_FILE`/`CUSTOM_SKILL_FILE` from the job environment.
3. Run with `validate_only: true` to confirm the bundle resolves and hashes
   verify.
4. Run with `dry_run: true` to confirm prompt composition and contract
   validation.
5. Switch to normal publication.

## Telemetry

The deprecation warning is emitted to the GitHub Actions log as
`::warning::<message>`. It does not include secrets. The provenance record's
`source_alias` is `local` and `profile` is `legacy` for legacy runs, which
makes legacy usage measurable in the artifacts.

## Reversibility

Legacy support is reversible: a consumer can move to a bundle and back to
legacy variables during the migration window. After Phase 3, only bundles are
supported. Pin to a workflow release before Phase 3 to keep legacy behavior
while preparing migration.
