# Documentation plan — implemented

The repository configuration and security guide is now published at
[`../configuration.md`](../configuration.md).

It documents:

- supplied local profiles and the safe validation-to-publication rollout;
- repository-local configuration bundles, manifests, hashes, and migration from
	legacy agent/skill variables;
- approved, SHA-pinned remote configuration bundles and private-repository
	token handling;
- the distinction between supported remote bundles and the future direct
	remote-file design in [`allow-remote-files.md`](allow-remote-files.md);
- trust boundaries, prompt-injection mitigations, policy and output controls,
	provenance, incident response, and the rationale for the constrained
	workflow model.

Related operational details are maintained in
[`../operations/operations-guide.md`](../operations/operations-guide.md) and
[`../operations/migration-and-deprecation.md`](../operations/migration-and-deprecation.md).