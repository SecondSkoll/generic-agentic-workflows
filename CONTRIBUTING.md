# Contributing

Thanks for contributing to `generic-agentic-workflows`.

## Development workflow

1. Create a branch from the current default branch.
2. Keep changes focused; do not mix workflow, agent, and dependency changes unless they are required together.
3. Run the relevant tests before opening a pull request:

   ```text
   python3 -m unittest
   ```

4. Run `git diff --check` to catch whitespace errors.
5. Describe the behavior change, the configuration bundles affected, and validation performed in the pull request.

## Editing OpenCode configuration bundles

Reusable profiles live under `.opencode/configuration/<profile>/`. A bundle normally contains:

- `manifest.json` — declares the workflow, agent, skills, prompt, and model profile.
- `agent.md` and optional additional agent files — OpenCode agent front matter and instructions.
- `skills/` and `prompts/` — the bundle's verified guidance.
- `hashes.json` — SHA-256 integrity hashes for every declared content file.

The resolver rejects a bundle when a declared file does not exactly match its entry in `hashes.json`. Therefore, **every content edit must update the corresponding hash**. This includes agent instructions and front matter such as `model:`, prompt templates, skills, and additional agents.

The organization-controlled model profile mapping is in `scripts/agentic_policy.py`. When changing the model for a bundle, update both:

1. The bundle agent's `model:` front-matter value.
2. The corresponding `provider_model` in `MODEL_PROFILES`.

Do not introduce credentials, endpoints, headers, or caller-controlled model identifiers in bundle files or policies.

## Updating `hashes.json`

From the repository root, calculate SHA-256 values for every edited bundle content file:

```text
sha256sum .opencode/configuration/<profile>/agent.md
sha256sum .opencode/configuration/<profile>/prompts/<prompt>.md
sha256sum .opencode/configuration/<profile>/skills/<skill>/SKILL.md
```

Copy each resulting 64-character digest into the matching key in that profile's `hashes.json`. Preserve the path spelling exactly; paths are relative to the bundle directory.

For example, after editing `documentation-review/agent.md`, update:

```json
{
  "agent.md": "<sha256 of agent.md>"
}
```

Do not hash `manifest.json` into `hashes.json` unless the resolver is explicitly changed to require it. Do not alter an unrelated bundle's hashes merely to make a test pass—investigate the mismatch and update only when the checked-in content intentionally changed.

## Validate bundle changes

Resolve each changed bundle directly before opening a pull request:

```text
python3 scripts/agentic_configuration.py \
  --workflow pr-documentation-review \
  --bundle-root .opencode/configuration \
  --configuration-profile documentation-review \
  --result /tmp/documentation-review.json

python3 scripts/agentic_configuration.py \
  --workflow issue-feedback \
  --bundle-root .opencode/configuration \
  --configuration-profile issue-feedback \
  --result /tmp/issue-feedback.json
```

Use the appropriate workflow and profile for other bundles, such as `issue-implementation` and `default-implementation`. Then run the focused configuration and policy tests:

```text
python3 -m unittest -q \
  tests.test_agentic_policy \
  tests.test_agentic_configuration \
  tests.test_revision2
```

Finally, confirm that obsolete model identifiers are absent from active configuration and policy files, and run:

```text
git diff --check
```

## Security requirements

- Treat issue text, pull-request text, diffs, and external configuration as untrusted data.
- Preserve the read-only boundaries of feedback agents and the policy restrictions on implementation agents.
- Do not add secrets to tracked files, logs, prompts, or test fixtures.
- Do not weaken pinned references, integrity validation, workflow permissions, or policy ceilings without an approved security review.
