#!/usr/bin/env python3
"""Normalize and validate reusable-workflow invocation inputs.

This module is intentionally dependency-free so it can run in a GitHub Actions
runner. It is shared by the review, issue-feedback, and issue-implementation
workflows. It normalizes direct-trigger values and ``workflow_call`` inputs
into a single set of environment variables, validates them before any checkout
or model invocation, and writes a JSON result that downstream steps consume.

The resolver only validates invocation metadata. It does not fetch remote
configuration bundles (Plan 2), render prompts (Plan 3), or merge policy
layers (Plan 4). It rejects every input that would let an untrusted caller
select executable or instruction-bearing content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Iterable


# ---------------------------------------------------------------------------
# Allowlists and bounds
# ---------------------------------------------------------------------------

#: Workflows that may be invoked through this resolver.
SUPPORTED_WORKFLOWS: frozenset[str] = frozenset(
    {
        "pr-documentation-review",
        "issue-feedback",
        "issue-implementation",
    }
)

#: Configuration sources accepted by the resolver. ``local`` is resolved
#: directly; remote aliases are validated for shape here and fetched by
#: ``scripts/agentic_configuration.py`` (Plan 2). The remote alias set is the
#: single source of truth in ``agentic_configuration.REMOTE_SOURCE_ALIASES``;
#: this frozenset mirrors it so the invocation resolver can run before the
#: configuration module is imported. The two are kept in sync by tests.
try:
    import importlib.util as _importlib_util

    _cfg_path = Path(__file__).resolve().parent / "agentic_configuration.py"
    _spec = _importlib_util.spec_from_file_location("_agentic_cfg_mirror", _cfg_path)
    if _spec and _spec.loader:
        _mirror = _importlib_util.module_from_spec(_spec)
        _spec.loader.exec_module(_mirror)
        _remote_aliases = frozenset(_mirror.REMOTE_SOURCE_ALIASES.keys())
    else:  # pragma: no cover - defensive
        _remote_aliases = frozenset()
except Exception:  # pragma: no cover - mirror must never break invocation
    _remote_aliases = frozenset({"central"})
SUPPORTED_SOURCES: frozenset[str] = frozenset({"local"}) | _remote_aliases

#: Review focus values a caller may select. Profiles may narrow this set, but
#: callers cannot introduce new focus values.
ALLOWED_FOCUS: frozenset[str] = frozenset(
    {
        "documentation",
        "security",
        "tests",
        "general",
    }
)

#: Profile identifier pattern. Conservative on purpose: lowercase ASCII letters,
#: digits, and hyphens, starting with an alphanumeric, length 1..63.
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Full 40-character Git commit SHA. Required for any remote source.
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

#: Positive integer target (PR or issue number).
TARGET_PATTERN = re.compile(r"^[1-9][0-9]*$")

#: Bounds for ``max_comments``.
MAX_COMMENTS_MIN = 0
MAX_COMMENTS_MAX = 20

#: Bounds for ``max_issues`` (issue-feedback batch mode).
MAX_ISSUES_MIN = 1
MAX_ISSUES_MAX = 100


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedInvocation:
    """Immutable, validated invocation metadata for a workflow run."""

    workflow: str
    configuration_source: str
    configuration_ref: str | None
    configuration_profile: str
    focus: str | None
    max_comments: int | None
    max_issues: int | None
    dry_run: bool
    validate_only: bool
    target_number: int | None
    request_label: str | None
    legacy: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize the resolved invocation as deterministic JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "configuration_source": self.configuration_source,
            "configuration_ref": self.configuration_ref,
            "configuration_profile": self.configuration_profile,
            "focus": self.focus,
            "max_comments": self.max_comments,
            "max_issues": self.max_issues,
            "dry_run": self.dry_run,
            "validate_only": self.validate_only,
            "target_number": self.target_number,
            "request_label": self.request_label,
            "legacy": dict(self.legacy),
        }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class InvocationError(ValueError):
    """Raised when an invocation input is invalid."""


def _coerce_bool(value: Any, name: str) -> bool:
    """Coerce a string/bool/None into a strict boolean.

    GitHub Actions ``workflow_call`` boolean inputs arrive as the strings
    ``"true"``/``"false"`` when read from the environment. Direct Python
    callers may pass real booleans.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
    raise InvocationError(f"{name} must be a boolean, got {value!r}")


def _coerce_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    """Coerce a string/int into a bounded integer."""
    if value is None or value == "":
        raise InvocationError(f"{name} is required")
    if isinstance(value, bool):
        raise InvocationError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise InvocationError(f"{name} is required")
        try:
            candidate = int(text)
        except ValueError as error:
            raise InvocationError(
                f"{name} must be an integer, got {value!r}"
            ) from error
    else:
        raise InvocationError(f"{name} must be an integer, got {value!r}")
    if candidate < minimum or candidate > maximum:
        raise InvocationError(
            f"{name} must be between {minimum} and {maximum}, got {candidate}"
        )
    return candidate


def _optional_int(value: Any, name: str, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    return _coerce_int(value, name, minimum, maximum)


def _validate_profile(profile: Any) -> str:
    if not isinstance(profile, str):
        raise InvocationError("configuration_profile must be a string")
    normalized = profile.strip()
    if not PROFILE_PATTERN.match(normalized):
        raise InvocationError(
            "configuration_profile must match [a-z0-9][a-z0-9-]{0,62}; "
            f"got {profile!r}"
        )
    return normalized


def _validate_source(source: Any) -> str:
    if not isinstance(source, str):
        raise InvocationError("configuration_source must be a string")
    normalized = source.strip().lower()
    if normalized not in SUPPORTED_SOURCES:
        raise InvocationError(
            f"configuration_source must be one of {sorted(SUPPORTED_SOURCES)}; got {source!r}"
        )
    return normalized


def _validate_ref(ref: Any, source: str) -> str | None:
    if ref is None or ref == "":
        if source == "local":
            return None
        raise InvocationError("configuration_ref is required for remote sources")
    if not isinstance(ref, str):
        raise InvocationError("configuration_ref must be a string")
    normalized = ref.strip().lower()
    if not SHA_PATTERN.match(normalized):
        raise InvocationError(
            "configuration_ref must be a full 40-character commit SHA when set; "
            f"got {ref!r}"
        )
    return normalized


def _validate_focus(focus: Any) -> str | None:
    if focus is None or focus == "":
        return None
    if not isinstance(focus, str):
        raise InvocationError("focus must be a string")
    normalized = focus.strip().lower()
    if normalized not in ALLOWED_FOCUS:
        raise InvocationError(
            f"focus must be one of {sorted(ALLOWED_FOCUS)}; got {focus!r}"
        )
    return normalized


def _validate_target(target: Any) -> int | None:
    if target is None or target == "":
        return None
    if isinstance(target, int) and not isinstance(target, bool):
        candidate = target
    elif isinstance(target, str):
        text = target.strip()
        if not text:
            return None
        if not TARGET_PATTERN.match(text):
            raise InvocationError(f"target must be a positive integer, got {target!r}")
        candidate = int(text)
    else:
        raise InvocationError(f"target must be a positive integer, got {target!r}")
    if candidate <= 0:
        raise InvocationError(f"target must be a positive integer, got {target!r}")
    return candidate


def _validate_request_label(label: Any) -> str | None:
    if label is None or label == "":
        return None
    if not isinstance(label, str):
        raise InvocationError("request_label must be a string")
    normalized = label.strip()
    if not PROFILE_PATTERN.match(normalized):
        raise InvocationError(
            f"request_label must match [a-z0-9][a-z0-9-]{{0,62}}; got {label!r}"
        )
    return normalized


def _validate_workflow(workflow: Any) -> str:
    if not isinstance(workflow, str):
        raise InvocationError("workflow must be a string")
    normalized = workflow.strip().lower()
    if normalized not in SUPPORTED_WORKFLOWS:
        raise InvocationError(
            f"workflow must be one of {sorted(SUPPORTED_WORKFLOWS)}; got {workflow!r}"
        )
    return normalized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_invocation(
    *,
    workflow: str,
    configuration_source: str = "local",
    configuration_ref: str | None = None,
    configuration_profile: str,
    focus: str | None = None,
    max_comments: int | str | None = None,
    max_issues: int | str | None = None,
    dry_run: bool | str = False,
    validate_only: bool | str = False,
    target_number: int | str | None = None,
    request_label: str | None = None,
    legacy: dict[str, str] | None = None,
) -> ResolvedInvocation:
    """Validate and normalize all invocation inputs.

    Raises:
        InvocationError: If any input is invalid. The caller should surface
            the message to the workflow log and exit non-zero before any
            checkout or model invocation.
    """
    resolved_workflow = _validate_workflow(workflow)
    resolved_source = _validate_source(configuration_source)
    resolved_ref = _validate_ref(configuration_ref, resolved_source)
    resolved_profile = _validate_profile(configuration_profile)
    resolved_focus = _validate_focus(focus)
    resolved_max_comments = _optional_int(
        max_comments, "max_comments", MAX_COMMENTS_MIN, MAX_COMMENTS_MAX
    )
    resolved_max_issues = _optional_int(
        max_issues, "max_issues", MAX_ISSUES_MIN, MAX_ISSUES_MAX
    )
    resolved_dry_run = _coerce_bool(dry_run, "dry_run")
    resolved_validate_only = _coerce_bool(validate_only, "validate_only")
    resolved_target = _validate_target(target_number)
    resolved_label = _validate_request_label(request_label)

    if resolved_dry_run and resolved_validate_only:
        raise InvocationError("dry_run and validate_only are mutually exclusive")

    # Workflow-specific compatibility checks.
    if resolved_workflow == "pr-documentation-review":
        if resolved_target is None:
            raise InvocationError(
                "pr-documentation-review requires a pull-request target number"
            )
        if resolved_max_issues is not None:
            raise InvocationError("max_issues is not supported for PR review")
    elif resolved_workflow == "issue-feedback":
        if resolved_max_comments is not None:
            raise InvocationError("max_comments is not supported for issue feedback")
    elif resolved_workflow == "issue-implementation":
        if resolved_target is None:
            raise InvocationError(
                "issue-implementation requires an issue target number"
            )
        if resolved_focus is not None:
            raise InvocationError("focus is not supported for issue implementation")
        if resolved_max_comments is not None:
            raise InvocationError(
                "max_comments is not supported for issue implementation"
            )
        if resolved_max_issues is not None:
            raise InvocationError(
                "max_issues is not supported for issue implementation"
            )

    # Remote sources are validated for shape only; Plan 2 wires up fetching.
    if resolved_source != "local" and resolved_ref is None:
        raise InvocationError("configuration_ref is required for remote sources")

    return ResolvedInvocation(
        workflow=resolved_workflow,
        configuration_source=resolved_source,
        configuration_ref=resolved_ref,
        configuration_profile=resolved_profile,
        focus=resolved_focus,
        max_comments=resolved_max_comments,
        max_issues=resolved_max_issues,
        dry_run=resolved_dry_run,
        validate_only=resolved_validate_only,
        target_number=resolved_target,
        request_label=resolved_label,
        legacy=dict(legacy or {}),
    )


def resolve_from_env(env: dict[str, str]) -> ResolvedInvocation:
    """Resolve an invocation from the environment variables set by a workflow.

    The workflow ``Resolve invocation`` step exports normalized values into
    the environment before invoking this module. This keeps the workflow YAML
    thin and the validation logic testable.
    """
    return resolve_invocation(
        workflow=_require_env(env, "AGENTIC_WORKFLOW"),
        configuration_source=env.get("AGENTIC_CONFIGURATION_SOURCE", "local"),
        configuration_ref=env.get("AGENTIC_CONFIGURATION_REF") or None,
        configuration_profile=_require_env(env, "AGENTIC_CONFIGURATION_PROFILE"),
        focus=env.get("AGENTIC_FOCUS") or None,
        max_comments=env.get("AGENTIC_MAX_COMMENTS") or None,
        max_issues=env.get("AGENTIC_MAX_ISSUES") or None,
        dry_run=env.get("AGENTIC_DRY_RUN", "false"),
        validate_only=env.get("AGENTIC_VALIDATE_ONLY", "false"),
        target_number=env.get("AGENTIC_TARGET_NUMBER") or None,
        request_label=env.get("AGENTIC_REQUEST_LABEL") or None,
        legacy={
            key: value
            for key, value in env.items()
            if key.startswith("AGENTIC_LEGACY_")
        },
    )


def _require_env(env: dict[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise InvocationError(f"{key} environment variable is required")
    return value


def write_github_outputs(resolved: ResolvedInvocation, output_path: Path) -> None:
    """Write the resolved invocation as ``$GITHUB_OUTPUT`` lines.

    Downstream steps read these values through ``steps.resolve.outputs.*``.
    """
    lines = [
        f"workflow={resolved.workflow}",
        f"configuration_source={resolved.configuration_source}",
        f"configuration_ref={resolved.configuration_ref or ''}",
        f"configuration_profile={resolved.configuration_profile}",
        f"focus={resolved.focus or ''}",
        f"max_comments={resolved.max_comments if resolved.max_comments is not None else ''}",
        f"max_issues={resolved.max_issues if resolved.max_issues is not None else ''}",
        f"dry_run={'true' if resolved.dry_run else 'false'}",
        f"validate_only={'true' if resolved.validate_only else 'false'}",
        f"target_number={resolved.target_number if resolved.target_number is not None else ''}",
        f"request_label={resolved.request_label or ''}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_job_summary(resolved: ResolvedInvocation, summary_path: Path) -> None:
    """Append a concise, redacted job summary for maintainers."""
    lines = [
        "### Resolved agentic invocation",
        "",
        f"- Workflow: `{resolved.workflow}`",
        f"- Configuration source: `{resolved.configuration_source}`",
        f"- Configuration profile: `{resolved.configuration_profile}`",
    ]
    if resolved.configuration_ref:
        lines.append(f"- Configuration ref: `{resolved.configuration_ref}`")
    if resolved.focus:
        lines.append(f"- Focus: `{resolved.focus}`")
    if resolved.max_comments is not None:
        lines.append(f"- Max comments: `{resolved.max_comments}`")
    if resolved.max_issues is not None:
        lines.append(f"- Max issues: `{resolved.max_issues}`")
    if resolved.target_number is not None:
        lines.append(f"- Target number: `{resolved.target_number}`")
    if resolved.request_label:
        lines.append(f"- Request label: `{resolved.request_label}`")
    mode = (
        "validate-only"
        if resolved.validate_only
        else ("dry-run" if resolved.dry_run else "publish")
    )
    lines.append(f"- Mode: `{mode}`")
    existing = ""
    if summary_path.exists():
        existing = summary_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    summary_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize and validate reusable-workflow invocation inputs."
    )
    parser.add_argument(
        "--workflow", required=True, choices=sorted(SUPPORTED_WORKFLOWS)
    )
    parser.add_argument("--configuration-source", default="local")
    parser.add_argument("--configuration-ref", default=None)
    parser.add_argument("--configuration-profile", required=True)
    parser.add_argument("--focus", default=None)
    parser.add_argument("--max-comments", default=None)
    parser.add_argument("--max-issues", default=None)
    parser.add_argument("--dry-run", default="false")
    parser.add_argument("--validate-only", default="false")
    parser.add_argument("--target-number", default=None)
    parser.add_argument("--request-label", default=None)
    parser.add_argument(
        "--github-output",
        default=None,
        help="Path to write $GITHUB_OUTPUT lines to.",
    )
    parser.add_argument(
        "--github-step-summary",
        default=None,
        help="Path to append a job summary to.",
    )
    parser.add_argument(
        "--result",
        default=None,
        help="Path to write the resolved invocation JSON to.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        resolved = resolve_invocation(
            workflow=args.workflow,
            configuration_source=args.configuration_source,
            configuration_ref=args.configuration_ref,
            configuration_profile=args.configuration_profile,
            focus=args.focus,
            max_comments=args.max_comments,
            max_issues=args.max_issues,
            dry_run=args.dry_run,
            validate_only=args.validate_only,
            target_number=args.target_number,
            request_label=args.request_label,
        )
    except InvocationError as error:
        print(f"::error::Invocation validation failed: {error}", file=sys.stderr)
        return 1

    payload = resolved.to_json()
    print(payload)

    if args.github_output:
        write_github_outputs(resolved, Path(args.github_output))
    if args.github_step_summary:
        write_job_summary(resolved, Path(args.github_step_summary))
    if args.result:
        Path(args.result).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
