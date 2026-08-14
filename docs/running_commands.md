# Run commands from a configuration bundle

Configuration bundles can request a small set of workflow-owned commands by
using `preflight_commands`. This is intended for release-readiness evidence,
not general shell access.

Command execution is currently supported only by the
`release-project-review` workflow. The workflow runner—not the model—executes
each approved command in the immutable target release checkout. Agent
configuration should continue to deny Bash access.

## Prerequisites and limits

A command must satisfy all of these requirements:

- The bundle's `allowed_workflows` must contain `release-project-review`.
- `preflight_commands` must be a JSON array containing at most three strings.
- Every string must exactly match a command in the workflow's fixed allowlist.
- The required top-level executable must already be available on the
  GitHub-hosted runner. An approved project command may perform its normal,
  reviewed setup—for example, the Sphinx Stack Makefile creates a virtual
  environment and installs `docs/requirements.txt`—but a bundle cannot add a
  separate installation command.

The currently approved commands are:

```text
python3 -m pytest
make -C docs html
```

Arguments, paths, environment assignments, pipes, redirects, command chaining,
and alternate spellings are not accepted. The Sphinx command is specifically
approved for a release checkout that uses the Sphinx Stack's `docs/Makefile`;
after it runs, the workflow also requires a non-empty
`docs/_build/index.html`. The Makefile may create its documented virtual
environment and install its pinned Python requirements. For example,
`python3 -m pytest tests/` is a different command and is rejected.

## Configure a bundle

Add `preflight_commands` to the release-review profile's `bundle.json`:

```json
{
  "schema_version": 1,
  "profile_name": "release-readiness",
  "allowed_workflows": ["release-project-review"],
  "agent_file": "agent.md",
  "skill_files": ["skills/release-management/SKILL.md"],
  "prompt_template": "prompts/release-project-review.md",
  "model_profile": "release-project-review-readonly",
  "output_contract": "release-project-issue-v1",
  "limits": {},
  "preflight_commands": [
    "python3 -m pytest"
  ],
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
changed, regenerate the content hashes as usual:

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
the review phase.

`validate_only: true` resolves configuration but deliberately skips release
fetching, checkout, command execution, model invocation, and publication. Use
`dry_run: true` for the first execution test: it runs the preflight and model
but suppresses issue publication. After inspecting the run and its provenance,
remove `dry_run` to enable normal publication.

## Execution behavior

For each configured command, the runner:

1. maps the exact configured string to fixed argument-vector entries;
2. starts the process directly without a shell;
3. runs it from the immutable target release checkout;
4. supplies a minimal environment and no standard input;
5. limits execution to 300 seconds; and
6. captures at most 16 KiB of combined standard output and standard error; and
7. for `make -C docs html`, verifies that `docs/_build/index.html` is a
  non-empty regular file.

A nonzero exit or timeout is recorded as preflight evidence and does not by
itself fail the workflow. The result is supplied to the model as untrusted
data. It may support a release-readiness issue only when the finding explains
a concrete release-management consequence and identifies an owner or action.

Invalid configuration, an unapproved command, a missing target checkout, or a
missing required executable fails the review before model invocation. The
model cannot alter a command, request another command, install software, or
execute the preflight itself.

## Troubleshooting

- **No command ran:** Confirm the selected profile contains
  `preflight_commands` and the run is not using `validate_only: true`.
- **Unapproved release preflight command:** Use the exact approved string;
  arbitrary flags and commands are intentionally unsupported.
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
