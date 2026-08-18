# Run commands from a configuration bundle

Configuration bundles can request a small set of workflow-owned commands by
using `preflight_commands` and, on schema 2, `midflight_commands`. This is
intended for release-readiness evidence, not general shell access.

Command execution is currently supported only by the
`release-project-review` workflow. The workflow runner—not the model—executes
each approved command through a pinned, immutable registry. The registry maps
stable command IDs to fixed argument vectors and execution policies; a bundle,
caller, or model may select an ID but may never supply argv, environment,
working directory, credentials, network destination, or publication target.
Agent configuration should continue to deny Bash access.

## Prerequisites and limits

A command must satisfy all of these requirements:

- The bundle's `allowed_workflows` must contain `release-project-review`.
- `preflight_commands` / `midflight_commands` must be a JSON array containing
  at most three strings. Schema 2 lists registry command IDs; schema 1 lists
  legacy shell strings resolved through compatibility aliases.
- Every ID must exist in the pinned registry and be approved for the requested
  phase (preflight or midflight).
- The required top-level executable must already be available on the
  GitHub-hosted runner. An approved project command may perform its normal,
  reviewed setup—for example, in the target checkout, the Sphinx Stack Makefile
  creates a virtual environment and installs `docs/requirements.txt`—but a
  bundle cannot add a separate installation command.

The currently registered commands are:

```text
documentation-build   -> make -C docs html       (preflight; repeatable)
python-pytest        -> python3 -m pytest       (preflight)
```

Schema 1 may also use the legacy shell strings (`make -C docs html`,
`python3 -m pytest`) through compatibility aliases; schema 2 uses IDs only.

Arguments, paths, environment assignments, pipes, redirects, command chaining,
and alternate spellings are not accepted. The Sphinx command is specifically
approved for a release checkout that uses the Sphinx Stack's `docs/Makefile`;
after it runs, the workflow also requires a non-empty
`docs/_build/index.html`. The Makefile may create its documented virtual
environment and install its pinned Python requirements. For example,
`python3 -m pytest tests/` is a different command and is rejected.

`documentation-build` applies only to consumer target repositories that provide
the Sphinx Stack `docs/Makefile`, which creates its own virtual environment and
installs `docs/requirements.txt`. This repository provides neither
`docs/Makefile` nor `docs/requirements.txt`; build its documentation with the uv
commands in the README.md "Building the documentation" section.

## Configure a bundle

Add `preflight_commands` (and, on schema 2, `midflight_commands`) to the
release-review profile's `bundle.json`:

```json
{
  "schema_version": 2,
  "profile_name": "release-readiness",
  "allowed_workflows": ["release-project-review"],
  "agent_file": "agent.md",
  "skill_files": ["skills/release-management/SKILL.md"],
  "prompt_template": "prompts/release-project-review.md",
  "model_profile": "release-project-review-readonly",
  "output_contract": "release-project-issue-v1",
  "limits": {},
  "preflight_commands": ["python-pytest"],
  "midflight_commands": [],
  "policy": {
    "capabilities": {
      "filesystem": "read-release-context-only",
      "github_write": "issue-create-only",
      "delegation": "deny"
    }
  }
}
```

Do not grant the agent shell access. A release-review agent should retain
read-only permissions such as:

```yaml
permission:
  edit: deny
  bash: deny
  read: allow
```

`preflight_commands` is manifest configuration, not a declared content file,
so it is not added to `hashes.json`. If agent, skill, or prompt content is also
changed in a remote or hash-locked local bundle, regenerate the content hashes:

```text
uv run scripts/update_hashes.py --profile release-readiness
```

## Validate the configuration

Resolve the bundle before deploying it:

```text
uv run python scripts/agentic_configuration.py \
  --workflow release-project-review \
  --bundle-root .opencode/configuration \
  --configuration-profile release-readiness \
  --result /tmp/release-readiness.json
```

Resolution verifies the field type, workflow compatibility, and three-command
limit. The command allowlist is enforced by the release runner before model
invocation. An unsupported command fails closed rather than being passed to a
shell or ignored.

## Enable the workflow

Select the configured profile from a release-project-review wrapper. For a
bundle stored in the consuming repository's trusted default branch:

```yaml
jobs:
  release-review:
    uses: organization/generic-agentic-workflows/.github/workflows/opencode-release-project-review.yml@<reviewed-workflow-sha>
    permissions:
      contents: read
      issues: write
    with:
      target_repository: ${{ github.repository }}
      release_id: ${{ github.event.release.id }}
      configuration_source: local
      configuration_profile: release-readiness
      dry_run: true
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

For a supplied or central bundle, use `configuration_source: default` or
`configuration_source: central` and provide the reviewed full commit SHA as
`configuration_ref`. See [Deploy a workflow](deploying-a-workflow.md) for the
complete source-specific setup and token requirements.

There is no separate caller input that enables commands. Selecting a valid
release-review bundle containing `preflight_commands` enables execution during
the review phase. Selecting `midflight_commands` (schema 2 only) enables the
two-stage interaction: a fresh analysis phase, the configured midflight
commands, then a fresh final assessment. When `midflight_commands` is empty or
absent the runner preserves the single-phase behavior.

**No command is currently approved for the midflight phase.** Every registered
command is preflight-only until an enforceable, reviewed OS-level network
isolation mechanism is demonstrated. Configuration resolution rejects any
`midflight_commands` ID because no registry entry permits the midflight phase.
The two-phase framework (registry, executor, handoff contract, policy,
provenance) remains in place so a future reviewed command can opt into
midflight after its isolation behaviour is demonstrated; the supplied schema-2
bundle ships with `midflight_commands` empty.

`validate_only: true` resolves configuration and policy but deliberately skips
release fetching, checkout, command execution, model invocation, and
publication. Configuration and policy resolution run before the validate-only
stop, so an invalid command ID, count, phase, or overlap is caught without a
release fetch. Use `dry_run: true` for the first execution test: it runs the
preflight, both model phases, and any midflight commands but suppresses issue
publication. After inspecting the run and its provenance, remove `dry_run` to
enable normal publication.

## Execution behavior

Preflight and midflight commands both run through the shared hardened
executor. Preflight runs in a fresh disposable copy of the resolved commit;
midflight runs in a fresh disposable copy per command. Both are discarded
before either model phase receives filesystem access. For each configured
command, the runner:

1. resolves the configured ID (or schema-1 alias) to the pinned registry spec;
2. starts the fixed argument vector directly without a shell, with no stdin;
3. constructs the environment from a fixed credential-free allowlist (no
   provider, GitHub, OIDC, SSH, proxy, or caller-secret variable);
4. starts the command in its own process group and terminates the whole group
   on timeout;
5. applies platform-supported CPU, address-space, file-size, process-count,
   and open-file resource limits, failing closed if a required limit cannot be
   applied;
6. streams stdout/stderr into a bounded ring buffer while reading (never
   unbounded `capture_output` followed by truncation);
7. limits execution to the registry-declared timeout (default 300 seconds); and
8. for `documentation-build`, verifies that `docs/_build/index.html` is a
   non-empty regular file.

Network posture is declared by registry policy: every registered command
declares `network="disabled"`, and the executor refuses any command that
requests network. Enforced OS-level denial (network namespace / locked-down
container) is the hosted runner's responsibility — the in-process executor
itself cannot technically block outbound connections, so a runner that does
not supply OS-level isolation must not enable midflight. Because no
enforceable OS-level network isolation has been demonstrated, **zero
midflight commands are currently approved** (see the warning above under
"Enable the workflow"); the in-process controls in this section apply to
preflight execution, where they defend against credential leakage, resource
exhaustion, and workspace mutation rather than against network egress.

A nonzero exit or ordinary timeout is recorded as evidence and does not by
itself fail the workflow. An execution-safety error (unknown command, failed
isolation, unavailable resource limit, capture failure) fails closed: the run
stops, no issue is published, and redacted failure provenance is written. The
result is supplied to the model as bounded, delimited untrusted data. It may
support a release-readiness issue only when the finding explains a concrete
release-management consequence and identifies an owner or action.

Invalid configuration, an unapproved command, a missing target checkout, or a
missing required executable fails the review before model invocation. The
model cannot alter a command, request another command, install software, or
execute any command itself.

## Troubleshooting

- **No command ran:** Confirm the selected profile contains
  `preflight_commands` / `midflight_commands` and the run is not using
  `validate_only: true`.
- **Unapproved release command:** Use a registered command ID (or the exact
  legacy shell string on schema 1); arbitrary flags and commands are
  intentionally unsupported.
- **Unknown manifest key:** Schema 1 and 2 reject unknown manifest keys. To
  add `midflight_commands`, migrate the bundle to schema 2.
- **`midflight_commands` rejected on schema 1:** The field is only supported by
  schema 2; bump `schema_version` to 2.
- **`python3` is required:** Ensure the runner provides Python 3. The reusable
  workflow sets up Python for non-validation runs.
- **Pytest cannot be imported:** The target project must make pytest available
  without a preflight installation step. Command execution does not grant
  dependency-installation capability.
- **The Sphinx build passed but the output check failed:** Confirm that the
  release's `docs/Makefile` writes its dirhtml output to
  `docs/_build/index.html`; missing, empty, and symbolic-link entry points are
  rejected.
- **Tests failed but the workflow continued:** This is expected. A test failure
  is evidence for the release review, while configuration or execution-safety
  failures stop the workflow.
- **Command repeated across preflight and midflight rejected:** A command may
  not appear in both phases unless its registry definition explicitly allows
  repeated execution (`documentation-build` does; `python-pytest` does not).
