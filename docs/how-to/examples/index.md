# Consumer wrapper examples

These examples show how a consumer repository can call the reusable workflows
in this repository through `workflow_call`. Each wrapper is a thin local
workflow that owns its own trigger, least-privilege permissions, and secret
forwarding. The reusable workflow owns validation, model invocation, and
publication.

> Replace `<pinned-sha>` with a real, reviewed commit SHA or protected release
> tag of this repository before adopting these examples in production. Do not
> copy the placeholder into a production workflow.

## Caller/callee responsibility split

| Concern | Owned by | How |
| --- | --- | --- |
| Event triggers and filtering | Caller | `on:` block in the wrapper |
| `permissions:` declarations | Caller | `permissions:` in the wrapper job |
| Provider secret forwarding | Caller | `secrets:` in the `uses:` call |
| Input validation and bounds | Callee | `Resolve invocation` step |
| Configuration profile selection | Caller | `configuration_profile` input |
| Model invocation and output parsing | Callee | `scripts/run_agentic_feedback.py` |
| Publication (comments, reviews, PRs) | Callee | gated by `dry_run`/`validate_only` |
| Provenance artifact | Callee | `actions/upload-artifact` step |

The reusable workflow documents its required permissions and fails with a
clear error if the caller's token cannot perform a required GitHub operation.
A caller may grant *fewer* permissions than the callee requests (for example,
to run `validate_only` with read-only access), but never more than the callee
declares.

## Ready-to-copy configuration-source examples

For complete pull-request documentation-review and issue-feedback workflows
for each supported configuration source, copy one of the directories described
in the [configuration-source examples](configuration-sources/index.md).
The established review and feedback examples contain real full commit-SHA
pins. The changelog example deliberately uses an invalid all-zero placeholder
that must be replaced before use. See the [configuration-source examples directory
table](configuration-sources/index.md) to choose the files to copy.

The established examples start with `validate_only: true`; set that explicitly
on the changelog example for a staged rollout. Follow the rollout instructions
in the configuration-source examples before enabling model invocation or
publication. See the [configuration reference](../../reference/configuration-reference.md)
for exact input definitions.

For the complete typed caller interface and the values that callers cannot
configure, see [Caller inputs in the configuration
reference](../../reference/configuration-reference.md#caller-inputs).

## Migration from direct triggers

The pull-request review and issue-feedback workflows support reusable calls;
issue implementation is manual-dispatch-only and has no reusable wrapper. To
migrate a consumer repository to the reusable form:

1. Pin this repository to a reviewed commit SHA in your wrapper's `uses:` line.
2. Copy the relevant wrapper example into `.github/workflows/` in your repository.
3. Adjust the `on:` trigger and `permissions:` to match your policy.
4. Forward only the provider secret the callee needs (`OPENROUTER_API_KEY`).
5. Start with `validate_only: true`, then `dry_run: true`, then normal
   publication to build confidence before enabling write-capable workflows.
