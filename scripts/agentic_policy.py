#!/usr/bin/env python3
"""Model/tool policy engine for agentic workflows (Plan 4).

Dependency-free. Resolves policy in a fixed five-layer precedence using
restrictive operations only: set intersection for allowlists, minimum for
quotas, logical AND for permissions, and explicit rejection for conflicting
required values. No layer can broaden a capability granted by a higher
layer. The output is an inspectable effective-policy report.

Layers, in precedence order (1 is highest):

1. Built-in workflow safety policy (immutable, shipped with this module).
2. Organization policy (pinned, allowlisted central policy document).
3. Bundle profile policy (validated bundle, may make policy stricter).
4. Consumer local overlay (trusted local file, may reduce scope).
5. Typed invocation inputs (may make policy stricter only).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Mapping


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PolicyError(ValueError):
    """Raised when a policy layer cannot be validated or merged."""


# ---------------------------------------------------------------------------
# Capability model
# ---------------------------------------------------------------------------

#: Capability axes a policy may grant or deny.
CAPABILITY_AXES: tuple[str, ...] = (
    "filesystem",
    "shell",
    "network",
    "github_write",
    "delegation",
)

#: Capability values. ``deny`` is the most restrictive; ``allow`` requires
#: every layer to allow it. Specific allowlisted scopes (e.g.
#: ``read-trusted-checkout``) intersect by string equality.
DENY = "deny"


def intersect_capability(a: str, b: str) -> str:
    """Intersect two capability values.

    ``deny`` always wins. Two equal non-deny values intersect to themselves.
    Two different non-deny values intersect to ``deny`` (no layer can broaden
    a different specific grant).
    """
    if a == DENY or b == DENY:
        return DENY
    if a == b:
        return a
    return DENY


def min_int(a: int | None, b: int | None) -> int | None:
    """Return the minimum of two optional ints; ``None`` means unbounded."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def intersect_string_sets(a: Iterable[str], b: Iterable[str]) -> frozenset[str]:
    """Return the restrictive intersection of two string allowlists."""
    return frozenset(a) & frozenset(b)


# ---------------------------------------------------------------------------
# Built-in safety policy (layer 1)
# ---------------------------------------------------------------------------

#: Per-workflow built-in safety policy. These cannot be overridden.
BUILTIN_SAFETY_POLICY: dict[str, dict[str, Any]] = {
    "pr-documentation-review": {
        "capabilities": {
            "filesystem": "read-trusted-checkout-diff",
            "shell": DENY,
            "network": "provider-only",
            "github_write": "review-comment-only",
            "delegation": DENY,
        },
        "required_output_contract": "pr-review-json-v1",
        "required_source": "trusted-or-allowlisted",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ("pr-documentation-review",),
        "data_classification": "repository-content",
        "deny_path_patterns": [
            r"^\.github/workflows/",
            r"^\.opencode/",
        ],
        "allow_changed_file_context": True,
        "allow_repository_manifest": True,
        "allow_repository_file_context": True,
        "max_context_rounds": 3,
        "max_context_files": 20,
        "max_context_file_bytes": 200 * 1024,
        "max_context_total_bytes": 1024 * 1024,
        "max_manifest_entries": 5000,
    },
    "issue-feedback": {
        "capabilities": {
            "filesystem": "read-issue-context-only",
            "shell": DENY,
            "network": "provider-only",
            "github_write": "issue-comment-only",
            "delegation": DENY,
        },
        "required_output_contract": "issue-feedback-markdown-v1",
        "required_source": "trusted-or-allowlisted",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ("issue-feedback",),
        "data_classification": "repository-content",
        "deny_path_patterns": [
            r"^\.github/workflows/",
            r"^\.opencode/",
        ],
    },
    "issue-implementation": {
        "capabilities": {
            "filesystem": "repository-workspace-allowlist",
            "shell": "validation-commands-only",
            "network": "provider-and-github-api-only",
            "github_write": "scoped-branch-pr-issue",
            "delegation": "planner-to-executor",
        },
        "required_output_contract": "issue-implementation-decision-v1",
        "required_source": "trusted-or-allowlisted",
        "max_tokens": 16000,
        "temperature_max": 0.2,
        "timeout_seconds": 300,
        "max_retries": 1,
        "allowed_workflows": ("issue-implementation",),
        "data_classification": "repository-content",
        #: Issue-implementation changed-path denials. These categories are
        #: machine-enforced before any push or PR creation. The exact list
        #: comes from Plan 4: workflows, automation, dependencies, and agent
        #: instruction files.
        "deny_path_patterns": [
            r"^\.github/workflows/",
            r"^\.github/actions/",
            r"^\.opencode/agents/",
            r"^\.opencode/skills/",
            r"^\.opencode/configuration/",
            r"^\.opencode/policy/",
            r"^package\.json$",
            r"^package-lock\.json$",
            r"^bun\.lock$",
            r"^uv\.lock$",
            r"^pyproject\.toml$",
            r"^requirements\.txt$",
            r"^Cargo\.toml$",
            r"^Cargo\.lock$",
            r"^go\.mod$",
            r"^go\.sum$",
            r"^pnpm-lock\.yaml$",
            r"^yarn\.lock$",
            r"^Gemfile$",
            r"^Gemfile\.lock$",
        ],
    },
    "release-project-review": {
        "capabilities": {
            "filesystem": "read-release-context-only",
            "shell": DENY,
            "network": "provider-only",
            "github_write": "issue-create-only",
            "delegation": DENY,
        },
        "required_output_contract": "release-project-issue-v1",
        "required_source": "trusted-or-allowlisted",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ("release-project-review",),
        "data_classification": "repository-content",
        #: Release review is read-only over the checked-out release context.
        #: The workflow (not the model) owns the issue destination, labels,
        #: marker, and publication. The model may not select an endpoint,
        #: repository, labels, or credentials.
        "deny_path_patterns": [
            r"^\.github/workflows/",
            r"^\.opencode/",
        ],
        #: Bounded release/repository read context. The runner collects an
        #: allowlisted set of release metadata and operational documents at
        #: the immutable target commit and treats them as untrusted.
        "allow_release_context": True,
        "max_release_context_files": 8,
        "max_release_context_file_bytes": 64 * 1024,
        "max_release_context_total_bytes": 256 * 1024,
        #: Publication is workflow-mediated issue creation only, and for an
        #: external target repository requires an explicitly forwarded,
        #: target-scoped token (verified by the runner before publication).
        "issue_create_only": True,
        "require_external_target_authorization": True,
        #: Workflow-owned command controls (midflight commands). Restrictive
        #: only: a lower layer may remove command IDs, lower counts/limits, or
        #: disable midflight, but cannot add commands or relax isolation. The
        #: bundle command list is intersected with this ceiling and the pinned
        #: registry. This section is part of the effective policy hash so a
        #: command-policy change is detectable in provenance.
        "workflow_commands": {
            "allowed_phases": ("preflight", "midflight"),
            "max_commands_per_phase": 3,
            "max_commands_total": 3,
            "allowed_registry_ids": ("documentation-build", "python-pytest"),
            "required_isolation_profile": "release-midflight-v1",
            "max_total_wall_seconds": 600,
            "max_total_output_bytes": 64 * 1024,
            "max_model_phases": 2,
        },
    },
}


# ---------------------------------------------------------------------------
# Model profile registry (layer 2 / organization-controlled)
# ---------------------------------------------------------------------------

#: Approved model profiles. A bundle references a profile by name only. No
#: profile exposes provider credentials, API base URLs, headers, or arbitrary
#: model strings. The configured model IDs are taken from the existing
#: repository agents (no secrets, no placeholders).
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "review-readonly": {
        "provider_model": "openrouter/openai/gpt-5.6-luna",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ["pr-documentation-review"],
        "data_classification": "repository-content",
    },
    "issue-feedback-readonly": {
        "provider_model": "openrouter/openai/gpt-5.6-luna",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ["issue-feedback"],
        "data_classification": "repository-content",
    },
    "implementation-planner": {
        "provider_model": "openrouter/openai/gpt-5.6-terra",
        "max_tokens": 16000,
        "temperature_max": 0.2,
        "timeout_seconds": 300,
        "max_retries": 1,
        "allowed_workflows": ["issue-implementation"],
        "data_classification": "repository-content",
    },
    "release-project-review-readonly": {
        "provider_model": "openrouter/openai/gpt-5.6-luna",
        "max_tokens": 8000,
        "temperature_max": 0.2,
        "timeout_seconds": 180,
        "max_retries": 1,
        "allowed_workflows": ["release-project-review"],
        "data_classification": "repository-content",
    },
}

#: Organization ceilings. Every profile limit must be at or below these.
ORG_CEILINGS: dict[str, int] = {
    "max_tokens": 16000,
    "temperature_max": 0.2,
    "timeout_seconds": 300,
    "max_retries": 1,
}


def validate_model_profile(name: str, *, workflow: str) -> dict[str, Any]:
    """Validate that a named model profile exists and supports the workflow."""
    if not isinstance(name, str) or name not in MODEL_PROFILES:
        raise PolicyError(f"unknown model profile: {name!r}")
    profile = MODEL_PROFILES[name]
    allowed = profile.get("allowed_workflows", [])
    if workflow not in allowed:
        raise PolicyError(
            f"model profile {name!r} does not allow workflow {workflow!r}"
        )
    for key, ceiling in ORG_CEILINGS.items():
        value = profile.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PolicyError(f"model profile {name!r} has non-numeric {key}")
        if value > ceiling:
            raise PolicyError(
                f"model profile {name!r} {key}={value} exceeds organization ceiling {ceiling}"
            )
    if not isinstance(profile.get("provider_model"), str):
        raise PolicyError(f"model profile {name!r} has no provider_model")
    return profile


# ---------------------------------------------------------------------------
# Agent front-matter capability parsing
# ---------------------------------------------------------------------------


def parse_agent_capabilities(text: str) -> dict[str, str]:
    """Parse requested capabilities from an agent's front matter.

    Treats each ``permission.<axis>`` line as a *request*, not an
    authorization. Returns a mapping of axis -> requested value.
    """
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise PolicyError("agent file must start with YAML front matter")
    body = match.group(1)
    # Map permission keys to capability axes.
    axis_map = {
        "edit": "filesystem",
        "bash": "shell",
        "read": "filesystem",
        "network": "network",
        "external_directory": "filesystem",
        "task": "delegation",
        "skill": "delegation",
        "github_write": "github_write",
    }
    requested: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        m = re.match(r"^([A-Za-z_]+):\s*(.+)$", stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in axis_map:
            axis = axis_map[key]
            if value in {"allow", "deny"}:
                grant = "allow" if value == "allow" else DENY
            else:
                grant = value  # scoped string
            existing = requested.get(axis)
            if existing is None:
                requested[axis] = grant
            else:
                requested[axis] = intersect_capability(existing, grant)
    return requested


def validate_delegated_agent(text: str, *, workflow: str) -> dict[str, str]:
    """Validate a delegated (subagent) agent's front matter against policy.

    A delegated agent (``mode: subagent``) must not grant itself capabilities
    the workflow forbids. For issue-implementation, the executor may edit and
    run narrowly-allowed validation commands, but must not request network
    beyond provider/GitHub API, broad delegation, or workflow-file edits.
    Returns the parsed front matter.
    """
    front = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise PolicyError("delegated agent file must start with YAML front matter")
    body = match.group(1)
    for line in body.splitlines():
        stripped = line.strip()
        m = re.match(r"^([A-Za-z_]+):\s*(.+)$", stripped)
        if not m:
            continue
        front[m.group(1)] = m.group(2).strip()
    if front.get("mode") != "subagent":
        raise PolicyError(
            f"delegated agent {front.get('name', '<unknown>')!r} must have mode: subagent"
        )
    caps = parse_agent_capabilities(text)
    builtin = BUILTIN_SAFETY_POLICY.get(workflow)
    if builtin is None:
        raise PolicyError(f"unknown workflow: {workflow!r}")
    for axis, request in caps.items():
        if axis not in CAPABILITY_AXES:
            continue
        allowed = builtin["capabilities"].get(axis, DENY)
        # A delegated agent may never request a capability axis the workflow
        # denies entirely. Scoped axes (e.g. filesystem "workspace-allowlist")
        # are narrowed by the effective-policy intersection in merge_policy, so
        # a broad "allow" request there is recorded but not hard-rejected; only
        # an outright DENY axis is a hard violation for a delegated agent.
        if allowed == DENY and request != DENY:
            raise PolicyError(
                f"delegated agent {front.get('name')!r} requests forbidden {axis}={request!r} "
                f"for workflow {workflow!r}"
            )
    return front


# ---------------------------------------------------------------------------
# Effective policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectivePolicy:
    """Immutable merged policy for a single workflow run."""

    workflow: str
    model_profile: str
    provider_model: str
    max_tokens: int
    temperature_max: float
    timeout_seconds: int
    max_retries: int
    capabilities: dict[str, str]
    allowed_workflows: tuple[str, ...]
    output_contract: str
    data_classification: str
    deny_path_patterns: tuple[str, ...]
    publication_allowed: bool
    test_commands: tuple[str, ...]
    rejected_conflicts: tuple[str, ...]
    layers: tuple[dict[str, Any], ...]
    sha256: str
    context: dict[str, Any] = field(default_factory=dict)
    workflow_commands: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable effective-policy report."""
        return {
            "workflow": self.workflow,
            "model_profile": self.model_profile,
            "provider_model": self.provider_model,
            "max_tokens": self.max_tokens,
            "temperature_max": self.temperature_max,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "capabilities": dict(self.capabilities),
            "allowed_workflows": list(self.allowed_workflows),
            "output_contract": self.output_contract,
            "data_classification": self.data_classification,
            "deny_path_patterns": list(self.deny_path_patterns),
            "publication_allowed": self.publication_allowed,
            "test_commands": list(self.test_commands),
            "rejected_conflicts": list(self.rejected_conflicts),
            "sha256": self.sha256,
            "context": dict(self.context),
            "workflow_commands": dict(self.workflow_commands),
        }

    def to_json(self) -> str:
        """Serialize this policy as deterministic, pretty JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def _layer(name: str, **fields: Any) -> dict[str, Any]:
    """Build a labelled policy-layer record for diagnostics and hashing."""
    return {"layer": name, **fields}


#: Numeric workflow-command ceilings that a lower layer may only lower.
_WORKFLOW_COMMAND_NUMERIC_CEILINGS: tuple[str, ...] = (
    "max_commands_per_phase",
    "max_commands_total",
    "max_total_wall_seconds",
    "max_total_output_bytes",
    "max_model_phases",
)


def _merge_workflow_commands(
    builtin: dict[str, Any],
    bundle: dict[str, Any],
    overlay: dict[str, Any],
    invocation: Mapping[str, Any],
    workflow: str,
) -> dict[str, Any]:
    """Merge the workflow-command ceiling restrictively across layers.

    Starts from the built-in ceiling. The bundle and overlay may only remove
    command IDs, lower counts/limits, remove phases, or disable midflight;
    they cannot add commands, add phases, or relax isolation. The result is
    included in the effective policy hash so a command-policy change is
    detectable in provenance.
    """
    base = builtin.get("workflow_commands")
    if not isinstance(base, dict):
        return {}
    # Deep-copy the built-in ceiling.
    effective: dict[str, Any] = {
        "allowed_phases": tuple(base.get("allowed_phases", ())),
        "max_commands_per_phase": base.get("max_commands_per_phase"),
        "max_commands_total": base.get("max_commands_total"),
        "allowed_registry_ids": tuple(base.get("allowed_registry_ids", ())),
        "required_isolation_profile": base.get("required_isolation_profile"),
        "max_total_wall_seconds": base.get("max_total_wall_seconds"),
        "max_total_output_bytes": base.get("max_total_output_bytes"),
        "max_model_phases": base.get("max_model_phases"),
    }
    for layer_name, layer in (("bundle", bundle), ("overlay", overlay)):
        section = layer.get("workflow_commands")
        if not isinstance(section, dict):
            continue
        if "allowed_phases" in section:
            phases = section["allowed_phases"]
            if not isinstance(phases, list):
                raise PolicyError(f"{layer_name} workflow_commands.allowed_phases must be a list")
            current_phases = set(effective["allowed_phases"])
            for phase in phases:
                if phase not in current_phases:
                    raise PolicyError(
                        f"{layer_name} workflow_commands.allowed_phases cannot "
                        f"add phase {phase!r}"
                    )
            effective["allowed_phases"] = tuple(phases)
        if "allowed_registry_ids" in section:
            ids = section["allowed_registry_ids"]
            if not isinstance(ids, list):
                raise PolicyError(
                    f"{layer_name} workflow_commands.allowed_registry_ids must be a list"
                )
            current_ids = set(effective["allowed_registry_ids"])
            for command_id in ids:
                if command_id not in current_ids:
                    raise PolicyError(
                        f"{layer_name} workflow_commands.allowed_registry_ids cannot "
                        f"add command {command_id!r}"
                    )
            effective["allowed_registry_ids"] = tuple(ids)
        for key in _WORKFLOW_COMMAND_NUMERIC_CEILINGS:
            if key in section:
                value = section[key]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise PolicyError(f"{layer_name} workflow_commands.{key} must be an integer")
                ceiling = effective[key]
                if ceiling is not None and value > ceiling:
                    raise PolicyError(
                        f"{layer_name} workflow_commands.{key}={value} exceeds ceiling {ceiling}"
                    )
                effective[key] = value
        # A lower layer may not change the required isolation profile to a
        # different value (it cannot broaden or substitute).
        if "required_isolation_profile" in section:
            requested = section["required_isolation_profile"]
            if requested != effective["required_isolation_profile"]:
                raise PolicyError(
                    f"{layer_name} workflow_commands.required_isolation_profile "
                    "cannot be broadened or substituted"
                )
    return effective


def merge_policy(
    *,
    workflow: str,
    model_profile: str,
    agent_capabilities: Mapping[str, str] | None = None,
    bundle_policy: Mapping[str, Any] | None = None,
    overlay: Mapping[str, Any] | None = None,
    invocation_inputs: Mapping[str, Any] | None = None,
) -> EffectivePolicy:
    """Merge all five policy layers into an :class:`EffectivePolicy`.

    Restrictive only. Any attempt by a lower layer to broaden a capability
    granted by a higher layer is rejected and recorded.
    """
    if workflow not in BUILTIN_SAFETY_POLICY:
        raise PolicyError(f"unknown workflow: {workflow!r}")
    builtin = BUILTIN_SAFETY_POLICY[workflow]
    rejected: list[str] = []
    layers: list[dict[str, Any]] = []

    profile = validate_model_profile(model_profile, workflow=workflow)
    layers.append(_layer("builtin-safety", source="shipped", **builtin))
    layers.append(_layer("model-profile", source=model_profile, **profile))

    # Layer 3: bundle profile policy (may make stricter).
    bundle = dict(bundle_policy or {})
    if bundle:
        layers.append(_layer("bundle-profile", source="bundle", **bundle))
        if "capabilities" in bundle:
            for axis, value in bundle["capabilities"].items():
                if axis not in CAPABILITY_AXES:
                    raise PolicyError(
                        f"bundle policy references unknown capability axis: {axis}"
                    )
                builtin_value = builtin["capabilities"][axis]
                merged = intersect_capability(builtin_value, value)
                # A bundle may only narrow (set to deny or to the same value).
                # Broadening or substituting a different grant is a hard error.
                if merged != value:
                    raise PolicyError(
                        f"bundle capability {axis}={value!r} broadens or conflicts with "
                        f"builtin {builtin_value!r}"
                    )
        if "max_tokens" in bundle:
            ceiling = min_int(builtin["max_tokens"], profile["max_tokens"])
            if bundle["max_tokens"] > ceiling:
                raise PolicyError(
                    f"bundle max_tokens={bundle['max_tokens']} exceeds ceiling {ceiling}"
                )

    # Layer 4: consumer local overlay (may reduce scope only).
    overlay = dict(overlay or {})
    if overlay:
        layers.append(_layer("consumer-overlay", source="local", **overlay))
        if "max_comments" in overlay and not isinstance(overlay["max_comments"], int):
            raise PolicyError("overlay max_comments must be an integer")
        if "allowed_focus" in overlay:
            for focus in overlay["allowed_focus"]:
                if focus not in {"documentation", "security", "tests", "general"}:
                    raise PolicyError(
                        f"overlay allowed_focus contains unknown value: {focus!r}"
                    )
        if "publication" in overlay:
            if not isinstance(overlay["publication"], dict):
                raise PolicyError("overlay publication must be an object")
        if "test_commands" in overlay:
            cmds = overlay["test_commands"]
            if not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds):
                raise PolicyError("overlay test_commands must be a list of strings")
            # Only implementation workflows may declare test commands.
            if workflow != "issue-implementation" and cmds:
                raise PolicyError(
                    "overlay test_commands are only permitted for issue-implementation"
                )
        # An overlay may never grant capabilities, broaden network, or
        # introduce secrets.
        for forbidden in ("capabilities", "provider_model", "secrets", "env"):
            if forbidden in overlay:
                raise PolicyError(f"overlay may not set {forbidden!r}")

    # Layer 5: typed invocation inputs (stricter only).
    invocation = dict(invocation_inputs or {})
    if invocation:
        layers.append(_layer("invocation", source="inputs", **invocation))
        if "max_comments" in invocation and "max_comments" in overlay:
            if invocation["max_comments"] > overlay["max_comments"]:
                raise PolicyError(
                    "invocation max_comments cannot exceed overlay maximum"
                )

    # Compute effective capabilities: intersect builtin, agent request,
    # bundle, overlay (publication flag only).
    effective_caps: dict[str, str] = dict(builtin["capabilities"])
    if agent_capabilities:
        for axis, request in agent_capabilities.items():
            if axis not in CAPABILITY_AXES:
                # Unknown requests are denied, never granted.
                rejected.append(f"agent requested unknown capability axis: {axis}")
                continue
            current = effective_caps[axis]
            # An agent request cannot broaden; it can only be granted if it
            # matches the current value or is denied.
            if request == DENY:
                effective_caps[axis] = DENY
            elif request == current:
                pass
            else:
                # The agent requested a broader or different capability than
                # the builtin allows. Deny and record.
                rejected.append(
                    f"agent requested {axis}={request!r} but builtin allows {current!r}; denied"
                )
                effective_caps[axis] = DENY
    if bundle and "capabilities" in bundle:
        for axis, value in bundle["capabilities"].items():
            effective_caps[axis] = intersect_capability(effective_caps[axis], value)

    # Effective numeric limits: minima across builtin, profile, bundle.
    max_tokens = min_int(builtin["max_tokens"], profile["max_tokens"])
    if bundle and "max_tokens" in bundle:
        max_tokens = min_int(max_tokens, bundle["max_tokens"])
    timeout = min_int(builtin["timeout_seconds"], profile["timeout_seconds"])
    retries = min_int(builtin["max_retries"], profile["max_retries"])

    publication_allowed = True
    if overlay and "publication" in overlay:
        publication_allowed = bool(overlay["publication"].get("allow", True))
    if invocation.get("dry_run") or invocation.get("validate_only"):
        publication_allowed = False

    test_commands: tuple[str, ...] = ()
    if overlay and "test_commands" in overlay:
        test_commands = tuple(overlay["test_commands"])

    deny_patterns = tuple(builtin["deny_path_patterns"])

    # Additional PR context can only be narrowed by lower layers. Capability
    # booleans intersect with AND; numeric limits use the minimum.
    context_keys = (
        "max_context_rounds", "max_context_files", "max_context_file_bytes",
        "max_context_total_bytes", "max_manifest_entries",
    )
    context = {key: builtin.get(key) for key in context_keys if key in builtin}
    for key in ("allow_changed_file_context", "allow_repository_manifest", "allow_repository_file_context"):
        if key in builtin:
            context[key] = bool(builtin[key])
    for layer_name, layer in (("bundle", bundle), ("overlay", overlay), ("invocation", invocation)):
        for key in context_keys:
            if key in layer:
                value = layer[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise PolicyError(f"{layer_name} {key} must be a non-negative integer")
                if value > context[key]:
                    raise PolicyError(f"{layer_name} {key} cannot exceed the effective ceiling")
                context[key] = min(context[key], value)
        for key in ("allow_changed_file_context", "allow_repository_manifest", "allow_repository_file_context"):
            if key in layer:
                if not isinstance(layer[key], bool):
                    raise PolicyError(f"{layer_name} {key} must be a boolean")
                if layer[key] and not context[key]:
                    raise PolicyError(f"{layer_name} cannot enable disabled {key}")
                context[key] = context[key] and layer[key]
    context["deny_path_patterns"] = (
        *tuple(deny_patterns),
        r"(^|/)\.env($|\.)", r"\.pem$", r"\.key$", r"(^|/)id_rsa$",
        r"(^|/)secrets\.", r"(^|/)(?:\.npmrc|\.pypirc|\.netrc)$",
    )

    # Workflow-owned command controls (midflight commands). Restrictive only:
    # a lower layer may remove command IDs, lower counts/limits, or disable
    # midflight, but cannot add commands, add phases, or relax isolation. The
    # effective command policy is part of the policy hash.
    workflow_commands = _merge_workflow_commands(
        builtin, bundle, overlay, invocation, workflow
    )

    policy_dict = {
        "workflow": workflow,
        "model_profile": model_profile,
        "provider_model": profile["provider_model"],
        "max_tokens": max_tokens,
        "temperature_max": profile["temperature_max"],
        "timeout_seconds": timeout,
        "max_retries": retries,
        "capabilities": effective_caps,
        "allowed_workflows": tuple(builtin["allowed_workflows"]),
        "output_contract": builtin["required_output_contract"],
        "data_classification": profile["data_classification"],
        "deny_path_patterns": deny_patterns,
        "publication_allowed": publication_allowed,
        "test_commands": test_commands,
        "rejected_conflicts": tuple(rejected),
        "layers": layers,
        "context": context,
        "workflow_commands": workflow_commands,
    }
    import hashlib

    sha = hashlib.sha256(
        json.dumps(policy_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EffectivePolicy(
        workflow=workflow,
        model_profile=model_profile,
        provider_model=profile["provider_model"],
        max_tokens=max_tokens,
        temperature_max=profile["temperature_max"],
        timeout_seconds=timeout,
        max_retries=retries,
        capabilities=effective_caps,
        allowed_workflows=tuple(builtin["allowed_workflows"]),
        output_contract=builtin["required_output_contract"],
        data_classification=profile["data_classification"],
        deny_path_patterns=deny_patterns,
        publication_allowed=publication_allowed,
        test_commands=test_commands,
        rejected_conflicts=tuple(rejected),
        layers=tuple(layers),
        sha256=sha,
        context=context,
        workflow_commands=workflow_commands,
    )


# ---------------------------------------------------------------------------
# Changed-path enforcement (Plan 4 implementation workflow)
# ---------------------------------------------------------------------------


def enforce_changed_paths(
    changed_paths: Iterable[str],
    *,
    workflow: str,
    extra_deny_patterns: Iterable[str] | None = None,
) -> list[str]:
    """Reject changed paths that match a denied category for ``workflow``.

    Returns the list of offending paths. Empty list means the diff is
    acceptable. Used by the issue-implementation workflow before any push or
    PR creation. The denied categories come from
    :data:`BUILTIN_SAFETY_POLICY` for ``issue-implementation``.
    """
    if workflow not in BUILTIN_SAFETY_POLICY:
        raise PolicyError(f"unknown workflow: {workflow!r}")
    patterns = list(BUILTIN_SAFETY_POLICY[workflow]["deny_path_patterns"])
    if extra_deny_patterns:
        patterns.extend(extra_deny_patterns)
    compiled = [re.compile(p) for p in patterns]
    offenders: list[str] = []
    for path in changed_paths:
        if not isinstance(path, str):
            raise PolicyError(f"changed path must be a string, got {path!r}")
        normalized = path.replace("\\", "/")
        # Strip a leading "./" only; do not strip other leading characters.
        while normalized.startswith("./"):
            normalized = normalized[2:]
        for pattern in compiled:
            if pattern.search(normalized):
                offenders.append(normalized)
                break
    return offenders


def collect_implementation_changed_paths(
    repo_root: Path,
    *,
    base_ref: str | None = None,
) -> list[str]:
    """Collect every changed path in the implementation workspace.

    Captures tracked changes (committed or staged relative to ``base_ref`` or
    the default branch) AND untracked files, so agent-created files and
    uncommitted edits cannot bypass enforcement. Uses ``git status --porcelain``
    for the working tree (untracked + modified + staged) and ``git diff`` for
    committed ranges when a base ref is supplied.

    When ``base_ref`` is supplied and cannot be resolved (not fetched/unknown),
    this raises :class:`PolicyError` rather than silently omitting the
    committed range. The caller must establish a trusted base ref (for example
    by fetching the default branch) before calling with one.
    """
    import subprocess

    paths: set[str] = set()
    # Working-tree changes: modified, staged, AND untracked files.
    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in status.stdout.splitlines():
        if not line:
            continue
        # Format: "XY <path>" where XY is status; path may be quoted.
        path = line[3:]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        # Rename format: "old -> new"; take the new path.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        paths.add(path.replace("\\", "/"))
    # Committed changes relative to a base ref (catches agent-created commits).
    if base_ref:
        # Verify the base ref is resolvable before diffing; hard-fail if not
        # so a missing default branch never silently omits committed changes.
        cat = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-t", base_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if cat.returncode != 0:
            raise PolicyError(
                f"base ref {base_ref!r} is not resolvable in the repository; "
                f"fetch the default branch before changed-path enforcement"
            )
        diff = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in diff.stdout.splitlines():
            if line:
                paths.add(line.replace("\\", "/"))
    return sorted(paths)


def resolve_default_branch_ref(repo_root: Path, *, remote: str = "origin") -> str:
    """Resolve the default branch's remote ref for use as a trusted base.

    Fetches ``refs/heads/<default>`` from ``remote`` and returns the
    ``refs/remotes/<remote>/<default>`` reference name suitable for use as
    ``base_ref``. Raises :class:`PolicyError` if the default branch cannot be
    determined or fetched.
    """
    import subprocess

    # Determine the default branch via remote symbolic-ref.
    sym = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", f"refs/remotes/{remote}/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if sym.returncode != 0:
        # Fall back to querying the remote directly.
        sym = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if sym.returncode != 0:
            raise PolicyError(
                f"could not determine default branch for {remote!r}; "
                f"set AGENTIC_BASE_REF explicitly"
            )
    default_ref = sym.stdout.strip()
    # symbolic-ref returns something like refs/remotes/origin/main or refs/heads/main.
    if default_ref.startswith("refs/remotes/"):
        remote_ref = default_ref
    elif default_ref.startswith("refs/heads/"):
        branch = default_ref[len("refs/heads/") :]
        remote_ref = f"refs/remotes/{remote}/{branch}"
    else:
        raise PolicyError(f"unexpected default ref format: {default_ref!r}")
    # Fetch the ref to make it resolvable locally.
    fetch = subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "--depth=1", remote, remote_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch.returncode != 0:
        # The ref may already be present locally; verify resolvability.
        cat = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-t", remote_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if cat.returncode != 0:
            raise PolicyError(
                f"could not fetch or resolve default branch ref {remote_ref!r}: {fetch.stderr.strip()}"
            )
    return remote_ref


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(token|password|secret|api[_-]?key|bearer|authorization)\s*[:=]\s*\S+"
    ),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)


def redact_secrets(text: str) -> str:
    """Redact secret-shaped values from ``text``."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redacted_policy_report(policy: EffectivePolicy) -> dict[str, Any]:
    """Return a redacted, summary-safe effective-policy report."""
    report = policy.to_dict()

    # Layers may contain bundle/overlay metadata; redact any secret-shaped
    # strings recursively.
    def _redact(obj: Any) -> Any:
        """Recursively redact secret-shaped strings from report data."""
        if isinstance(obj, str):
            return redact_secrets(obj)
        if isinstance(obj, list):
            return [_redact(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _redact(v) for k, v in obj.items()}
        return obj

    return _redact(report)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def write_job_summary(policy: EffectivePolicy, summary_path: Path) -> None:
    """Append a concise effective-policy summary to a GitHub step summary."""
    lines = [
        "### Effective agentic policy",
        "",
        f"- Workflow: `{policy.workflow}`",
        f"- Model profile: `{policy.model_profile}` (`{policy.provider_model}`)",
        f"- Output contract: `{policy.output_contract}`",
        f"- Max tokens: `{policy.max_tokens}`",
        f"- Timeout: `{policy.timeout_seconds}s`",
        f"- Retries: `{policy.max_retries}`",
        f"- Publication allowed: `{policy.publication_allowed}`",
        f"- Effective policy SHA-256: `{policy.sha256}`",
        "- Capabilities:",
    ]
    for axis in CAPABILITY_AXES:
        lines.append(f"  - {axis}: `{policy.capabilities.get(axis, DENY)}`")
    if policy.rejected_conflicts:
        lines.append("- Rejected conflicts:")
        for conflict in policy.rejected_conflicts:
            lines.append(f"  - {conflict}")
    existing = ""
    if summary_path.exists():
        existing = summary_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    summary_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    """Resolve policy from CLI inputs, emit JSON, and return an exit status."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Resolve and report effective agentic policy."
    )
    parser.add_argument(
        "--workflow", required=True, choices=sorted(BUILTIN_SAFETY_POLICY)
    )
    parser.add_argument("--model-profile", required=True)
    parser.add_argument(
        "--resolved-config",
        default=None,
        help="Path to resolved bundle JSON; its 'bundle_policy' is applied as layer 3.",
    )
    parser.add_argument(
        "--agent-capabilities-json",
        default=None,
        help="JSON object of agent-requested capabilities to intersect (layer 3).",
    )
    parser.add_argument(
        "--overlay", default=None, help="Path to a consumer overlay JSON (layer 4)."
    )
    parser.add_argument(
        "--mode",
        default="publish",
        choices=["publish", "dry-run", "validate-only"],
        help="Typed invocation mode (layer 5); dry-run/validate-only disable publication.",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=None,
        help="Invocation max_comments override (stricter only, layer 5).",
    )
    parser.add_argument("--result", default=None)
    parser.add_argument("--github-step-summary", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    bundle_policy: dict[str, Any] | None = None
    agent_caps: dict[str, str] | None = None
    overlay: dict[str, Any] | None = None
    invocation: dict[str, Any] = {}
    try:
        if args.resolved_config:
            data = json.loads(Path(args.resolved_config).read_text(encoding="utf-8"))
            bundle_policy = data.get("bundle_policy") or None
            if not bundle_policy:
                # Derive a minimal bundle policy from limits so the runner's
                # max_comments ceiling is enforced.
                limits = data.get("limits") or {}
                if limits:
                    bundle_policy = {"limits": limits}
        if args.agent_capabilities_json:
            agent_caps = json.loads(args.agent_capabilities_json)
            if not isinstance(agent_caps, dict):
                raise PolicyError("--agent-capabilities-json must be a JSON object")
        if args.overlay:
            overlay = json.loads(Path(args.overlay).read_text(encoding="utf-8"))
        if args.mode == "dry-run":
            invocation["dry_run"] = True
        elif args.mode == "validate-only":
            invocation["validate_only"] = True
        if args.max_comments is not None:
            invocation["max_comments"] = args.max_comments
        policy = merge_policy(
            workflow=args.workflow,
            model_profile=args.model_profile,
            agent_capabilities=agent_caps,
            bundle_policy=bundle_policy,
            overlay=overlay,
            invocation_inputs=invocation,
        )
    except PolicyError as error:
        print(f"::error::Policy resolution failed: {error}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f"::error::Policy input error: {error}", file=sys.stderr)
        return 1
    payload = policy.to_json()
    print(payload)
    if args.result:
        Path(args.result).write_text(payload + "\n", encoding="utf-8")
    if args.github_step_summary:
        write_job_summary(policy, Path(args.github_step_summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
