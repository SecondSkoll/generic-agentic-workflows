# Set up your first workflow

In this tutorial, you add the supplied documentation-review wrapper to a GitHub
repository and verify its configuration without invoking a model or publishing
feedback. You finish with a successful validation-only run and its redacted
summary and artifacts.

## Before you start

You need:

- a GitHub repository where you can add a workflow and inspect Actions runs;
- permission to add the `OPENROUTER_API_KEY` Actions secret; and
- a pull request that you can open or update to trigger the wrapper.

Validation-only mode does not use the provider key, but the reusable workflow
interface requires the secret to be forwarded. Use a real secret, never a key
in the workflow file.

## 1. Copy the supplied wrapper

Copy
[`documentation-review.yml`](../how-to/examples/configuration-sources/default/.github/workflows/documentation-review.yml)
from the `default` example into `.github/workflows/documentation-review.yml` in
your repository.

The wrapper selects the supplied `documentation-review` profile and already
sets `validate_only: true`. In this mode, the workflow resolves and validates
the invocation without invoking a model or publishing feedback.

## 2. Add the provider secret

In the repository settings, add an Actions secret named
`OPENROUTER_API_KEY`. Give the credential the smallest practical scope, credit
limit, and lifetime. Do not place its value in the wrapper.

## 3. Commit the wrapper

Commit `.github/workflows/documentation-review.yml` to your repository. Keep
the wrapper under review because it owns the trigger, permissions, and secret
forwarding.

## 4. Trigger validation

Open or update a pull request. The wrapper's `pull_request` trigger starts the
**Documentation review (supplied default configuration)** workflow.

Open the resulting Actions run and wait for the `review` job to finish. Because
`validate_only` is enabled, the run stops after validation and performs no
model invocation or GitHub publication.

## 5. Verify the result

Confirm all of the following:

1. The workflow run succeeds.
2. The job summary reports the resolved invocation and validation-only result.
3. The run exposes the `resolved-invocation-pr-review` artifact, containing the
   available redacted invocation and provenance files.

You have now deployed and validated your first workflow without publishing
feedback.

## Next steps

- Follow [Deploy an agentic workflow](../how-to/deploy-a-workflow.md) to choose
  another workflow or configuration source and progress through dry run to
  publication.
- Use the [configuration reference](../reference/configuration-reference.md)
  to look up every caller input and bundle field.
- Read the [security model](../explanation/security-model.md) to understand the
  trust boundaries behind immutable configuration, typed inputs, and
  validation-only rollout.
