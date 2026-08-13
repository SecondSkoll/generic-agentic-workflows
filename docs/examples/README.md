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

## Available wrappers

- [`opencode-review.yml`](./opencode-review.yml) — pull-request documentation review.
- [`opencode-issue-feedback.yml`](./opencode-issue-feedback.yml) — issue feedback.

## Typed inputs exposed to callers

| Input | Type | Required | Purpose |
| --- | --- | --- | --- |
| `configuration_source` | string | no | `default` (supplied profile), `local` (calling repository), or `central`; default `default`. |
| `configuration_ref` | string | no | Full 40-character commit SHA for a remote bundle. |
| `configuration_profile` | string | yes | Bundle profile name, constrained to `[a-z0-9][a-z0-9-]{0,62}`. |
| `focus` | string | no | Allowlisted review focus; no arbitrary prompt text. |
| `max_comments` | number | no | Bounded feedback count, `0`–`20` (PR review only). |
| `max_issues` | number | no | Bounded issue count, `1`–`100` (issue feedback only). |
| `dry_run` | boolean | no | Resolve, validate, and generate output without publishing. |
| `validate_only` | boolean | no | Resolve and validate configuration only. |
| `pull_number` / `issue_number` | number | no | Target number when event context is unavailable. |

The reusable workflows do **not** expose raw prompt, agent path, skill path,
model identifier, arbitrary URL, or mutable reference inputs. Any attempt to
supply them is rejected before checkout or model invocation.

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
