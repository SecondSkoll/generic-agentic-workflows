# Run issue implementation

Use the dispatch-only issue-implementation workflow to request an initial pull
request for an open issue in this repository. The workflow is not reusable via
`workflow_call` and is not available as a remote action.

## Prerequisites

- Add `OPENROUTER_API_KEY` as a repository Actions secret.
- Keep `.github/workflows/opencode-issue-implementation.yml` on the trusted
  default branch.
- Keep the local `default-implementation` configuration profile available.
- Select an open issue that is not a pull request and has enough detail for a
  safe initial implementation.
- The workflow's top-level permissions are `contents: read`, `issues: write`,
  and `pull-requests: write`. Its implementation job elevates `contents` to
  `write` so it can push the proposed branch.

## Dispatch the workflow

1. Open the repository's **Actions** page.
2. Select **OpenCode issue implementation**.
3. Select **Run workflow**.
4. Enter the positive issue number in `issue_number`.
5. Start the run.

The workflow rejects a closed issue, a pull request number, or a target that is
not a positive issue number.

## Check the result

Review the selected issue for a status comment containing the marker
`issue-implementation-status:v1`.

- If the agent produced an implementation, the comment links to an initial
  pull request. Review it as a proposal, not an approved change.
- If the issue needs clarification, the comment identifies the maintainer
  action needed before another dispatch.
- If the run failed, inspect the workflow logs and the 14-day
  `resolved-invocation-issue-implementation` and
  `resolved-configuration-issue-implementation` artifacts.

## Safeguards and recovery

Before push and pull-request creation, the workflow rejects changes to denied
paths, including workflow files, automation, dependencies, and agent, skill,
configuration, or policy instructions. It also rejects a run that produces no
implementation changes.

If a run fails or is blocked, do not bypass the path checks. Clarify the issue
or correct the trusted local configuration, then dispatch the workflow again.
For broader incident response, use the [operations guide](operations-guide.md).
For bundle fields and the `default-implementation` mapping, see the
[configuration reference](../reference/configuration-reference.md).
