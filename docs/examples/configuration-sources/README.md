# Configuration-source workflow examples

Each directory is a complete, copy-ready set of reusable workflow wrappers
for pull-request documentation review, issue feedback, and release project
review. Copy the contents of one directory into the root of a consumer
repository; in particular, retain its `.github/` path. The workflows call the
reusable actions in this repository at commit
`61ed1bbd34a878f3ae270b1e4ff027cf786b730b`.

| Directory | `configuration_source` | Additional files to copy | Use case |
| --- | --- | --- | --- |
| [`default/`](default/) | `default` | None | Use the supplied `documentation-review`, `issue-feedback`, and `release-project-review` profiles unchanged. |
| [`local/`](local/) | `local` | `.opencode/configuration/local-documentation-review/`, `.opencode/configuration/local-issue-feedback/`, and `.opencode/configuration/local-release-project-review/` | Keep repository-specific reviewed instructions with the consumer. |
| [`central/`](central/) | `central` | None | Use the centrally approved profiles hosted by this repository. |

Each directory contains `documentation-review.yml`, `issue-feedback.yml`, and
`release-project-review.yml`. Keep only the workflows needed by the consumer
repository.

All three examples start in `validate_only` mode so they can be enabled
safely. After verifying the resolution artifact, change `validate_only: true`
to `dry_run: true`, and then remove the flag to publish feedback. The
repository must define the `OPENROUTER_API_KEY` Actions secret before leaving
validation-only mode. Documentation review needs `pull-requests: write`;
issue feedback needs `issues: write`; release project review needs
`issues: write` and, for cross-repository reviews, a target-scoped
`release_target_token` with `contents: read` and `issues: write` on the target
repository.

`release-project-review.yml` is manual/reusable only (`workflow_dispatch` and
`workflow_call`). It examines a published GitHub release identified by a
release ID or tag and creates at most one `release-readiness` issue in the
reviewed repository when it finds material release-planning or
project-management gaps. It never runs automatically on a release event.

The remote workflow and remote configuration reference are pinned to complete
commit SHAs. Update both pins together only after reviewing a newer release.
The `release-project-review.yml` wrapper pins must point to a reviewed release
commit that contains both the reusable workflow
(`.github/workflows/opencode-release-project-review.yml`) and the supplied
`release-project-review` profile (`.opencode/configuration/release-project-review/`).
The placeholder pin shown in these examples (`61ed1bbd…`) predates the release
project-review work and is a format placeholder only; replace it with a real
reviewed release commit SHA before enabling the workflow.

> OpenCode configuration (`.opencode/`) is loaded once at startup and is not
> hot-reloaded. After changing any configuration in this directory, quit and
> restart OpenCode for the change to take effect.