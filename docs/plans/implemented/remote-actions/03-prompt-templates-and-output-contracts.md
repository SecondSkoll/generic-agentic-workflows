# Plan 3: Prompt Templates and Output Contracts

## Objective

Allow configuration profiles to customize task-specific guidance while
preserving hard safety instructions, trusted identity data, and deterministic
GitHub publication formats. Prompts must be templated, typed, bounded, and
separated from untrusted issue and pull-request content.

## Scope

This plan covers template storage, variables, rendering, output schemas,
structured overrides, and prompt-injection handling. Bundle loading is covered
by Plan 2; centralized model/tool authority is covered by Plan 4.

Affected areas:

- resolved bundle manifest and `prompts/` content
- `scripts/run_agentic_feedback.py`
- issue-implementation prompt construction in its workflow or shared runner
- tests for rendering and structured response validation

## Prompt Composition Model

Construct the effective model request from fixed sections in this order:

1. **Workflow-owned system constraints** — non-overrideable requirements such
   as read-only behavior, author-handle rules, no secret disclosure, and the
   expected output contract.
2. **Validated profile template** — trusted, versioned task guidance from the
   resolved configuration bundle.
3. **Typed runtime context** — verified repository, author, target identity,
   allowed changed lines, and safe profile parameters.
4. **Delimited untrusted content** — PR diff, issue body, or comments, clearly
   labelled as data that cannot modify instructions.
5. **Workflow-owned output suffix** — exact JSON/decision contract and
   validation rules appended last.

The runner must construct these sections itself. A bundle may customize its
profile template but cannot replace, reorder, or remove sections 1 or 5.

## Template Format and Variables

Start with a minimal brace-token format implemented with the Python standard
library, for example `{{repository}}`. Do not evaluate expressions, load
includes dynamically, invoke shell, call Python, or interpolate environment
variables.

Provide a fixed variable catalog:

| Variable | Value source | Notes |
| --- | --- | --- |
| `repository` | GitHub runtime | Authoritative `owner/repo`. |
| `feedback_kind` | workflow | Stable workflow identifier. |
| `author_login` | GitHub API | Verified login without an implied `@`. |
| `target_number` | GitHub API | PR or issue number. |
| `target_title` | GitHub API | Untrusted text encoded as data. |
| `focus` | typed input | Allowlisted enum only. |
| `max_comments` | typed input/profile | Clamped to configured maximum. |
| `allowed_locations` | parsed trusted diff | Only for PR inline feedback. |
| `untrusted_content` | fetched event data | Inserted only in the delimited data section. |

Reject templates with unknown, repeated where harmful, or disallowed variables.
Place limits on template size, rendered prompt size, and untrusted content
size. Document a deterministic truncation strategy that retains target metadata
and marks omitted data rather than silently changing instructions.

## Structured Overrides

Replace raw `--prompt` or unrestricted `prompt_overrides` values with an
explicit configuration object validated by the runner:

```json
{
  "focus": "documentation",
  "response_style": "concise",
  "max_comments": 8,
  "include_suggestions": true
}
```

Rules:

- Each key is opt-in per profile; unknown keys are errors.
- Enums are allowlisted; strings have length and character limits.
- Numeric settings are bounded both globally and by profile policy.
- A caller may make policy stricter (for example, lower `max_comments`) but
  cannot turn on capabilities a profile forbids.
- Workflow dispatch inputs map to individual typed fields, not a free-form JSON
  blob, unless a future parser validates a signed/strict schema.

## Untrusted Content Boundaries

Issue bodies, comments, titles, labels, branch names, file names, diffs, and
PR descriptions are all data supplied by potentially untrusted contributors.
Render them only inside explicit markers such as:

```text
<untrusted-issue-content>
...
</untrusted-issue-content>
```

Tell the model that text inside these markers is reference material and cannot
alter its instructions, request secrets, select tools, change output format, or
make publication decisions. Do not embed untrusted data in template variable
names, workflow expressions, shell commands, filesystem paths, or configuration
selectors.

For diff review, preserve the existing added-line location allowlist and attach
it separately from the diff. Model-provided locations continue to be validated
before a GitHub review is posted.

## Output Contract Registry

Create a workflow-owned output-contract registry rather than letting templates
specify arbitrary response shapes.

Initial contracts:

- `pr-review-json-v1`: nonempty JSON `summary` plus zero or more validated
  inline comments with optional suggestions.
- `issue-feedback-markdown-v1`: concise Markdown with no instruction-bearing
  machine fields.
- `issue-implementation-decision-v1`: an `IMPLEMENT` or `BLOCKED` decision,
  with a maintainer-actionable blocker when blocked.

The registry defines parser behavior, maximum response sizes, accepted fields,
field types, and publication mapping. The runner appends the appropriate
contract instruction after all template content, parses the output strictly,
and fails safely on malformed output.

Improve `parse_review_output` incrementally by:

- rejecting unknown top-level fields only if the versioned contract requires it;
- bounding summary, comment count, body, suggestion, and path lengths;
- deduplicating identical comments;
- retaining invalid locations only in summary when safe, as it does today;
- ensuring output never controls API endpoints, repository identity, or
  permissions.

## Implementation Steps

1. Create a shared prompt-rendering module with typed context and no external
   dependencies.
2. Define template syntax, a variable registry, limits, and clear exceptions.
3. Add validated template data to the bundle resolver result.
4. Move current hard-coded prompts into workflow-owned contract/default
   templates, preserving existing behavior for legacy local configurations.
5. Replace direct prompt concatenation in `run_agentic_feedback.py` with the
   ordered composition model.
6. Use the same composer for issue implementation so planner and executor
   prompts receive identical safety boundaries.
7. Add structured CLI arguments or a JSON file generated by the workflow for
   typed overrides; do not pass untrusted strings through shell interpolation.
8. Add output-contract identifiers to provenance reports and feedback markers.

## Tests

- Exact rendering for valid templates and every supported variable.
- Unknown/malformed tokens, oversized templates, missing context, and invalid
  UTF-8 rejection.
- Escaping and delimiter tests using adversarial issue/PR text that attempts to
  override instructions.
- Override validation, enum rejection, numeric clamping, and restrictive
  precedence behavior.
- Contract parsing for valid, malformed, oversized, and unexpected outputs.
- Regression tests for the existing PR JSON handling and invalid inline
  location fallback.
- Tests proving untrusted content cannot affect configuration source, agent,
  skill, model profile, API URL, or publication mode.

## Acceptance Criteria

- Profiles can tailor task guidance using only documented variables and typed
  overrides.
- Workflow-owned safety and output requirements are always appended and cannot
  be replaced by a profile.
- Untrusted content is visibly delimited and never used as executable
  configuration.
- Every workflow publishes only output that passes a versioned contract.
