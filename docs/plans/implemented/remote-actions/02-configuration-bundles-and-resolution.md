# Plan 2: Configuration Bundles and Remote Resolution

## Objective

Replace independent agent and skill paths with a single, versioned
configuration bundle that can be loaded from the local trusted checkout or an
organization-approved remote GitHub repository. Every run must resolve,
validate, and record the precise bundle content it uses.

## Scope

This plan defines the bundle contract, resolution algorithm, and source trust
policy. Prompt templates and model/tool policy are addressed by Plans 3 and 4.

Expected code changes:

- new dependency-free module such as `scripts/agentic_configuration.py`
- updates to `scripts/run_agentic_feedback.py`
- updates to all three workflow files
- fixture bundles and unit tests under `tests/`
- bundle schema and sample local bundle under `.opencode/`

## Bundle Layout

Adopt a single directory per profile. A sample layout:

```text
.opencode/configuration/
  documentation-review/
    bundle.json
    agent.md
    skills/
      documentation/SKILL.md
    prompts/
      review.md
    hashes.json
```

The resolver receives a bundle root and profile name; it must not accept paths
for individual files from untrusted or runtime inputs.

## Manifest Contract

Use JSON for the initial manifest because the Python standard library can parse
it without introducing a YAML parser dependency. `bundle.json` must contain:

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

Define and enforce the following rules:

- `schema_version` must be a supported integer.
- `profile_name` must match a conservative identifier pattern such as
  `[a-z0-9][a-z0-9-]{0,62}`.
- Every file field is a normalized, repository-relative POSIX path with no
  leading slash, `..`, empty segment, or symlink traversal.
- `allowed_workflows` must include the current feedback kind.
- The output contract must be known to the runner and cannot be weakened by a
  profile.
- The bundle must contain exactly one agent entry and one or more skills only
  when the agent explicitly permits them.

Use `hashes.json` (or a `content_hashes` manifest field) mapping each declared
content path to a lower-case SHA-256 digest. Validate all declared files before
OpenCode executes.

## Source Trust Model

### Local source

The default source remains `local`. Resolve it only from the checked-out
trusted revision:

- PR reviews use the API-resolved base SHA already checked out by
  `pull_request_target`.
- issue workflows use the workflow's trusted checkout revision.
- no event payload, issue text, PR text, or dispatch free text can choose a
  local bundle path.

### Remote source

Remote sources must be named aliases, not URLs. Store their repository identity
and allowed root paths in a built-in policy file or workflow-owned allowlist.
For production:

- require an exact 40-character Git commit SHA;
- reject branches, tags, PR refs, GitHub Gists, raw URLs, and relative
  cross-repository references;
- resolve through GitHub's authenticated Contents/Git APIs or a sparse,
  detached checkout;
- fetch the manifest and its declared content from that SHA only;
- constrain total bundle file count and byte size before parsing content.

A development-only mode may resolve an allowlisted tag or branch to a SHA, but
must log that SHA and must not be enabled by untrusted event data.

## Resolver Algorithm

1. Receive normalized workflow context: feedback kind, source alias, requested
   SHA, profile, and safe typed overrides.
2. Validate source alias, profile syntax, current workflow compatibility, and
   SHA format before fetching any remote data.
3. Materialize the bundle in a fresh temporary directory under `RUNNER_TEMP`.
   Do not copy it into the working tree or source it from shell.
4. Parse `bundle.json` and validate schema fields, including workflow
   compatibility.
5. Validate every declared path against the bundle root with resolved-path
   containment checks; reject symlinks and unexpected files where appropriate.
6. Enforce per-file and total byte limits; read as UTF-8 with a deterministic
   newline policy.
7. Verify SHA-256 hashes and validate agent/skill front matter: required names,
   unique names, supported mode, and permitted capability declarations.
8. Return immutable resolved objects containing file contents, names, model
   profile, output contract, manifest hash, source repository, and resolved
   SHA. Do not return arbitrary paths for downstream use.
9. Write a redacted resolved-configuration report for the job summary and
   artifact.

A failure at any step must stop execution. Do not silently select a local
profile, a default branch, or a previous remote revision.

## Workflow Integration

Add a `Resolve agentic configuration` step before OpenCode installation or
execution. It should invoke the resolver with workflow-derived values and write
an internal JSON result to `RUNNER_TEMP`. Downstream commands read only this
result.

Update `run_agentic_feedback.py` to consume resolved configuration data rather
than independently opening paths passed by the workflow.

The issue-implementation workflow currently reads an agent name with `sed`.
Replace it with the same resolver result used by the feedback workflows to
avoid inconsistent validation.

## Tests

Use local fixture directories and mocked GitHub responses; tests must not make
network requests.

- Valid local and remote bundle resolution.
- Missing, malformed, or unsupported manifest version.
- Invalid profile, workflow mismatch, duplicate names, absent front matter, and
  unsupported agent capabilities.
- Path traversal, absolute paths, backslash ambiguity, symlinks, unexpected
  binary data, oversized files, and total-size exhaustion.
- Missing/mismatched hash values and unlisted declared content.
- Mutable ref rejection, invalid SHAs, unknown source aliases, and remote
  repository allowlist rejection.
- Redacted provenance output containing source/revision/hash but no secrets.

## Acceptance Criteria

- A local or allowlisted remote bundle resolves to a fully validated immutable
  configuration object.
- Production remote bundles are pinned to a full commit SHA.
- All agents and skills are verified before OpenCode is invoked.
- Remote configuration failures fail closed.
