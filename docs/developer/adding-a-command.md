# Add a preflight or midflight command

This procedure is for maintainers of Generic Agentic Workflows. It adds a
workflow-owned command capability for `release-project-review`; it does **not**
let a configuration bundle, caller, or model supply shell text.

Use an existing command whenever possible. A new command changes the workflow's
security boundary and requires review, tests, documentation, and a new pinned
workflow revision.

## 1. Decide whether the command is eligible

Before editing the registry, document why the command is needed and review its
complete execution path. The command argument vector, invoked executable,
target-controlled inputs, generated files, tool plugins, dependency behavior,
and artifact checks are all in scope.

A candidate must meet these requirements:

- It supports only `release-project-review`.
- Its argv is fixed and can run directly without a shell, interpolation, pipes,
  redirects, or caller/model supplied arguments.
- It can run in a disposable checkout with no credentials, no stdin, bounded
  output, and the shared resource limits.
- Its useful artifacts can be described by a fixed, small relative-path
  allowlist, or it needs no artifacts.
- Nonzero exit and timeout behavior are explicitly classified as evidence or a
  safety failure.

For **midflight** support, apply the additional requirement: an enforceable,
reviewed OS-level network-isolation mechanism must protect the command
workspace. The executor's `network="disabled"` declaration alone is not an
OS-level network block. Do not approve a midflight command until the isolation
self-check and the command's isolation behavior are demonstrated on the hosted
runner.

## 2. Add an immutable registry entry

In `scripts/agentic_commands.py`, add a `CommandSpec` entry to `REGISTRY`. Give
it a stable, descriptive ID and define every execution property in code:

- `workflow="release-project-review"`;
- `phases=("preflight",)` or `phases=("preflight", "midflight")`;
- a fixed `argv` tuple;
- timeout and output ceilings suitable for the command;
- only required fixed environment variables;
- `network="disabled"`;
- narrowly scoped artifact paths, if needed; and
- `allow_repeat=True` only when running the same command in both phases is
  justified.

Do not add a registry feature that accepts an argv, environment, working
directory, timeout, artifact glob, URL, or executable from a bundle or model.
Bump `REGISTRY_VERSION` when the registry entry shape or its compatibility/
provenance semantics change as described in `scripts/agentic_commands.py`.

## 3. Keep policy restrictive

Update the `release-project-review` `workflow_commands` policy in
`scripts/agentic_policy.py` to include the new ID in `allowed_registry_ids`.
Do not weaken any phase, count, isolation-profile, time, output, model-shell,
or delegation restriction to accommodate a command.

The effective policy is an upper bound. A bundle can select only IDs approved
by both the policy and registry, and an overlay may only remove authority. Keep
`max_commands_per_phase`, `max_commands_total`, and the total resource ceilings
consistent with the new command's worst-case use.

## 4. Validate configuration behavior

Schema-2 bundles select command IDs in `preflight_commands` and
`midflight_commands`. `scripts/agentic_configuration.py` must continue to
reject unknown, duplicate, wrong-phase, or cross-workflow IDs during
configuration resolution, including `validate_only` runs.

When adding preflight support, verify the schema-2 ID resolves to the registry
entry. When adding midflight support, verify all of the following:

- the ID permits the `midflight` phase in the registry;
- the effective policy permits the ID and `midflight` phase;
- the command is rejected in schema 1;
- an overlapping preflight/midflight selection is rejected unless
  `allow_repeat=True`; and
- an empty `midflight_commands` list still preserves the one-phase path.

Do not enable a new ID in the supplied bundle by default. Add it to a reviewed,
consumer-owned schema-2 profile only after validation and dry-run evaluation.

## 5. Test the executor and runner

Add focused tests in `tests/test_agentic_commands.py`,
`tests/test_agentic_configuration.py`, `tests/test_agentic_policy.py`, and
`tests/test_run_agentic_release_project_review.py` as applicable. At minimum,
cover:

1. exact direct argv execution, with no shell and no stdin;
2. rejection of unknown IDs, invalid phases, policy-denied IDs, and excess or
   duplicate selections;
3. credential-free environment, disposable workspace cleanup, timeout/process
   group handling, resource-limit failures, and bounded output;
4. artifact containment, symlink/file-type rejection, and metadata-only
   reporting where artifacts are declared;
5. expected pass, nonzero-exit, timeout, and safety-error results; and
6. for midflight, two fresh model phases, validated handoff/evidence delimiters,
   a failing isolation self-check, and the successful reviewed isolation path.

Run the full test suite before proposing the change. The supplied command
configuration must remain valid and existing schema-1 compatibility tests must
continue to pass.

## 6. Update user and maintainer documentation

Update [Run commands from a configuration bundle](../how-to/running-commands.md)
and the command entries in the reference documentation with the new ID, phase,
prerequisites, fixed behavior, artifacts, and any target-project assumptions.
Update the [security model](../explanation/security-model.md) if the command
changes the threat model or isolation guarantees.

Keep this maintainer guide and the command registry comments accurate. State
plainly if a command is preflight-only. Do not describe midflight support as
enabled until a registry entry, restrictive policy, and enforceable network
isolation all permit it.

## 7. Roll out safely

1. Land the registry, policy, tests, and documentation in a reviewed workflow
   revision; do not add the command to a default profile yet.
2. Resolve a schema-2 test profile with `validate_only: true` to verify manifest
   and policy checks without fetching a release or executing a command.
3. Run the command in `dry_run: true`, inspect redacted provenance and bounded
   evidence, and confirm that no issue is published.
4. For midflight, review the isolation self-check and two-phase behavior on the
   intended runner before any canary.
5. Enable one reviewed profile or wrapper first, then expand only after the
   canary behaves as expected.

To roll back, remove the command ID from the selected bundle or pin the caller
to the last known-good workflow revision. Never fall back to arbitrary shell
text, mutable refs, or weaker isolation.
