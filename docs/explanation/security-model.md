# Security model

These reusable workflows automate feedback and narrowly bounded implementation
using a model, but they do not treat the model, pull requests, issues, release
metadata, or caller inputs as trusted control planes. This document explains
the controls that establish the trust boundary and why they exist.

## Security principles

1. **Configuration is reviewed, immutable, and verified.** A caller selects a
   profile; it does not supply instructions, paths, or model settings.
2. **Untrusted repository data remains data.** Issue text, PR text, diffs,
   release notes, and checked-out context are explicitly delimited and cannot
   change policy or publication targets.
3. **Every layer can only narrow authority.** Workflow policy, organization
   policy, bundle policy, local overlay, and typed invocation inputs are
   intersected rather than overridden.
4. **The workflow, not the model, performs privileged actions.** Parsing,
   validation, destination selection, idempotency, and GitHub publication are
   deterministic runner responsibilities.
5. **Failures stop safely.** Invalid configuration or policy does not trigger
   a fallback to a branch, tag, local profile, or stale remote content.

## Control summary

| Control | Threat addressed | Why it is needed |
| --- | --- | --- |
| Immutable workflow and remote-bundle SHA pins | Mutable branch/tag substitution | A reviewed commit is stable; a branch or tag can later point to different workflow code or instructions. |
| Fixed source aliases | Caller-controlled URLs or repositories | An alias maps to a known repository and root, preventing configuration exfiltration or unreviewed code sources. |
| Hash-verified remote bundle files | Tampered, incomplete, or substituted remote agent content | Remote manifest content must match `hashes.json` byte-for-byte before use. Caller-local bundles may omit the file and are trusted through the protected checkout. |
| Path and symlink validation | Traversal and filesystem escape | Bundles cannot read a runner path outside their profile through `..`, absolute paths, or symlinks. |
| Trusted base checkout for PR review | Pull-request code execution/configuration takeover | `pull_request_target` uses the trusted base and does not check out or execute the untrusted PR head. |
| Typed invocation inputs | Prompt injection through wrapper inputs | Callers may choose bounded selectors, not raw prompt text, paths, model IDs, URLs, or mutable refs. |
| Restrictive policy merge | Lower layers broadening privilege | Every policy layer intersects capabilities and takes lower quotas; attempted escalation fails. |
| Least-privilege permissions and tokens | Excessive GitHub access | Each wrapper declares only the GitHub permissions it needs, and external releases use a target-scoped token. |
| Contract-validated model output | Model-directed API calls or malformed publication | The runner accepts only versioned output shapes before it posts comments, reviews, or issues. |
| Redacted provenance | Secret or sensitive-content leakage in diagnostics | Artifacts aid investigation without recording tokens, raw prompts, raw model output, full diffs, or release bodies. |

## Configuration trust boundary

A configuration bundle combines `bundle.json`, declared agent/skill/prompt
files, and, for remote sources or optional local hash locks, `hashes.json`. The resolver enforces the supported schema, known
workflow and output contract, a registered model-profile name, safe relative
paths, required agent/skill front matter, file-count and size bounds, and
SHA-256 integrity for every declared content file.

There are three source choices:

- **`local`** reads the profile from the consuming repository's trusted
  default-branch checkout. Pull-request content is not used to select it.
- **`default`** resolves a supplied profile from this repository at a full
  pinned SHA.
- **`central`** resolves an organization profile from the fixed allowlisted
  central repository at a separately pinned full SHA.

Remote resolution is bounded by request timeouts, retry count, response size,
bundle file count, and total content size. A missing file, bad hash, unknown
source, invalid SHA, or resolution failure stops the run with no fallback.

These checks prevent a caller or a PR author from redirecting the workflow to
an arbitrary repository, replacing a reviewed instruction with a different
file, or exploiting filesystem path traversal.

## Input and prompt-injection defenses

Model-facing workflows process adversarial text by design. An issue author,
PR author, release author, or contributor can write text that looks like an
instruction. The workflows therefore:

- delimit untrusted source material and identify it as data;
- use a reviewed prompt template selected through the bundle instead of
  caller-supplied prompt text;
- bound the amount and type of context collected;
- restrict PR inline comments to added diff lines, converting invalid
  locations to summary feedback; and
- require a versioned output contract before publication.

This does not claim that a model cannot be influenced by input. It ensures
that influence cannot alter the model's configured capabilities, select
credentials or destinations, execute arbitrary commands, or bypass the
workflow's deterministic validation and publication logic.

## Policy and capability boundaries

The effective policy has five ordered layers: built-in workflow policy,
organization policy/model profile, bundle policy, optional consumer overlay,
and typed invocation inputs. The merge is restrictive only:

- capability allowlists are intersected;
- quotas use the lower value;
- permissions combine with logical AND; and
- conflicting required values are rejected.

For example, documentation review is limited to a trusted checkout diff,
provider-only network access, review-comment publication, and no delegation.
A bundle can deny an already permitted operation or lower a quota, but it
cannot exchange that scope for a broader one. Review agent front matter that
requests edit permission is rejected.

The issue-implementation path has a different, deliberately narrow policy:
its model may plan and use an executor only within the repository workspace
allowlist and validation-command allowlist. Before push or PR creation, the
workflow blocks edits to workflow files, automation, dependencies, and agent,
skill, configuration, or policy files. This protects the automation from
self-modification and dependency-based escalation.

## GitHub access and publication

Reusable callers own triggers, concurrency, permission declarations, and
secret forwarding. They should grant only the selected workflow's required
permissions. The model never receives a general GitHub write capability or
selects an API endpoint.

For release project review, the workflow validates a canonical `owner/repo`,
accepts exactly one release ID or conservatively validated tag, resolves the
release through the GitHub API, and checks out the exact immutable target
commit read-only. Cross-repository review requires an explicitly forwarded,
target-scoped token with `contents: read` and `issues: write`; the caller's
`GITHUB_TOKEN` is not assumed to work outside its repository.

The release output contract allows either `NO_ISSUE` or a constrained
`CREATE_ISSUE` record. The runner owns the destination repository,
`release-readiness` label allowlist, idempotency marker, and final issue
creation. It rejects output fields that attempt to specify a repository,
endpoint, URL, assignee, or milestone.

### Midflight commands and the two-stage trust boundary

A schema-2 release-project-review bundle may declare up to three unique
`midflight_commands`. When present, the runner performs a two-stage
interaction joined by a workflow-owned, validated handoff:

1. a fresh model invocation under the non-publishing
   `release-project-analysis-handoff-v1` contract produces a bounded
   analysis;
2. the workflow runs each configured command ID through the pinned registry;
3. a fresh model invocation receives the validated handoff and bounded
   command evidence as separate untrusted delimiters and produces the final
   decision.

Security invariants:

- Commands are capabilities, not shell text. Bundles select stable registry
  IDs; the registry maps each ID to a fixed argument vector and execution
  policy. No shell, interpolation, chaining, redirects, arbitrary flags, or
  environment assignments are supported. No bundle, caller, or model input can
  supply argv, environment, working directory, credentials, or artifact globs.
- Command execution is credential-free and disposable. The executor constructs
  the environment from a fixed allowlist that omits provider, GitHub, OIDC,
  SSH, proxy, and caller-secret variables; uses a disposable `HOME`; attaches
  stdin to `/dev/null`; starts a separate process group terminated on timeout;
  applies platform-supported CPU/address-space/file-size/process/open-file
  resource limits fail-closed; streams output into a bounded ring buffer; and
  runs in a fresh disposable copy of the resolved commit discarded before
  either model phase receives filesystem access. Network posture: only
  `network="disabled"` commands are registered and the executor refuses any
  command requesting network; enforced OS-level denial (network namespace /
  locked-down container with no secrets) is the hosted runner's
  responsibility. Until an enforceable, reviewed isolation mechanism is
  demonstrated (asserted via the `network_denial_self_check` helper), midflight
  commands must remain disabled or limited to commands that inspect
  pre-existing files without executing target-controlled code.
- Both model outputs are untrusted. The first response is contract-checked,
  bounded, and inserted into the second prompt as delimited data. It is never
  appended to system instructions or executed. Only the final response can
  publish; the first phase has no publication contract.
- Failures stop closed. Unknown commands, isolation failures, output overflow,
  invalid handoffs, policy mismatches, or resource-control failures do not
  fall back to a one-stage review or skip the midflight phase silently.
- The effective workflow-command policy is restrictive only: an overlay may
  remove command IDs, lower counts/limits, or disable midflight, but cannot add
  commands or relax isolation. It is part of the effective policy hash.

## Idempotency and abuse resistance

Feedback markers include a deterministic configuration digest. PR feedback is
suppressed for the same digest and PR head SHA; issue feedback is suppressed
for the same digest. Release-review idempotency includes the canonical target,
release ID, immutable target commit, configuration digest, and workflow
version. As a result, retried events do not create duplicate feedback, while a
reviewed configuration change intentionally produces a new identity.

Output contracts reject malformed JSON, unknown fields or labels, empty or
oversize content, and unsupported findings. The runner creates at most one
release-readiness issue for a matching release-review identity.

## Logging, provenance, and incident response

Every execution path emits redacted provenance, including validation-only and
configuration failures. Artifacts retain enough information to investigate:
workflow version, target metadata, selected source/profile/SHA, manifest and
prompt hashes, output contract, model profile, effective-policy hash, mode,
and result.

They deliberately exclude credentials, token values, complete prompts, raw
model responses, full issue text, unredacted diffs, and release bodies. Keep
artifact retention short (currently 14 days), restrict access to Actions logs
and artifacts, and never add secrets to bundle files or prompts.

If a workflow behaves unexpectedly, set `validate_only: true` or disable its
wrapper trigger, preserve the redacted artifacts, identify the failing layer,
and explicitly pin a known-good workflow or bundle SHA. Do not automate
rollback to an unreviewed revision. The [operations guide](../developer/operations-guide.md)
contains the detailed incident-response and rollback procedure.
The [operations reference](../reference/operations-reference.md) lists the
normative marker formats, provenance fields, and runtime limits.
