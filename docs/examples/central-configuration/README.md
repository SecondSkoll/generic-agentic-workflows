# Example central configuration repository

This directory documents the structure of an organization-approved **remote**
configuration repository, consumed through the `central` source alias built into
`scripts/agentic_configuration.py`. The reusable workflows resolve a remote
bundle pinned to a full 40-character commit SHA; callers cannot pass a URL,
repository name, branch, tag, raw file URL, or PR ref.

> The `central` alias maps to repository
> `SecondSkoll/generic-agentic-workflows` and root
> `.opencode/configuration`. To adopt this for your organization, update
> `REMOTE_SOURCE_ALIASES` in `scripts/agentic_configuration.py` through a
> reviewed release and publish the new alias in the compatibility table. The
> alias allowlist in `REMOTE_SOURCE_ALIASES` is the single source of truth;
> `scripts/resolve_invocation.py` mirrors it for input validation.

## Remote transport and credentials

Remote bundle files are fetched through GitHub's authenticated Contents API
(`GET /repos/{owner}/{repo}/contents/{path}?ref={sha}`), which returns JSON
with base64-encoded file content. The bearer token authenticates access, so a
**private** central configuration repository is supported.

The reusable workflows accept an optional `central_config_token` secret. When
forwarded, it is preferred for remote bundle fetch (never logged or written to
artifacts); otherwise the workflow falls back to `github.token`. This lets a
consumer grant read access to a private central config repository without
elevating the default workflow token:

```yaml
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      central_config_token: ${{ secrets.CENTRAL_CONFIG_TOKEN }}
```

Fetches are bounded by retries (transient 5xx/network only; 4xx fails
immediately), per-response byte limits, and timeouts.

## Repository layout

```text
.opencode/configuration/
  documentation-review/
    bundle.json
    agent.md
    skills/documentation/SKILL.md
    prompts/review.md
    hashes.json
  issue-feedback/
    bundle.json
    agent.md
    skills/triage/SKILL.md
    prompts/feedback.md
    hashes.json
  default-implementation/
    bundle.json
    agent.md
    skills/implementation-guardrails/SKILL.md
    prompts/plan.md
    hashes.json
```

The supplied `default` bundles in this repository under
`.opencode/configuration/` are a complete, valid example of the same structure.

## `bundle.json` (schema v1)

```json
{
  "schema_version": 1,
  "profile_name": "documentation-review",
  "allowed_workflows": ["pr-documentation-review"],
  "agent_file": "agent.md",
  "skill_files": ["skills/documentation/SKILL.md"],
  "prompt_template": "prompts/review.md",
  "model_profile": "review-readonly",
  "output_contract": "pr-review-json-v1",
  "limits": {"max_comments": 10}
}
```

`hashes.json` maps every declared content path to its lowercase SHA-256
digest. The resolver verifies every hash before OpenCode runs and rejects
undeclared or missing content.

## Consumer wrapper (SHA-pinned remote)

```yaml
jobs:
  review:
    uses: SecondSkoll/generic-agentic-workflows/.github/workflows/opencode-documentation-review.yml@61ed1bbd34a878f3ae270b1e4ff027cf786b730b
    permissions:
      contents: read
      pull-requests: write
    with:
      configuration_source: central
      configuration_ref: 61ed1bbd34a878f3ae270b1e4ff027cf786b730b
      configuration_profile: documentation-review
      focus: documentation
      max_comments: 10
    secrets:
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Rules:

- `configuration_ref` must be exactly 40 lowercase hexadecimal characters (a
  full commit SHA). Branches, tags, PR refs, GitHub Gists, raw URLs, and
  shortened SHAs are rejected before any fetch.
- `configuration_source` must be `local` (the calling repository), `default`
  (profiles supplied by this repository), or an approved central alias.
  Arbitrary URLs and repository names are rejected.
- A remote resolution failure fails closed: the run does not fall back to a
  local profile, a default branch, or a previous remote revision.

## Release process for a central configuration repository

1. Require code review and protected branches/tags.
2. Update bundle content in a PR; regenerate `hashes.json` with
  `python3 scripts/update_hashes.py` (the resolver recomputes and verifies
  hashes).
3. Tag the merged commit with a protected release tag and record the 40-char
   SHA in the compatibility table.
4. Consumers pin `configuration_ref` to that SHA. To roll forward or back,
   consumers update the pinned SHA; the workflow never changes it for them.
