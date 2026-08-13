# Fix line number feedback

When reviewing https://github.com/SecondSkoll/generic-agentic-workflows/pull/12 the review agent used vastly incorrect line numbers when providing additional feedback:

```
Additional feedback (CONTRIBUTING.md:287): This says to calculate hashes for every edited bundle content file, but the examples do not cover the additional agent files mentioned above. Please either include an example for those files or point contributors to the authoritative hash-generation procedure so the examples do not appear exhaustive.
Additional feedback (CONTRIBUTING.md:334): “Obsolete model identifiers” is not defined, so contributors cannot tell which identifiers they must search for or what source of truth to use. Name the deprecated values or link to the policy/test that defines this check.
Additional feedback (docs/plans/documentation.md:354): This description is too vague to orient readers, and the bullets below are not links or actionable plan items. Clarify the intended audience and indicate where each planned topic will be documented.
Additional feedback (docs/plans/documentation.md:363): “the opencode application in GitHub” is ambiguous and uses inconsistent capitalization. Name the specific GitHub integration or project being compared and state which security or operational properties this approach improves.
```

This issue should be investigated and resolved.