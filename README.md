# generic-agentic-workflows

Reusable GitHub Actions workflows for agentic pull-request and issue feedback using OpenCode.

## Workflows

* `.github/workflows/opencode-review.yml` reviews pull-request diffs for documentation impact.
* `.github/workflows/opencode-issue-feedback.yml` checks every open issue when an issue changes and on a daily schedule. It does not comment again when it finds its existing feedback marker on that issue.
* `.github/workflows/opencode-issue-implementation.yml` runs on weekdays and can be dispatched manually. It selects one open issue that has either an `[[AI REVIEW REQUESTED]]` (or `[[AI IMPLEMENTATION REQUESTED]]`) comment or an `ai-review-requested` label, unless the manual dispatch supplies an open issue number. It skips issues that already have an implementation-status comment from `github-actions[bot]`, asks OpenCode to create an initial implementation PR, and posts the PR link—or a failure status—to the issue.

Both workflows invoke `scripts/run_agentic_feedback.py`. The script validates the selected repository customisations, runs OpenCode, and writes one marked GitHub comment per feedback type.

The implementation workflow uses `.opencode/agents/issue-implementation.md` instead. Its agent has narrowly scoped instructions: it must work only on its dedicated branch, create a focused PR, and must not edit workflows, automation, dependencies, or its own instructions. Review the generated PR normally before merging it.

### Contributor handles

The workflows pass the authoritative GitHub login from the event or issue API to
the feedback runner: `${{ github.event.pull_request.user.login }}` for pull
requests and `.user.login` for each issue. The runner tells OpenCode to use
that exact `@handle`. Skills should follow this instruction rather than using
an unresolved placeholder such as `{author}` or deriving a name from an issue
number.

### Customise the agent and skill

Each workflow sets the following job environment variables. Change their paths to choose custom guidance stored in the repository's trusted default branch:

```yaml
CUSTOM_AGENT_FILE: .opencode/agents/docs-impact-eval.md
CUSTOM_SKILL_FILE: .opencode/skills/example-skill/SKILL.md
FEEDBACK_KIND: pr-documentation-review # or issue-feedback
```

Agent and skill files must start with YAML front matter containing a `name`. The agent name is passed to OpenCode; the skill file is validated so workflow configuration cannot silently drift from the repository customisation.

## Secrets and least-privilege tokens

The OpenCode provider credential is configured as the repository Actions secret `OPENROUTER_API_KEY`. Generate an OpenRouter key restricted to only the required provider/model where the provider supports it, with a small credit limit and an expiry or rotation policy. Store it under **Settings → Secrets and variables → Actions → New repository secret**. Do not place API keys in workflow YAML, issue text, pull-request content, or agent/skill files.

For GitHub comments, these workflows use the ephemeral `${{ github.token }}` automatically supplied by Actions. No personal access token is needed for a workflow in this repository:

| Workflow | Required job permissions |
| --- | --- |
| Pull-request review | `contents: read`, `pull-requests: write` |
| Issue feedback | `contents: read`, `issues: write` |
| Issue implementation | `contents: write`, `issues: write`, `pull-requests: write` |

If an external integration cannot use `github.token`, create a **fine-grained personal access token** for only this repository, set the shortest practical expiration, and grant only:

* **Pull requests: Read and write** for PR comments, or
* **Issues: Read and write** for issue comments.

Add it as an Actions secret such as `AGENTIC_FEEDBACK_TOKEN`, pass it to `GITHUB_TOKEN` only in the comment-producing step, and remove it when no longer required. Do not grant repository administration, workflow, or broad organization permissions.

> **Security note:** The PR workflow uses `pull_request_target` so it can comment with write permission, but it checks out the trusted base revision and only reads the untrusted PR diff. Do not change it to check out or execute pull-request code.
