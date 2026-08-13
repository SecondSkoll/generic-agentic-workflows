# Plan: release project-management review workflow

## Objective

Add a reusable OpenCode workflow, `opencode-release-project-review.yml`, that
examines a GitHub release and creates **one issue in the reviewed repository**
only when it finds material release-planning or project-management gaps. The
agent must assess release logic and operational readiness, not source-code
quality.

The supplied `release-project-review` profile is the default for both:

- direct runs in this repository; and
- reusable calls that select `configuration_source: default`.

As with the existing workflows, callers may instead select a hash-verified
`local` or approved `central` configuration profile.

## Scope and decisions

### In scope

- Review a published GitHub release identified by a release ID or tag.
- Check out the resolved release commit for read-only contextual inspection.
- Collect bounded release metadata, release notes, assets, selected repository
  documentation, and release-process artefacts for the prompt.
- Identify only logical/project-management problems: unclear scope or owners,
  missing acceptance criteria, dependencies, rollout or rollback plans,
  operational/support readiness, release-note gaps, risk decisions, and
  missing follow-up ownership.
- Produce a validated decision to either create a single actionable issue or
  record that no issue is warranted.
- Support a same-repository release by default and an explicitly authorized
  release in another repository.

### Out of scope

- Finding, reporting, or fixing implementation defects, code style issues,
  vulnerabilities, dependency updates, or test failures unless their release
  consequence is directly expressible as a project-management gap.
- Executing repository code, modifying the checked-out release, opening pull
  requests, or allowing the model to choose an API endpoint, repository,
  labels, or credentials.
- Automatically monitoring arbitrary external repositories. External review is
  invoked through a caller-controlled reusable/manual run with constrained
  credentials.

### Trigger and target model

The first implementation should be manual/reusable rather than automatically
running on every release. It is safer to validate the new issue-creation path
before enabling event-driven publication.

`workflow_dispatch` and `workflow_call` expose these typed inputs:

| Input | Default | Rules |
| --- | --- | --- |
| `target_repository` | `${{ github.repository }}` | Strict `owner/repo` grammar; no URLs, owner/repo ref syntax, paths, or expressions accepted after resolution. |
| `release_id` | empty | Positive numeric GitHub release ID. Exactly one of this and `release_tag` is required. |
| `release_tag` | empty | Conservative tag grammar; resolved through the GitHub API, never used as a Git ref directly. |
| `configuration_source` / `configuration_ref` / `configuration_profile` | `default` / required for remote / `release-project-review` | Follow the existing verified-bundle model. |
| `focus` | empty | Optional allowlisted release-management focus only; it must not accept arbitrary prompt text. |
| `dry_run` / `validate_only` | `false` | Retain the existing mutually exclusive no-publication modes. |

The workflow YAML will carry both checkout options, with the external option
commented out by default as requested. The active same-repository checkout
remains the normal path:

```yaml
# Default: review a release in the repository running this workflow.
- uses: actions/checkout@<pinned-sha>
  with:
    ref: ${{ steps.release.outputs.target_commit_sha }}
    persist-credentials: false

# External-release option: enable only for an approved cross-repository call
# after the target-token checks below have succeeded.
# - uses: actions/checkout@<pinned-sha>
#   with:
#     repository: ${{ steps.resolve.outputs.target_repository }}
#     ref: ${{ steps.release.outputs.target_commit_sha }}
#     token: ${{ secrets.release_target_token }}
#     persist-credentials: false
```

At implementation time the active checkout should become conditional rather
than simply uncommenting a second step, so only one checkout runs. The
commented example documents the deliberate difference and prevents an
unreviewed external-repository checkout from becoming the default.

For cross-repository runs, require an explicitly forwarded, separately scoped
GitHub App installation token (preferred) or fine-grained token in
`release_target_token`. It needs only `contents: read` and `issues: write` on
the target repository. Verify the token can read the target before checkout;
publish with that same target-scoped token. The caller's `GITHUB_TOKEN` must
not be assumed to have access outside its repository.

## Workflow sequence

1. Check out the trusted workflow/caller base revision with credentials
   disabled, then normalize all inputs before contacting a model.
2. Resolve the requested `release_id` or `release_tag` with GitHub REST API.
   Reject drafts, missing releases, ambiguous selectors, malformed target
   repositories, or an unresolved target commit. Record canonical repository,
   release ID, tag, publication time, and target commit SHA.
3. Select credentials and enforce the destination boundary. Same-repository
   runs use the job token; remote runs require `release_target_token` and must
   prove read/write access to the canonical target repository.
4. Check out exactly the resolved immutable target commit, read-only. Do not
   check out a branch, tag, release asset, PR head, or caller-controlled ref.
5. Run the existing invocation, configuration, policy, and provenance stages,
   extended for the new workflow identity and release target fields. Preserve
   the current failure-provenance behavior and artifacts.
6. Gather a bounded, allowlisted context: release metadata/body/assets,
   repository release notes and operational documents at the checked-out SHA,
   plus limited linked milestone/issue/PR metadata when configured. Treat every
   fetched value as untrusted input; enforce byte/item limits and do not send
   secrets, source archives, or arbitrary repository files to the provider.
7. Materialize the verified OpenCode profile, compose a delimited prompt, run
   OpenCode, and validate its response against the release issue contract.
8. In publish mode only, create an issue in the canonical target repository
   when the decision is `CREATE_ISSUE`; otherwise write a no-findings summary.
   `dry_run` validates the decision but creates nothing; `validate_only` does
   not fetch the release, check out a target, invoke OpenCode, or publish.
9. Before creation, search for a deterministic hidden idempotency marker. Use
   a key derived from canonical target repository, release ID, target commit
   SHA, configuration digest, and workflow version. Existing matching issues
   are reported and not duplicated.

## Agent, profile, contract, and policy

Add `.opencode/configuration/release-project-review/` containing:

- `bundle.json` restricted to `release-project-review`;
- a low-temperature, read-only `release-project-review` agent with no edit,
  shell, network, web, or delegation permission;
- `skills/release-management/SKILL.md` defining a concise project-manager
  rubric; and
- `prompts/release-project-review.md`, which explicitly rejects code-level
  review findings and asks for evidence, impact, owner/action, and priority
  for each release-readiness gap.

Add `release-project-issue-v1`, a structured output contract that accepts only
one of:

```json
{"decision":"NO_ISSUE","summary":"..."}
```

or:

```json
{"decision":"CREATE_ISSUE","title":"...","body":"...","labels":["release-readiness"]}
```

The parser must reject empty evidence, out-of-scope source-code findings,
unknown labels, unbounded titles/bodies, malformed JSON, and any model attempt
to supply a destination repository or endpoint. The runner owns the title/body
limits, allowlisted labels, destination, and marker.

Add an immutable `release-project-review` policy and compatible reviewed model
profile in `scripts/agentic_policy.py`. It permits bounded release/repository
read context and a workflow-mediated `issue-create-only` publication action;
it denies shell, editing, delegation, direct GitHub writes by the agent, and
all capability escalation by local/central bundles. Model output is never a
credential or authority source.

## Planned implementation areas

| Area | Planned changes |
| --- | --- |
| `.github/workflows/opencode-release-project-review.yml` | New manual/reusable workflow, conditional same/external checkout alternatives, minimal permissions, resolved-release collection, validated issue creation, provenance, and artifacts. |
| `scripts/resolve_invocation.py` | Allow the new workflow; add normalized target repository and mutually exclusive release ID/tag fields; add strict grammar, output/summary serialization, and workflow-specific compatibility rules. |
| `scripts/run_agentic_release_project_review.py` | New dedicated collector/runner/publication path. Do not overload PR/issue feedback behavior. |
| `scripts/agentic_prompts.py` and `.opencode/policy/output-contracts.json` | Add the release issue contract, typed release runtime context, delimiters, and parser/validation rules. |
| `scripts/agentic_policy.py` | Add the new model-profile/workflow mapping, bounded context capabilities, and `issue-create-only` policy semantics. |
| `scripts/agentic_provenance.py` | Record canonical target repository plus release ID, tag, and commit SHA without release body, prompt, raw model text, or tokens. |
| `.opencode/configuration/release-project-review/` | Add the supplied default profile and regenerate `hashes.json`. |
| `verify-reusable-workflows.yml` | Include the workflow, profile, examples, resolver smoke call, contract, and policy mapping in verification. |
| `docs/examples/configuration-sources/*` | Add default/local/central release-review wrappers. The default wrapper uses `release-project-review`; local wrappers use a local profile; all begin with `validate_only: true`. |
| `README.md`, `docs/configuration*.md`, and operations guide | Document target selection, restricted cross-repository credentials, rollout, idempotency, the commented checkout alternative, and release-project-management-only scope. |

## Test plan and acceptance criteria

Extend the existing unit/integration suites and add a dedicated runner test
module. The completed change must demonstrate all of the following:

1. Resolver tests accept canonical same-repository targets and valid release
   ID/tag selectors; reject both/neither selector, malformed repositories,
   tags/URLs/ref injection, invalid focus, and `dry_run` plus `validate_only`.
2. Configuration/hash tests resolve the supplied profile for the new workflow
   and reject a profile/workflow mismatch or permissions that broaden the
   read-only agent boundary.
3. Prompt/contract tests delimit hostile release text, retain typed release
   metadata, accept valid `NO_ISSUE`/`CREATE_ISSUE` responses, and reject
   code-only findings and destination/endpoint fields.
4. Policy tests reject shell, delegation, arbitrary network/write escalation,
   and external publication without the explicit target-scoped authorization.
5. Runner tests mock GitHub/OpenCode boundaries and verify canonical release
   lookup, immutable-SHA checkout input, context bounds, no issue for
   `NO_ISSUE`, exactly one issue for a valid finding, label allowlisting,
   idempotent skip, and no writes in dry-run/validate-only mode.
6. Workflow YAML tests verify pinned actions, both documented checkout paths,
   minimal same-repository permissions, required remote secret declaration,
   artifact paths, and default/local/central reusable wrappers.
7. Documentation examples are checked for a full SHA pin and begin with
   `validate_only`; operational documentation explains how to promote to
   `dry_run` then publish.

The implementation is complete only when an ordinary release in this
repository uses the supplied profile without caller overrides, a reusable
consumer using `configuration_source: default` receives that same profile, and
a remote-target run cannot read or create an issue outside the explicitly
authorized target repository.