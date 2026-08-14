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
| [`central/`](central/) | `central` | None | Use the centrally approved profiles hosted by the central config repository, defined by this repository. |

Each directory contains `documentation-review.yml`, `issue-feedback.yml`,
`release-project-review-self.yml`, and `external-release-project-review.yml`.
Keep only the workflows needed by the consumer repository.

All three examples start in `validate_only` mode so they can be enabled
safely. After verifying the resolution artifact, change `validate_only: true`
to `dry_run: true`, and then remove the flag to publish feedback. The
repository must define the `OPENROUTER_API_KEY` Actions secret before leaving
validation-only mode. Documentation review needs `pull-requests: write`;
issue feedback needs `issues: write`; release project review needs
`issues: write` and, for cross-repository reviews, a target-scoped
`release_target_token` with `contents: read` and `issues: write` on the target
repository.

`release-project-review-self.yml` runs when a release is published in the
repository containing the workflow. It forwards the event's release ID to the
reusable workflow and creates at most one `release-readiness` issue when it
finds material release-planning or project-management gaps.

`external-release-project-review.yml` runs daily at midnight UTC. Before using
it, replace `OWNER/REPOSITORY` in both the discovery job and reusable-workflow
inputs, and configure `release_target_token` with `contents: read` and
`issues: write` on that target repository. The scheduled job selects the most
recent non-draft target release published during the preceding 24 hours and
skips the review when no such release exists. It can also be dispatched
manually: provide `target_repository` and optionally `release_id`. A supplied
release ID bypasses discovery; without one, the workflow resolves the target's
latest published release.

The remote workflow and remote configuration reference are pinned to complete
commit SHAs. Each SHA must be reviewed in the repository it references. For
the `default` source, the configuration bundle is in this workflow repository,
so its `configuration_ref` matches the `uses:` SHA. For the `central` source,
`configuration_ref` instead pins the approved bundle in
`SecondSkoll/generic-agentic-workflows-config`; it is independent of the
workflow `uses:` SHA. The `release-project-review-self.yml` and
`external-release-project-review.yml` workflow pins must point to a reviewed
release commit containing
`.github/workflows/opencode-release-project-review.yml`. The placeholder
workflow pin shown in these examples (`61ed1bbd…`) predates the release
project-review work and is a format placeholder only; replace it with a real
reviewed release commit SHA before enabling the workflow.

> OpenCode configuration (`.opencode/`) is loaded once at startup and is not
> hot-reloaded. After changing any configuration in this directory, quit and
> restart OpenCode for the change to take effect.