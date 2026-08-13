# Plan 7: Remote GitHub File References for Agents, Prompts, and Skills

## Objective

Allow approved configuration profiles to source their agent, prompt, and skill
content from local files or from other GitHub repositories, including private
repositories when an appropriate read token is supplied. Remote content must be
resolved reproducibly, validated against the effective policy, assessed for
OpenCode compatibility, adapted when safe, and reflected in provenance and final
publication notes.

This plan is intentionally sequenced as **Plan 7**. It must be implemented
after:

1. reusable workflow interfaces and typed invocation validation from Plan 1;
2. configuration bundle resolution from Plan 2;
3. prompt composition and output-contract validation from Plan 3;
4. model, tool, and policy governance from Plan 4;
5. provenance, operations, and rollout controls from Plan 5;
6. pull-request review additional-context controls from Plan 6.

Remote file references should extend those systems. They must not reintroduce
the former pattern where workflow callers pass arbitrary prompt, agent, skill,
path, URL, model, or tool values directly to a workflow.

## Scope

This plan covers remote references for configuration content only:

- agent files;
- prompt template files;
- skill files;
- skill directories containing `SKILL.md` and supporting reference files.

Affected areas:

- `scripts/agentic_configuration.py`, or the configuration resolver introduced
	by Plan 2;
- the policy module introduced by Plan 4;
- the prompt-rendering and output-contract modules introduced by Plan 3;
- `scripts/run_agentic_feedback.py` for publication-note handling;
- all three reusable workflow files for optional private-content token wiring;
- provenance and idempotency code from Plan 5;
- tests and fixtures under `tests/`;
- `README.md`, `docs/examples/`, and sample bundles under `.opencode/`.

Out of scope:

- accepting arbitrary remote URLs as workflow-call inputs;
- loading files from non-GitHub hosts, GitHub Gists, GitHub Releases, or issue
	attachments;
- executing remote content, scripts, package managers, generated commands, or
	workflow files;
- allowing remote files to change the final output contract or effective tool
	policy;
- using remote files as additional pull-request review context. Plan 6 governs
	repository context supplied to the model during review.

## Prerequisites and Integration Points

### Plan 1: Reusable workflow interface

Keep the Plan 1 typed interface. Do not add inputs such as `agent_url`,
`skill_url`, `prompt_url`, `raw_prompt`, or `remote_file_path`.

Add only an optional secret contract for private remote content, for example:

- `REMOTE_CONTENT_TOKEN`, optional, read-only token used only by the resolver to
	fetch allowlisted private GitHub repositories.

The caller still selects a `configuration_source`, `configuration_ref`, and
`configuration_profile`. The selected trusted bundle or trusted local overlay
may declare remote file references if the effective policy permits them.

### Plan 2: Configuration bundles

Remote file references are part of the resolved bundle content graph. The
resolver must return immutable resolved objects containing the final materialized
agent, skills, and prompt template. Downstream code must not open arbitrary
paths or URLs.

Plan 7 should introduce a bundle manifest version that can express both local
files and remote GitHub references while preserving compatibility with Plan 2
schema version 1 bundles.

### Plan 3: Prompt templates and output contracts

Remote prompt templates are still only profile templates. They pass through the
same template parser, variable allowlist, size limits, and workflow-owned prompt
composition order. A remote prompt cannot replace workflow-owned system
constraints or output suffixes.

Compatibility notes generated while adapting remote files must be appended by
workflow-owned publication code, not by allowing remote prompt text to control
the final comment format.

### Plan 4: Policy governance

The policy engine decides whether remote file references are allowed, which
GitHub repositories may be used, whether private repositories are allowed, and
which compatibility transformations are permitted. Merge all such settings
restrictively.

### Plan 5: Provenance and operations

Record every remote content source, resolved commit SHA, input hash, converted
hash, compatibility action, and denial in the redacted provenance artifact. Do
not record full file contents, tokens, raw prompts, complete diffs, or model
responses.

Include the remote content digest in the configuration digest used for
idempotency markers. A change to remote content at a pinned commit is impossible
without changing the hash; a change to any referenced SHA or content hash should
produce a new deterministic configuration digest.

### Plan 6: Additional context

Keep remote configuration content separate from on-demand PR review context.
Remote skills and prompts may tell the agent how to review, but they cannot
grant additional repository context access. Plan 6 context tiers and limits
remain controlled by the effective policy and runner.

## Design Principles

### GitHub-only source support

Accept only canonical GitHub repository content links:

- raw file links under `https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>`;
- web file links under `https://github.com/<owner>/<repo>/blob/<ref>/<path>`;
- web directory links under `https://github.com/<owner>/<repo>/tree/<ref>/<path>` for skill directories only.

Reject all other schemes and hosts, including shortened URLs, Gists,
repository archive downloads, release assets, `git+ssh`, `file:`, and arbitrary
HTTP redirects.

### Trusted declaration, not untrusted invocation

Remote links may appear only in trusted configuration data loaded by the Plan 2
resolver:

- the checked-out trusted local bundle;
- an allowlisted remote bundle pinned by `configuration_ref`;
- a trusted consumer local overlay if Plan 4 policy explicitly allows overlays
	to narrow or substitute content references.

Issue text, PR text, branch names, workflow dispatch free text, and
model-generated output must never select remote file URLs.

### Immutable resolution

Production runs should require immutable commit SHAs for every remote content
reference. The URL parser may understand branch-like examples for developer
ergonomics, but production validation must fail closed unless the remote ref is
resolved to and pinned as a full 40-character commit SHA by trusted
configuration.

Recommended behavior:

- accept URL syntax containing a full 40-character SHA directly;
- optionally allow a trusted manifest field such as `resolved_sha` when the URL
	uses a human-readable ref, and verify that the ref resolves to that exact SHA;
- disallow mutable refs entirely unless an organization policy explicitly
	enables a development-only resolver mode;
- always record the resolved SHA and content hashes in provenance.

### Hash everything before and after adaptation

For each remote file or directory, verify the declared source hash before any
compatibility transformation. If content is adapted for OpenCode, compute and
record a separate materialized hash.

The resolver should fail if a declared hash is absent, malformed, or mismatched,
except in an explicitly documented validation-assist mode that reports the
required hashes without invoking OpenCode or publishing feedback.

### Adapt safely, fail clearly

Compatibility adaptation should be deterministic and conservative. If the
runner can safely convert a remote customization into the local OpenCode layout,
it should do so in a temporary materialized bundle and record a note. If a file
requires semantic decisions, unsupported tools, executable behavior, or unsafe
path access, the resolver should fail with a redacted actionable error.

### Private access stays outside the model

Private repository tokens are used only by the resolver. They must never be
written to disk, passed to OpenCode, included in prompts, logged, stored in
provenance, or exposed to remote content.

## Bundle Manifest Extension

Introduce bundle schema version 2. Version 1 remains valid and continues to use
local bundle-relative paths exactly as defined in Plan 2.

### Example schema version 2 manifest

```json
{
  "schema_version": 2,
  "profile_name": "documentation-review",
  "allowed_workflows": ["pr-documentation-review"],
  "model_profile": "review-readonly",
  "output_contract": "pr-review-json-v1",
  "content": {
    "agent": {
      "kind": "agent",
      "source": "github_file",
      "url": "https://github.com/canonical/copilot-collections/blob/0123456789abcdef0123456789abcdef01234567/agents/documentation-review.agent.md",
      "sha256": "<source-file-sha256>",
      "compatibility": "auto"
    },
    "prompt_template": {
      "kind": "prompt_template",
      "source": "local_file",
      "path": "prompts/review.md",
      "sha256": "<local-file-sha256>"
    },
    "skills": [
      {
        "kind": "skill_directory",
        "source": "github_directory",
        "url": "https://github.com/canonical/copilot-collections/tree/0123456789abcdef0123456789abcdef01234567/skills/documentation-review",
        "tree_sha256": "<canonical-tree-sha256>",
        "compatibility": "auto"
      }
    ]
  },
  "limits": {"max_comments": 10}
}
```

### Content reference rules

Each `content` entry must specify:

- `kind`: one of `agent`, `prompt_template`, `skill_file`, or
	`skill_directory`;
- `source`: one of `local_file`, `github_file`, or `github_directory`;
- `path` for local files, or `url` for GitHub references;
- `sha256` for file content or `tree_sha256` for directory content;
- optional `compatibility`, defaulting to `strict`.

Rules:

- `agent` must resolve to exactly one materialized agent file.
- `prompt_template` must resolve to exactly one materialized prompt template.
- `skill_file` must resolve to one file whose basename may be `SKILL.md` or a
	documented skill entry file.
- `skill_directory` must resolve to a directory containing exactly one root
	`SKILL.md`; supporting files are allowed only under that directory.
- Remote directory references are valid only for `skill_directory` entries.
- Local paths retain all Plan 2 path containment, no-symlink, byte-limit, and
	hash-validation rules.
- Remote references must be allowlisted by effective policy before any fetch.

### Compatibility modes

Use a small enum rather than arbitrary converter names:

| Mode | Behavior |
| --- | --- |
| `strict` | Validate content is already OpenCode-compatible; fail otherwise. |
| `auto` | Apply only workflow-owned deterministic compatibility adapters permitted by policy. |
| `report_only` | Report incompatibilities during `validate_only`; fail before model execution. |

The effective policy may reduce `auto` to `strict`, but a bundle or invocation
must not upgrade `strict` to `auto` if policy forbids adaptation.

## Effective Policy Additions

Add fields to the Plan 4 policy schema and merge them restrictively:

```json
{
  "remote_content": {
    "allow": true,
    "allow_private_repositories": false,
    "allowed_repositories": ["canonical/copilot-collections"],
    "allowed_owners": [],
    "require_pinned_sha": true,
    "allow_raw_githubusercontent": true,
    "allow_github_blob_links": true,
    "allow_github_tree_links_for_skills": true,
    "allow_compatibility_auto_fix": true,
    "max_remote_files": 20,
    "max_remote_file_bytes": 204800,
    "max_remote_total_bytes": 1048576,
    "max_skill_directory_files": 50,
    "denied_path_patterns": [".env", "*.pem", "*.key", "id_rsa", "secrets.*"],
    "denied_directory_patterns": [".git", "node_modules", ".venv", "dist", "build"]
  }
}
```

Merge behavior:

- `allow` and `allow_private_repositories`: logical AND;
- repository and owner allowlists: intersection;
- `require_pinned_sha`: logical OR, because any layer may require stricter
	immutability;
- compatibility permissions: logical AND;
- byte, file, and directory limits: minimum;
- denied patterns: union.

Typed invocation inputs may lower limits for testing, but they must not enable
remote content, private repositories, new hosts, new repositories, or
compatibility adaptation.

## URL Normalization and Validation

Create a dependency-free URL parser that returns a canonical content reference:

```text
GitHubContentRef(
  owner,
  repository,
  ref,
  path,
  ref_type=file|directory,
  original_url,
  canonical_web_url,
  canonical_raw_url
)
```

Validation steps:

1. Parse the URL with the Python standard library.
2. Require `https`.
3. Require host `github.com` or `raw.githubusercontent.com`.
4. For `github.com`, accept only `/owner/repo/blob/ref/path` and
	`/owner/repo/tree/ref/path` shapes.
5. For `raw.githubusercontent.com`, accept only `/owner/repo/ref/path` and
	treat the reference as a file.
6. Reject empty owner, repository, ref, or path segments.
7. Normalize path separators to POSIX `/`.
8. Reject absolute paths, `..`, empty segments, encoded traversal, control
	characters, and backslash ambiguity.
9. Reject directory URLs unless `kind` is `skill_directory`.
10. Validate owner/repository against the effective allowlist.
11. Validate ref immutability according to policy.
12. Produce canonical identifiers used for fetching, hashing, provenance, and
	error messages.

Do not fetch remote content by shelling out to `curl` with user-provided URLs.
Use GitHub APIs or carefully constructed canonical raw URLs only after
validation. Prefer GitHub API access for private repositories and tree walking.

## Remote Fetching Algorithm

Implement a resolver module such as `scripts/remote_content.py`, used only by
the configuration resolver.

### Inputs

- canonical content references from the manifest;
- effective remote-content policy;
- optional `REMOTE_CONTENT_TOKEN` supplied by the workflow;
- current mode: `validate_only`, `dry_run`, or `publish`;
- workflow-owned runtime limits and temp directory.

### Steps for a remote file

1. Validate URL and policy.
2. Resolve the repository ref to a commit SHA.
	- If a full SHA was supplied, verify that the object exists.
	- If a mutable ref is supplied, allow it only when trusted configuration and
		policy explicitly permit resolution, then verify it matches the manifest's
		declared `resolved_sha`.
3. Fetch the blob metadata and reject directories, symlinks, submodules, large
	files, binary content, and denied path patterns.
4. Enforce per-file and total remote byte budgets before storing content.
5. Decode as UTF-8 with deterministic newline handling.
6. Verify declared `sha256` against the exact fetched source text bytes.
7. Run compatibility assessment and optional adaptation.
8. Write the materialized file under a fresh `RUNNER_TEMP` directory with safe
	permissions and a generated internal path.
9. Return immutable metadata and materialized content to the bundle resolver.

### Steps for a skill directory

1. Validate URL and policy, including that the entry `kind` is
	`skill_directory`.
2. Resolve and verify the commit SHA.
3. List the tree under the requested directory using GitHub's Git Trees API or
	a sparse, detached, no-checkout Git operation.
4. Reject symlinks, submodules, nested `.git` directories, denied paths,
	generated/vendor directories, binary files, and oversized files.
5. Require a root `SKILL.md` directly under the referenced directory.
6. Enforce maximum file count, per-file bytes, total bytes, and path depth.
7. Compute a canonical `tree_sha256` from sorted tuples of relative path,
	file mode, file SHA, and normalized file bytes.
8. Verify the declared `tree_sha256`.
9. Run compatibility assessment and optional adaptation on `SKILL.md` and
	supporting references.
10. Materialize the directory under `RUNNER_TEMP` with generated internal names.

### Token handling

- If no token is provided, public repositories may be fetched anonymously or
	with the default `GITHUB_TOKEN` only when it has access and policy permits.
- If a private repository is referenced, require `REMOTE_CONTENT_TOKEN` or a
	workflow-owned token with `contents:read` access to that repository.
- Detect authorization failures and report a redacted error that names the
	owner/repository and required permission, but never the token value.
- Do not pass the token to OpenCode or write it into materialized files.

## OpenCode Compatibility Assessment and Adaptation

Create a compatibility layer that runs after source hash verification and
before final materialization into the OpenCode execution layout.

### Compatibility result

Return a structured result for every content entry:

```json
{
  "entry_id": "skills[0]",
  "kind": "skill_directory",
  "source_url": "https://github.com/...",
  "source_sha256": "...",
  "materialized_sha256": "...",
  "status": "compatible|adapted|rejected",
  "actions": [
    {
      "code": "frontmatter-normalized",
      "severity": "notice",
      "message": "Converted supported front matter keys to OpenCode format."
    }
  ]
}
```

### Assessment checks

For all remote content:

- validate UTF-8 text and newline policy;
- reject executable binary content;
- reject unsupported include mechanisms, remote includes, shell snippets that
	claim to be required setup, and path references outside the materialized
	entry;
- reject secret-shaped literal values where practical;
- enforce maximum rendered prompt, agent, skill, and support-file sizes.

For agents:

- validate front matter and name fields required by the OpenCode agent loader;
- treat requested tools, delegation, networking, shell, and filesystem access
	as requests only, then intersect them with effective policy from Plan 4;
- reject agents that require unsupported or forbidden capabilities.

For prompt templates:

- validate the Plan 3 template token syntax;
- reject unknown variables and dynamic include/evaluation mechanisms;
- ensure the remote template cannot remove workflow-owned system constraints or
	output suffixes.

For skills:

- require one root skill entry file;
- validate front matter and documented skill structure;
- rewrite only internal relative links that remain inside the materialized skill
	directory;
- reject remote links that would fetch additional content outside the declared
	directory unless a future plan defines explicit recursive reference handling.

### Safe adaptations

Allowed deterministic adaptations may include:

- normalizing line endings;
- adding or normalizing supported front matter keys;
- removing unsupported display-only metadata that OpenCode ignores;
- converting accepted VS Code/Copilot customization metadata into the closest
	OpenCode metadata when the mapping is exact and policy allows it;
- rewriting internal skill-directory links to the materialized relative paths;
- dropping unsupported optional sections only when they are explicitly marked as
	optional and the compatibility note records the drop.

Never adapt by:

- granting a forbidden tool or capability;
- evaluating code or shell commands;
- fetching additional undeclared URLs;
- changing the semantic task instructions beyond mechanical format conversion;
- silently removing safety-relevant instructions;
- suppressing output-contract, policy, or validation errors.

If adaptation occurs, the final materialized file must be the only content
visible to OpenCode. The source file remains immutable for hashing and audit.

## Workflow Integration

### Workflow secrets

Extend each reusable workflow with an optional secret:

- `REMOTE_CONTENT_TOKEN`: token for allowlisted private GitHub content.

Documentation should recommend a fine-grained token or GitHub App token with
the minimum `contents:read` access to the specific source repositories.

Direct-trigger workflows may omit the token and should still support local and
public remote references when policy allows.

### Configuration resolution step

Update the Plan 2 `Resolve agentic configuration` step to:

1. read the optional token from the environment;
2. load the effective policy;
3. resolve the selected bundle;
4. resolve all local and remote content references;
5. validate hashes and compatibility;
6. materialize a complete OpenCode-ready configuration under `RUNNER_TEMP`;
7. write a redacted resolved-configuration JSON record consumed by downstream
	steps.

Downstream OpenCode execution should receive only materialized internal paths or
resolved content produced by the resolver. It should not receive original remote
URLs.

### Publication notes

Add a workflow-owned publication-note mechanism. If any remote file was adapted,
or if optional compatibility issues were observed in `validate_only` or
`dry_run`, append a concise note to the final published artifact:

- PR review: append to the GitHub review summary body;
- issue feedback: append to the issue comment;
- issue implementation: append to the generated PR body and/or issue status
	comment.

Example note:

```text
Configuration note: 2 remote customization files were adapted for OpenCode
compatibility. See the run provenance artifact for redacted details.
```

Rules:

- Notes must be generated by trusted runner code, not the model.
- Notes must not include private file contents, prompts, full paths from private
	repositories when policy marks them sensitive, or token-derived information.
- Notes should include enough detail for maintainers to know that adaptation
	occurred and where to find the provenance artifact.
- If no adaptation or relevant warning occurred, do not add noise to comments.

## Provenance and Idempotency

Extend the Plan 5 provenance record with a `remote_content` section:

```json
{
  "remote_content": [
    {
      "kind": "skill_directory",
      "repository": "canonical/copilot-collections",
      "resolved_sha": "0123456789abcdef0123456789abcdef01234567",
      "path": "skills/documentation-review",
      "source_hash": "...",
      "materialized_hash": "...",
      "compatibility_status": "adapted",
      "compatibility_actions": ["frontmatter-normalized"]
    }
  ]
}
```

Redaction rules:

- public repository names and paths may be recorded;
- private repository names may be recorded only if policy allows, otherwise
	record an opaque source alias and hash;
- never record token values, Authorization headers, full source content, raw
	prompt text, or raw model output;
- include denial reasons without embedding sensitive content.

Update the configuration digest used in feedback idempotency to include:

- source repository identity or redacted alias;
- resolved commit SHA;
- content kind;
- source hash;
- materialized hash;
- compatibility action codes;
- effective remote-content policy digest.

## Failure Modes

Fail closed before OpenCode execution when:

- remote content is disabled by effective policy;
- a URL is not a supported GitHub raw, blob, or allowed skill-directory tree
	link;
- the repository or owner is not allowlisted;
- a private repository is referenced without a valid read token;
- a ref is mutable when policy requires a pinned SHA;
- fetched content does not match the declared hash;
- remote content exceeds file count, byte, or directory limits;
- a skill directory has no root `SKILL.md`;
- compatibility assessment rejects the content;
- adaptation is required but policy allows only strict compatibility;
- provenance cannot be written safely.

In `validate_only`, perform all resolution, fetching, hashing, compatibility,
policy, and provenance work, then stop before model/provider calls.

In `dry_run`, allow model execution if configuration resolution succeeds, but do
not publish comments, push branches, create PRs, or write issue state.

## Implementation Steps

1. **Add schema version 2 fixtures.**
	- Create local test bundles that use local files, GitHub file references, and
		GitHub skill-directory references.
	- Include fixtures for raw GitHub URLs and `github.com/blob` URLs.
2. **Implement URL canonicalization.**
	- Add parser and validation helpers with no network access.
	- Unit-test accepted and rejected URL shapes exhaustively.
3. **Extend policy schema.**
	- Add `remote_content` settings and restrictive merge behavior.
	- Add tests for attempted escalation from bundle, overlay, and invocation
		layers.
4. **Implement remote fetching abstraction.**
	- Add a small GitHub client that supports mocked file, blob, tree, and ref
		lookups.
	- Keep network calls outside unit tests by injecting a fake client.
5. **Add source hash and tree hash verification.**
	- Define canonical hash algorithms for files and directories.
	- Add a validation-assist command or error message that reports expected
		hashes in `validate_only` without running OpenCode.
6. **Implement compatibility assessment.**
	- Start with strict validation for agents, prompt templates, and skills.
	- Add a small, documented set of deterministic adapters for common
		OpenCode-compatible transformations.
7. **Materialize resolved content.**
	- Write converted content to an internal temp bundle.
	- Ensure no remote file can escape the materialized root.
	- Ensure downstream code uses only the resolver result.
8. **Wire workflows and secrets.**
	- Add optional `REMOTE_CONTENT_TOKEN` to reusable workflow definitions.
	- Pass it only to the resolver step.
	- Update direct-trigger behavior and examples.
9. **Extend provenance and idempotency.**
	- Record remote source metadata and compatibility action codes.
	- Include remote content in the configuration digest.
10. **Add publication-note handling.**
	- Thread compatibility notes from resolver to publication code.
	- Append concise trusted notes to PR reviews, issue comments, or generated PR
		bodies as appropriate.
11. **Document rollout and migration.**
	- Show how to pin GitHub URLs to commit SHAs.
	- Show how to grant private repository read access.
	- Show example local and remote bundle manifests.
	- Document strict versus auto compatibility behavior.

## Test Plan

### URL parser tests

- Accept raw GitHub file URLs.
- Accept `github.com/blob/<sha>/...` file URLs.
- Accept `github.com/tree/<sha>/...` URLs only for skill directories.
- Reject non-HTTPS, non-GitHub hosts, Gists, release URLs, archives, redirects,
	SSH URLs, local files, path traversal, encoded traversal, missing paths, and
	directory URLs for non-skill content.

### Policy tests

- Remote content disabled at any layer prevents fetching.
- Repository allowlists are intersected restrictively.
- Private repository access requires both policy permission and token presence.
- Bundles and overlays cannot increase limits or enable `auto` adaptation.
- Invocation inputs can lower byte and file limits only.

### Fetching and hashing tests

- Public file fetch succeeds with expected hash.
- Private file fetch uses a fake token provider and redacts token values.
- Mutable ref rejection works when `require_pinned_sha` is true.
- Mismatched source hash and tree hash fail closed.
- Oversized files, excessive directory entries, symlinks, submodules, binary
	content, and denied paths fail closed.

### Compatibility tests

- Already-compatible agent, prompt, and skill content passes in `strict` mode.
- Unsupported tools and dynamic include mechanisms are rejected.
- Prompt templates with unknown variables are rejected.
- Safe deterministic adaptations produce expected materialized hashes and notes.
- Required adaptation fails when effective policy disallows `auto`.

### Integration tests

- A schema version 1 local bundle still resolves unchanged.
- A schema version 2 bundle mixing local prompt and remote skill resolves to a
	complete materialized configuration.
- `validate_only` resolves remote content and writes provenance without calling
	the provider.
- `dry_run` may call the provider but never publishes adaptation notes.
- Normal PR review publication appends a trusted compatibility note only when
	adaptation occurred.
- Idempotency markers change when remote resolved SHA, source hash,
	materialized hash, or compatibility actions change.

## Documentation Deliverables

Update `README.md` and examples with:

- schema version 2 manifest examples;
- how to convert a GitHub web or raw file URL to a pinned commit SHA;
- skill-directory reference examples;
- private repository token requirements and least-privilege guidance;
- remote-content policy examples for organizations;
- compatibility modes and expected publication notes;
- troubleshooting for common resolver failures;
- guidance for adopting local or remote bundles.

## Rollout Strategy

1. Implement parser, policy, and local-only schema version 2 support behind a
	feature flag.
2. Enable public GitHub remote file references in `validate_only` for pilot
	profiles.
3. Enable public remote skill-directory references after tree hashing and
	compatibility notes are stable.
4. Enable `dry_run` for selected consumer repositories.
5. Enable normal publication for low-risk PR review profiles.
6. Add private repository support only after token redaction and permission
	failures are verified.
7. Expand to issue feedback.
8. Expand to issue implementation last, preserving all Plan 4 path and tool
	restrictions.

## Acceptance Criteria

- Trusted bundles can reference local content, remote GitHub files, and remote
	GitHub skill directories without adding arbitrary URL workflow inputs.
- Both `raw.githubusercontent.com` file URLs and `github.com/blob/...` file URLs
	are accepted after canonical validation.
- `github.com/tree/...` directory URLs are accepted only for skills.
- Private remote repositories work only with an explicitly supplied read token
	and effective policy permission.
- Every remote source is pinned, hash-verified, policy-checked, and included in
	the configuration digest.
- Remote files are assessed for OpenCode compatibility before OpenCode runs.
- Safe compatibility adaptations are deterministic, recorded, and surfaced as
	concise trusted notes in final comments or generated PR text.
- Unsafe, oversized, unpinned, unauthorized, or incompatible remote content
	fails closed before model execution or publication.
- Existing Plans 1–6 guarantees remain intact: typed reusable interfaces,
	resolved bundles, workflow-owned prompt composition, restrictive policy,
	redacted provenance, idempotency, and bounded additional-context behavior.