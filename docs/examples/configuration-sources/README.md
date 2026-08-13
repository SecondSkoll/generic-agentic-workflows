# Configuration-source workflow examples

Each directory is a complete, copy-ready pair of pull-request documentation
review and issue-feedback workflows. Copy the contents of one directory into
the root of a consumer repository; in particular, retain its `.github/` path.
The workflows call the reusable actions in this repository at commit
`61ed1bbd34a878f3ae270b1e4ff027cf786b730b`.

| Directory | `configuration_source` | Additional files to copy | Use case |
| --- | --- | --- | --- |
| [`default/`](default/) | `default` | None | Use the supplied `documentation-review` and `issue-feedback` profiles unchanged. |
| [`local/`](local/) | `local` | `.opencode/configuration/local-documentation-review/` and `.opencode/configuration/local-issue-feedback/` | Keep repository-specific reviewed instructions with the consumer. |
| [`central/`](central/) | `central` | None | Use the centrally approved profiles hosted by this repository. |

Each directory contains both `documentation-review.yml` and
`issue-feedback.yml`. Keep only the workflows needed by the consumer
repository.

All three examples start in `validate_only` mode so they can be enabled
safely. After verifying the resolution artifact, change `validate_only: true`
to `dry_run: true`, and then remove the flag to publish feedback. The
repository must define the `OPENROUTER_API_KEY` Actions secret before leaving
validation-only mode. Documentation review needs `pull-requests: write`;
issue feedback needs `issues: write`.

The remote workflow and remote configuration reference are pinned to complete
commit SHAs. Update both pins together only after reviewing a newer release.