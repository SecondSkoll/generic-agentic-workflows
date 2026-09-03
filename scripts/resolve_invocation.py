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
from dataclasses import dataclass
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
        "release-project-review",
        "pr-changelog-update",
    }
)

#: Configuration sources accepted by the resolver. ``local`` resolves from
#: the trusted checkout of the calling repository. ``default`` and other
#: aliases are remote sources: they are validated for shape here and fetched by
#: ``scripts/agentic_configuration.py``. The remote alias set is the
#: single source of truth in ``agentic_configuration.REMOTE_SOURCE_ALIASES``;
#: this frozenset mirrors it so the invocation resolver can run before the
#: configuration module is imported. The two are kept in sync by tests.
try:
    import importlib.util as _importlib_util

    _cfg_path = Path(__file__).resolve().parent / "agentic_configuration.py"
    _spec = _importlib_util.spec_from_file_location("_agentic_cfg_mirror", _cfg_path)
    if _spec and _spec.loader:
        _mirror = _importlib_util.module_from_spec(_spec)
        # ``dataclasses`` resolves postponed annotations through
        # ``sys.modules`` while the configuration module is executing.
        sys.modules[_spec.name] = _mirror
        _spec.loader.exec_module(_mirror)
        _remote_aliases = frozenset(_mirror.REMOTE_SOURCE_ALIASES.keys())
    else:  # pragma: no cover - defensive
        _remote_aliases = frozenset()
except Exception:  # pragma: no cover - mirror must never break invocation
    _remote_aliases = frozenset({"default", "central"})
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

#: Release-management focus values a release-project-review caller may select.
#: These describe release-readiness concerns only; arbitrary prompt text is
#: rejected. Profiles may narrow this set, but callers cannot introduce new
#: values.
ALLOWED_RELEASE_FOCUS: frozenset[str] = frozenset(
    {
        "release-notes",
        "rollout",
        "rollback",
        "acceptance",
        "dependencies",
        "owners",
        "risk",
        "operational-readiness",
        "general",
    }
)

#: Strict canonical GitHub ``owner/repo`` grammar. Rejects URLs, owner/repo ref
#: syntax (``owner/repo@ref`` or ``owner/repo:ref``), paths, and expressions.
#: Each segment allows alphanumerics, ``.``, ``-``, and ``_`` only.
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

#: Conservative release tag grammar. Resolved through the GitHub REST API and
#: never used as a Git ref directly, so this bound exists only to reject
#: injection-shaped values (URLs, refs, expressions, paths, whitespace).
RELEASE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: Profile identifier pattern. Conservative on purpose: lowercase ASCII letters,
#: digits, and hyphens, starting with an alphanumeric, length 1..63.
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Full 40-character Git commit SHA. Required for any remote source.
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

#: Positive integer target (PR, issue, or release ID).
TARGET_PATTERN = re.compile(r"^[1-9][0-9]*$")

#: Bounds for ``max_comments``.
MAX_COMMENTS_MIN = 0
MAX_COMMENTS_MAX = 20

#: Bounds for ``max_issues`` (issue-feedback batch mode).
MAX_ISSUES_MIN = 1
MAX_ISSUES_MAX = 100

#: Maximum length of a ``target_file`` POSIX path.
TARGET_FILE_MAX = 512

#: Maximum length of a ``review_request_string``.
REVIEW_REQUEST_STRING_MAX = 128

#: Default substring that authorizes an additional review after prior feedback.
DEFAULT_REVIEW_REQUEST_STRING = "AI REVIEW REQUESTED"

#: First path segments a ``target_file`` may never target: workflow automation
#: and agent/skill/configuration instructions are workflow-owned and must not be
#: the changelog update target.
TARGET_FILE_FORBIDDEN_FIRST_SEGMENTS: frozenset[str] = frozenset(
    {".github", ".opencode"}
)


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
    target_repository: str | None = None
    release_id: int | None = None
    release_tag: str | None = None
    target_file: str | None = None
    review_request_string: str | None = None

    def to_json(self) -> str:
        """Serialize the resolved invocation as deterministic JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serialisable normalized invocation fields."""
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
            "target_repository": self.target_repository,
            "release_id": self.release_id,
            "release_tag": self.release_tag,
            "target_file": self.target_file,
            "review_request_string": self.review_request_string,
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
    """Return a bounded integer when supplied, otherwise ``None``."""
    if value is None or value == "":
        return None
    return _coerce_int(value, name, minimum, maximum)


def _validate_profile(profile: Any) -> str:
    """Validate and normalize a configuration profile identifier."""
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
    """Validate and normalize an allowlisted configuration source."""
    if not isinstance(source, str):
        raise InvocationError("configuration_source must be a string")
    normalized = source.strip().lower()
    if normalized not in SUPPORTED_SOURCES:
        raise InvocationError(
            f"configuration_source must be one of {sorted(SUPPORTED_SOURCES)}; got {source!r}"
        )
    return normalized


def _validate_ref(ref: Any, source: str) -> str | None:
    """Validate a remote source's immutable commit SHA, when required."""
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
    """Validate and normalize an optional allowlisted review focus."""
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


def _validate_release_focus(focus: Any) -> str | None:
    """Validate and normalize an optional allowlisted release-management focus.

    Release focus is a distinct allowlist from PR-review focus: it must describe
    a release-readiness concern and never accept arbitrary prompt text.
    """
    if focus is None or focus == "":
        return None
    if not isinstance(focus, str):
        raise InvocationError("focus must be a string")
    normalized = focus.strip().lower()
    if normalized not in ALLOWED_RELEASE_FOCUS:
        raise InvocationError(
            f"focus must be one of {sorted(ALLOWED_RELEASE_FOCUS)}; got {focus!r}"
        )
    return normalized


def _validate_target_repository(repository: Any) -> str:
    """Validate a strict canonical ``owner/repo`` target repository.

    Rejects URLs (``://``), owner/repo ref syntax (``@`` or ``:``), paths,
    expressions, and any value that is not exactly two segments matching the
    conservative grammar. The value is never used as a Git ref directly.
    """
    if repository is None or repository == "":
        raise InvocationError("target_repository is required")
    if not isinstance(repository, str):
        raise InvocationError("target_repository must be a string")
    normalized = repository.strip()
    if "://" in normalized or "@" in normalized or normalized.startswith("/"):
        raise InvocationError(
            f"target_repository must be a canonical owner/repo, got a URL or path: {repository!r}"
        )
    # Reject owner/repo:ref and owner/repo@ref injection shapes that the grammar
    # would otherwise allow when the segments contain ':'.
    if not REPOSITORY_PATTERN.match(normalized):
        raise InvocationError(
            "target_repository must be a canonical 'owner/repo' string "
            f"(alphanumerics, '.', '-', '_' in each segment); got {repository!r}"
        )
    return normalized


def _validate_release_id(release_id: Any) -> int | None:
    """Validate an optional positive numeric GitHub release ID."""
    if release_id is None or release_id == "":
        return None
    if isinstance(release_id, int) and not isinstance(release_id, bool):
        candidate = release_id
    elif isinstance(release_id, str):
        text = release_id.strip()
        if not text:
            return None
        if not TARGET_PATTERN.match(text):
            raise InvocationError(
                f"release_id must be a positive integer, got {release_id!r}"
            )
        candidate = int(text)
    else:
        raise InvocationError(f"release_id must be a positive integer, got {release_id!r}")
    if candidate <= 0:
        raise InvocationError(f"release_id must be a positive integer, got {release_id!r}")
    return candidate


def _validate_release_tag(tag: Any) -> str | None:
    """Validate an optional conservative release tag selector.

    The tag is resolved through the GitHub REST API and never used as a Git
    ref directly, so this bound exists only to reject injection-shaped values
    such as URLs, refs, expressions, paths, and whitespace.
    """
    if tag is None or tag == "":
        return None
    if not isinstance(tag, str):
        raise InvocationError("release_tag must be a string")
    normalized = tag.strip()
    if "://" in normalized or normalized.startswith("/") or "@" in normalized:
        raise InvocationError(
            f"release_tag must not be a URL, ref, or path: {tag!r}"
        )
    if ".." in normalized:
        raise InvocationError(
            f"release_tag must not contain '..' (Git ref range): {tag!r}"
        )
    if not RELEASE_TAG_PATTERN.match(normalized):
        raise InvocationError(
            "release_tag must match [A-Za-z0-9._-]{1,128}; got " f"{tag!r}"
        )
    return normalized


def _validate_target(target: Any) -> int | None:
    """Validate an optional positive issue or pull-request number."""
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
    """Validate an optional request label using the profile identifier grammar."""
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


def _validate_review_request_string(value: Any) -> str | None:
    """Validate an optional re-review request substring.

    The value is compared as inert data against comment bodies; it is never
    executed or interpolated into prompts. Rejects empty-after-strip values,
    control characters, and lengths beyond
    :data:`REVIEW_REQUEST_STRING_MAX` so log/argument injection shapes fail
    early.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvocationError("review_request_string must be a string")
    normalized = value.strip()
    if not normalized:
        raise InvocationError("review_request_string must not be empty")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in normalized):
        raise InvocationError(
            f"review_request_string must not contain control characters: {value!r}"
        )
    if len(normalized) > REVIEW_REQUEST_STRING_MAX:
        raise InvocationError(
            "review_request_string must be at most "
            f"{REVIEW_REQUEST_STRING_MAX} characters: {value!r}"
        )
    return normalized


def _validate_target_file(target_file: Any) -> str | None:
    """Validate an optional relative POSIX target file path.

    Rejects empty, absolute, backslash, control characters, ``.``/``..``/empty
    segments, trailing slashes, paths whose first segment is ``.github`` or
    ``.opencode`` (workflow/agent-owned), and paths longer than
    :data:`TARGET_FILE_MAX`. Returns a canonical POSIX-style relative path.
    """
    if target_file is None or target_file == "":
        return None
    if not isinstance(target_file, str):
        raise InvocationError("target_file must be a string")
    candidate = target_file.strip()
    if not candidate:
        raise InvocationError("target_file must not be empty")
    if "\\" in candidate:
        raise InvocationError(
            f"target_file must not contain backslashes: {target_file!r}"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        raise InvocationError(
            f"target_file must not contain control characters: {target_file!r}"
        )
    if candidate.startswith("/"):
        raise InvocationError(
            f"target_file must be a relative path, got absolute: {target_file!r}"
        )
    if candidate.endswith("/"):
        raise InvocationError(f"target_file must not end with '/': {target_file!r}")
    parts = candidate.split("/")
    if any(part in {"", "."} for part in parts):
        raise InvocationError(
            f"target_file must not contain empty or '.' segments: {target_file!r}"
        )
    if any(part == ".." for part in parts):
        raise InvocationError(
            f"target_file must not contain '..' segments: {target_file!r}"
        )
    if parts[0] in TARGET_FILE_FORBIDDEN_FIRST_SEGMENTS:
        raise InvocationError(
            "target_file must not target workflow or agent-owned paths "
            f"(.github/.opencode): {target_file!r}"
        )
    if len(candidate.encode("utf-8")) > TARGET_FILE_MAX:
        raise InvocationError(
            f"target_file must be at most {TARGET_FILE_MAX} bytes: {target_file!r}"
        )
    return candidate


def _validate_workflow(workflow: Any) -> str:
    """Validate and normalize a supported workflow identifier."""
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
    target_repository: str | None = None,
    release_id: int | str | None = None,
    release_tag: str | None = None,
    target_file: str | None = None,
    review_request_string: str | None = None,
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
    resolved_dry_run = _coerce_bool(dry_run, "dry_run")
    resolved_validate_only = _coerce_bool(validate_only, "validate_only")

    # Workflow-specific input validation. The release workflow has its own
    # release-management focus allowlist and release selector; the other
    # workflows use the PR/issue focus allowlist and target_number.
    if resolved_workflow == "release-project-review":
        resolved_focus = _validate_release_focus(focus)
        resolved_max_comments = _optional_int(
            max_comments, "max_comments", MAX_COMMENTS_MIN, MAX_COMMENTS_MAX
        )
        resolved_target_repository = _validate_target_repository(target_repository)
        resolved_release_id = _validate_release_id(release_id)
        resolved_release_tag = _validate_release_tag(release_tag)
        if (resolved_release_id is None) == (resolved_release_tag is None):
            raise InvocationError(
                "release-project-review requires exactly one of release_id or release_tag"
            )
        # Release review does not use PR/issue target_number, batch counts,
        # request_label, or max_issues; reject them to keep the surface narrow.
        if target_number is not None and target_number != "":
            raise InvocationError("target_number is not supported for release-project-review")
        if max_issues is not None and max_issues != "":
            raise InvocationError("max_issues is not supported for release-project-review")
        if request_label is not None and request_label != "":
            raise InvocationError("request_label is not supported for release-project-review")
        if target_file is not None and target_file != "":
            raise InvocationError("target_file is not supported for release-project-review")
        if review_request_string is not None and review_request_string != "":
            raise InvocationError(
                "review_request_string is not supported for release-project-review"
            )
        resolved_target = None
        resolved_label = None
        resolved_max_issues = None
        resolved_target_file = None
        resolved_review_request_string = None
    else:
        resolved_focus = _validate_focus(focus)
        resolved_max_comments = _optional_int(
            max_comments, "max_comments", MAX_COMMENTS_MIN, MAX_COMMENTS_MAX
        )
        resolved_max_issues = _optional_int(
            max_issues, "max_issues", MAX_ISSUES_MIN, MAX_ISSUES_MAX
        )
        resolved_target = _validate_target(target_number)
        resolved_label = _validate_request_label(request_label)
        resolved_target_file = _validate_target_file(target_file)
        resolved_review_request_string = _validate_review_request_string(
            review_request_string
        )
        resolved_target_repository = None
        resolved_release_id = None
        resolved_release_tag = None
        # Release selectors belong only to the release workflow.
        if release_id is not None and release_id != "":
            raise InvocationError("release_id is only supported for release-project-review")
        if release_tag is not None and release_tag != "":
            raise InvocationError("release_tag is only supported for release-project-review")
        if target_repository is not None and target_repository != "":
            raise InvocationError(
                "target_repository is only supported for release-project-review"
            )

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
        if resolved_target_file is not None:
            raise InvocationError("target_file is not supported for pr-documentation-review")
    elif resolved_workflow == "issue-feedback":
        if resolved_max_comments is not None:
            raise InvocationError("max_comments is not supported for issue feedback")
        if resolved_target_file is not None:
            raise InvocationError("target_file is not supported for issue-feedback")
        if resolved_review_request_string is not None:
            raise InvocationError(
                "review_request_string is only supported for pr-documentation-review"
            )
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
        if resolved_target_file is not None:
            raise InvocationError("target_file is not supported for issue implementation")
        if resolved_review_request_string is not None:
            raise InvocationError(
                "review_request_string is only supported for pr-documentation-review"
            )
    elif resolved_workflow == "pr-changelog-update":
        if resolved_target is None:
            raise InvocationError(
                "pr-changelog-update requires a pull-request target number"
            )
        if resolved_label is None:
            raise InvocationError(
                "pr-changelog-update requires a request_label"
            )
        if resolved_target_file is None:
            raise InvocationError(
                "pr-changelog-update requires a target_file"
            )
        if resolved_focus is not None:
            raise InvocationError("focus is not supported for pr-changelog-update")
        if resolved_max_comments is not None:
            raise InvocationError(
                "max_comments is not supported for pr-changelog-update"
            )
        if resolved_max_issues is not None:
            raise InvocationError(
                "max_issues is not supported for pr-changelog-update"
            )
        if resolved_review_request_string is not None:
            raise InvocationError(
                "review_request_string is only supported for pr-documentation-review"
            )

    # Remote sources are validated for shape only; configuration resolution
    # fetches them by their allowlisted alias.
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
        target_repository=resolved_target_repository,
        release_id=resolved_release_id,
        release_tag=resolved_release_tag,
        target_file=resolved_target_file,
        review_request_string=resolved_review_request_string,
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
        target_repository=env.get("AGENTIC_TARGET_REPOSITORY") or None,
        release_id=env.get("AGENTIC_RELEASE_ID") or None,
        release_tag=env.get("AGENTIC_RELEASE_TAG") or None,
        target_file=env.get("AGENTIC_TARGET_FILE") or None,
        review_request_string=env.get("AGENTIC_REVIEW_REQUEST_STRING") or None,
    )


def _require_env(env: dict[str, str], key: str) -> str:
    """Return a required environment value or raise :class:`InvocationError`."""
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
        f"target_repository={resolved.target_repository or ''}",
        f"release_id={resolved.release_id if resolved.release_id is not None else ''}",
        f"release_tag={resolved.release_tag or ''}",
        f"target_file={resolved.target_file or ''}",
        f"review_request_string={resolved.review_request_string or ''}",
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
    if resolved.target_repository:
        lines.append(f"- Target repository: `{resolved.target_repository}`")
    if resolved.release_id is not None:
        lines.append(f"- Release ID: `{resolved.release_id}`")
    if resolved.release_tag:
        lines.append(f"- Release tag: `{resolved.release_tag}`")
    if resolved.target_file:
        lines.append(f"- Target file: `{resolved.target_file}`")
    if resolved.review_request_string:
        lines.append(f"- Review request string: `{resolved.review_request_string}`")
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
    """Build the command-line parser for invocation resolution."""
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
    parser.add_argument("--target-repository", default=None)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--target-file", default=None)
    parser.add_argument("--review-request-string", default=None)
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
    """Resolve CLI inputs, emit normalized metadata, and return an exit status."""
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
            target_repository=args.target_repository,
            release_id=args.release_id,
            release_tag=args.release_tag,
            target_file=args.target_file,
            review_request_string=args.review_request_string,
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
