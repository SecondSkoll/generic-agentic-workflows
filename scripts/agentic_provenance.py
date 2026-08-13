#!/usr/bin/env python3
"""Provenance, idempotency, and operations support for agentic workflows (Plan 5).

Dependency-free. Builds a redacted, reproducible provenance record for every
invocation; emits a concise job summary; provides a deterministic
configuration digest; and produces a v2 feedback marker that carries the
configuration digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Mapping


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProvenanceError(ValueError):
    """Raised when provenance cannot be built or a marker is malformed."""


# ---------------------------------------------------------------------------
# Deterministic configuration digest
# ---------------------------------------------------------------------------


def configuration_digest(parts: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 digest over stable configuration fields.

    The digest is the basis for v2 idempotency: a configuration change should
    trigger new feedback even for the same PR head or issue request.
    """
    stable: dict[str, Any] = {
        "workflow": parts.get("workflow"),
        "configuration_source": parts.get("configuration_source"),
        "configuration_ref": parts.get("configuration_ref"),
        "profile": parts.get("profile"),
        "manifest_sha256": parts.get("manifest_sha256"),
        "prompt_template_sha256": parts.get("prompt_template_sha256"),
        "output_contract": parts.get("output_contract"),
        "model_profile": parts.get("model_profile"),
        "effective_policy_sha256": parts.get("effective_policy_sha256"),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Provenance record
# ---------------------------------------------------------------------------

#: Allowed values for the ``result`` field.
ALLOWED_RESULTS: frozenset[str] = frozenset(
    {"validated", "generated", "published", "skipped", "failed"}
)

#: Allowed values for the ``mode`` field.
ALLOWED_MODES: frozenset[str] = frozenset({"publish", "dry-run", "validate-only"})

#: Marker schema version.
MARKER_SCHEMA_V2 = "v2"


@dataclass(frozen=True)
class ProvenanceRecord:
    """Complete Plan 5 provenance record."""

    workflow_version: str
    workflow_name: str
    caller_repository: str
    target_kind: str | None
    target_number: int | None
    target_head_sha: str | None
    bundle_source_alias: str | None
    bundle_repository: str | None
    bundle_resolved_sha: str | None
    bundle_profile: str | None
    bundle_manifest_sha256: str | None
    prompt_template_sha256: str | None
    output_contract: str | None
    model_profile: str | None
    effective_policy_sha256: str | None
    mode: str
    result: str
    configuration_digest: str | None
    error: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the redacted record as a JSON-serialisable mapping."""
        return {
            "workflow_version": self.workflow_version,
            "workflow_name": self.workflow_name,
            "caller_repository": self.caller_repository,
            "target": {
                "kind": self.target_kind,
                "number": self.target_number,
                "head_sha": self.target_head_sha,
            },
            "bundle": {
                "source_alias": self.bundle_source_alias,
                "repository": self.bundle_repository,
                "resolved_sha": self.bundle_resolved_sha,
                "profile": self.bundle_profile,
                "manifest_sha256": self.bundle_manifest_sha256,
            },
            "prompt_template_sha256": self.prompt_template_sha256,
            "output_contract": self.output_contract,
            "model_profile": self.model_profile,
            "effective_policy_sha256": self.effective_policy_sha256,
            "mode": self.mode,
            "result": self.result,
            "configuration_digest": self.configuration_digest,
            **({"error": self.error} if self.error else {}),
            **({"error_message": self.error_message} if self.error_message else {}),
        }

    def to_json(self) -> str:
        """Serialize the record as deterministic, pretty JSON."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def build_provenance(
    *,
    workflow_version: str,
    workflow_name: str,
    caller_repository: str,
    target_kind: str | None,
    target_number: int | None,
    target_head_sha: str | None,
    bundle: Mapping[str, Any] | None,
    prompt_template_sha256: str | None,
    output_contract: str | None,
    model_profile: str | None,
    effective_policy_sha256: str | None,
    mode: str,
    result: str,
) -> ProvenanceRecord:
    """Build a complete, redacted provenance record.

    Never includes provider credentials, token values, full model prompts,
    complete issue text, unredacted diffs, or raw model responses.
    """
    if mode not in ALLOWED_MODES:
        raise ProvenanceError(
            f"mode must be one of {sorted(ALLOWED_MODES)}; got {mode!r}"
        )
    if result not in ALLOWED_RESULTS:
        raise ProvenanceError(
            f"result must be one of {sorted(ALLOWED_RESULTS)}; got {result!r}"
        )
    bundle = bundle or {}
    digest = configuration_digest(
        {
            "workflow": workflow_name,
            "configuration_source": bundle.get("source_alias"),
            "configuration_ref": bundle.get("resolved_sha"),
            "profile": bundle.get("profile"),
            "manifest_sha256": bundle.get("manifest_sha256"),
            "prompt_template_sha256": prompt_template_sha256,
            "output_contract": output_contract,
            "model_profile": model_profile,
            "effective_policy_sha256": effective_policy_sha256,
        }
    )
    return ProvenanceRecord(
        workflow_version=workflow_version,
        workflow_name=workflow_name,
        caller_repository=caller_repository,
        target_kind=target_kind,
        target_number=target_number,
        target_head_sha=target_head_sha,
        bundle_source_alias=bundle.get("source_alias"),
        bundle_repository=bundle.get("repository"),
        bundle_resolved_sha=bundle.get("resolved_sha"),
        bundle_profile=bundle.get("profile"),
        bundle_manifest_sha256=bundle.get("manifest_sha256"),
        prompt_template_sha256=prompt_template_sha256,
        output_contract=output_contract,
        model_profile=model_profile,
        effective_policy_sha256=effective_policy_sha256,
        mode=mode,
        result=result,
        configuration_digest=digest,
    )


def failure_record(
    *,
    workflow_version: str,
    workflow_name: str,
    caller_repository: str,
    mode: str,
    error: Exception,
    bundle: Mapping[str, Any] | None = None,
) -> ProvenanceRecord:
    """Build a minimal redacted attempted-resolution record for a failure.

    The error message is included verbatim because resolver/policy/contract
    errors are constructed from non-secret templates only.
    """
    if mode not in ALLOWED_MODES:
        mode = "publish"
    bundle = bundle or {}
    return ProvenanceRecord(
        workflow_version=workflow_version,
        workflow_name=workflow_name,
        caller_repository=caller_repository,
        target_kind=None,
        target_number=None,
        target_head_sha=None,
        bundle_source_alias=bundle.get("source_alias"),
        bundle_repository=bundle.get("repository"),
        bundle_resolved_sha=bundle.get("resolved_sha"),
        bundle_profile=bundle.get("profile"),
        bundle_manifest_sha256=bundle.get("manifest_sha256"),
        prompt_template_sha256=None,
        output_contract=None,
        model_profile=None,
        effective_policy_sha256=None,
        mode=mode,
        result="failed",
        configuration_digest=None,
        error=type(error).__name__,
        error_message=str(error),
    )


# ---------------------------------------------------------------------------
# Job summary
# ---------------------------------------------------------------------------


def job_summary(record: ProvenanceRecord) -> str:
    """Return a concise, redacted Markdown job summary."""
    lines = [
        "### Agentic workflow provenance",
        "",
        f"- Workflow: `{record.workflow_name}` (`{record.workflow_version}`)",
        f"- Caller repository: `{record.caller_repository}`",
        f"- Mode: `{record.mode}`",
        f"- Result: `{record.result}`",
    ]
    if record.bundle_repository:
        lines.append(f"- Bundle repository: `{record.bundle_repository}`")
    if record.bundle_resolved_sha:
        lines.append(f"- Resolved SHA: `{record.bundle_resolved_sha}`")
    if record.bundle_profile:
        lines.append(f"- Profile: `{record.bundle_profile}`")
    if record.output_contract:
        lines.append(f"- Output contract: `{record.output_contract}`")
    if record.model_profile:
        lines.append(f"- Model profile: `{record.model_profile}`")
    if record.configuration_digest:
        lines.append(f"- Configuration digest: `{record.configuration_digest}`")
    if record.error:
        lines.append(f"- Error: `{record.error}`")
    if record.target_number is not None:
        lines.append(
            f"- Target: `{record.target_kind or 'target'}#{record.target_number}`"
        )
    return "\n".join(lines) + "\n"


def write_job_summary(record: ProvenanceRecord, summary_path: Path) -> None:
    """Append a provenance summary to a GitHub step-summary file."""
    existing = ""
    if summary_path.exists():
        existing = summary_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    summary_path.write_text(existing + job_summary(record), encoding="utf-8")


# ---------------------------------------------------------------------------
# Feedback marker
# ---------------------------------------------------------------------------

#: v2 marker: ``<!-- agentic-workflow:<kind>:v2:<config_digest>[:<head_sha>] -->``
_V2_MARKER = re.compile(
    r"<!-- agentic-workflow:(?P<kind>[a-z0-9-]+):v2:(?P<digest>[0-9a-f]{64})(?::(?P<head_sha>[0-9a-f]{7,40}))? -->"
)


def feedback_marker(
    feedback_kind: str,
    *,
    head_sha: str | None = None,
    config_digest: str,
) -> str:
    """Return the idempotency marker.

    The marker carries the configuration digest so a profile update triggers
    new feedback for the same PR head or issue.
    """
    if not isinstance(config_digest, str) or not re.match(
        r"^[0-9a-f]{64}$", config_digest
    ):
        raise ProvenanceError("feedback marker requires a 64-char configuration digest")
    suffix = f":{head_sha}" if head_sha else ""
    return f"<!-- agentic-workflow:{feedback_kind}:v2:{config_digest}{suffix} -->"


def parse_marker(text: str, *, feedback_kind: str) -> dict[str, Any] | None:
    """Parse a feedback marker from ``text``.

    Returns marker metadata for a matching marker of the same feedback kind,
    or ``None``.
    """
    if not isinstance(text, str):
        return None
    v2 = _V2_MARKER.search(text)
    if v2 and v2.group("kind") == feedback_kind:
        return {
            "version": MARKER_SCHEMA_V2,
            "head_sha": v2.group("head_sha"),
            "config_digest": v2.group("digest"),
        }
    return None


def matches_current_config(
    text: str,
    *,
    feedback_kind: str,
    head_sha: str | None,
    config_digest: str,
) -> bool:
    """Return True when ``text`` contains a v2 marker for the same config.

    Used to suppress duplicate feedback.
    """
    parsed = parse_marker(text, feedback_kind=feedback_kind)
    if not parsed or parsed["version"] != MARKER_SCHEMA_V2:
        return False
    if head_sha and parsed["head_sha"] and parsed["head_sha"] != head_sha:
        return False
    return parsed["config_digest"] == config_digest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    """Build provenance from CLI inputs, write requested files, and exit."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Build a provenance record for an agentic run."
    )
    parser.add_argument("--result", default=None)
    parser.add_argument("--github-step-summary", default=None)
    parser.add_argument("--workflow-version", default="dev")
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--caller-repository", required=True)
    parser.add_argument("--mode", default="publish", choices=sorted(ALLOWED_MODES))
    parser.add_argument(
        "--result-status", default="validated", choices=sorted(ALLOWED_RESULTS)
    )
    parser.add_argument(
        "--bundle-json", default=None, help="Path to resolved bundle JSON"
    )
    parser.add_argument("--output-contract", default=None)
    parser.add_argument("--model-profile", default=None)
    parser.add_argument("--effective-policy-sha256", default=None)
    parser.add_argument("--prompt-template-sha256", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    bundle: dict[str, Any] = {}
    if args.bundle_json:
        bundle = json.loads(Path(args.bundle_json).read_text(encoding="utf-8"))

    record = build_provenance(
        workflow_version=args.workflow_version,
        workflow_name=args.workflow_name,
        caller_repository=args.caller_repository,
        target_kind=None,
        target_number=None,
        target_head_sha=None,
        bundle=bundle,
        prompt_template_sha256=args.prompt_template_sha256,
        output_contract=args.output_contract,
        model_profile=args.model_profile,
        effective_policy_sha256=args.effective_policy_sha256,
        mode=args.mode,
        result=args.result_status,
    )
    payload = record.to_json()
    print(payload)
    if args.result:
        Path(args.result).write_text(payload + "\n", encoding="utf-8")
    if args.github_step_summary:
        write_job_summary(record, Path(args.github_step_summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
