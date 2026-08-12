#!/usr/bin/env python3
"""Configuration bundle resolution for agentic workflows (Plan 2).

This module is dependency-free so it can run on a GitHub Actions runner. It
parses versioned configuration bundles, validates their manifest, content
paths, and SHA-256 hashes, and resolves a bundle from either the trusted local
checkout or an organization-approved remote GitHub repository pinned to a full
commit SHA.

The resolver never accepts caller-controlled paths into executable
configuration: it accepts only a bundle root/profile (local) or an allowlisted
source alias plus a pinned SHA (remote). A failure at any step fails closed;
there is no fallback to a different profile, branch, or stale revision.

Remote fetching is implemented through an injectable transport client so tests
can fake GitHub responses without making real network calls. Production uses a
bounded ``urllib``-based GitHub Contents/Git API client that enforces
retries, timeouts, file-count, and byte-size limits and caches immutable
content keyed by source repository, SHA, and profile.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


# ---------------------------------------------------------------------------
# Constants and allowlists
# ---------------------------------------------------------------------------

#: Bundle manifest schema versions this resolver understands.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

#: Profile identifier pattern. Same conservative rule used by the invocation
#: resolver so a profile name cannot carry shell/path metacharacters.
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Full 40-character lowercase Git commit SHA. Required for any remote source.
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

#: SHA-256 digest pattern (lowercase hex, 64 chars).
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: Built-in allowlist of remote source aliases. A caller may pass one of these
#: aliases as ``configuration_source``; it may never pass a URL, repository
#: name, branch, or tag. Each alias maps to a fixed ``owner/repo`` and an
#: allowed root path within that repository. Tests must not require live
#: network access to validate this allowlist.
REMOTE_SOURCE_ALIASES: dict[str, dict[str, str]] = {
    "central": {
        "repository": "agentic-configuration/example-configuration",
        "root": ".opencode/configuration",
        "description": "Organization-approved shared configuration repository.",
    },
}

#: Conservative bounds shared by local and remote resolution.
MAX_BUNDLE_FILES = 64
MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB per file
MAX_TOTAL_BYTES = 8 * 1024 * 1024  # 8 MiB total bundle content
MAX_MANIFEST_BYTES = 256 * 1024  # 256 KiB manifest

#: Bounds for the remote fetch client.
FETCH_TIMEOUT_SECONDS = 30
FETCH_MAX_RETRIES = 3
FETCH_RESPONSE_BYTES = 4 * 1024 * 1024  # 4 MiB per HTTP response

#: Output contracts known to the runner. A profile may select one of these
#: but cannot introduce a new contract.
SUPPORTED_OUTPUT_CONTRACTS: frozenset[str] = frozenset(
    {
        "pr-review-json-v1",
        "issue-feedback-markdown-v1",
        "issue-implementation-decision-v1",
    }
)

#: Workflows this resolver accepts in ``allowed_workflows``.
SUPPORTED_WORKFLOWS: frozenset[str] = frozenset(
    {
        "pr-documentation-review",
        "issue-feedback",
        "issue-implementation",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigurationError(ValueError):
    """Raised when a configuration bundle cannot be resolved or validated."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def normalize_bundle_path(value: Any) -> str:
    """Normalize and validate a repository-relative POSIX path.

    Rejects absolute paths, backslashes, control characters, ``..`` segments,
    empty segments, and trailing slashes. Returns a canonical POSIX-style
    relative path with forward slashes only.
    """
    if not isinstance(value, str):
        raise ConfigurationError("bundle path must be a string")
    candidate = value.strip()
    if not candidate:
        raise ConfigurationError("bundle path must not be empty")
    if "\\" in candidate:
        raise ConfigurationError(f"bundle path must not contain backslashes: {value!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        raise ConfigurationError(
            f"bundle path must not contain control characters: {value!r}"
        )
    candidate = candidate.replace("\\", "/")
    if candidate.startswith("/"):
        raise ConfigurationError(
            f"bundle path must be relative, got absolute path: {value!r}"
        )
    parts = candidate.split("/")
    if any(part in {"", "."} for part in parts):
        raise ConfigurationError(
            f"bundle path must not contain empty or '.' segments: {value!r}"
        )
    if any(part == ".." for part in parts):
        raise ConfigurationError(
            f"bundle path must not contain '..' segments: {value!r}"
        )
    if candidate.endswith("/"):
        raise ConfigurationError(f"bundle path must not end with '/': {value!r}")
    return candidate


def is_contained(root: Path, candidate: Path) -> bool:
    """Return True when ``candidate`` resolves to a path inside ``root``.

    Uses ``Path.resolve`` so symlink escape is detected. Callers must also
    reject symlinks explicitly via :func:`assert_no_symlink` before reading.
    """
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def assert_no_symlink(path: Path) -> None:
    """Reject any symlink in the path chain from the bundle root to ``path``.

    A symlink could escape the bundle root or point at mutable content. Even
    symlinks pointing back inside the root are denied for determinism.
    """
    if not path.exists():
        return
    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current.is_symlink():
            raise ConfigurationError(f"bundle content path contains a symlink: {path}")
        if current.parent == current:
            break
        current = current.parent
        if current == path.anchor and path.anchor:
            break


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of ``path`` contents."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(obj: Any) -> str:
    """Return a deterministic SHA-256 digest of a JSON-serialisable object."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


# ---------------------------------------------------------------------------
# Front matter validation
# ---------------------------------------------------------------------------


def parse_front_matter(text: str) -> dict[str, str]:
    """Parse a minimal YAML-like front matter block into a flat dict.

    Only top-level ``key: value`` lines (no leading indentation) are captured
    as keys. Indented lines represent nested mappings (for example
    ``permission:`` sub-keys) and are skipped: capability requests are parsed
    separately by :func:`parse_agent_capabilities` in the policy module, which
    walks the raw block. Optional single/double quoting is supported. This
    avoids a YAML parser dependency while validating the required ``name``
    field.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        raise ConfigurationError("agent/skill file must start with YAML front matter")
    body = match.group(1)
    result: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Skip indented (nested) lines; they are sub-keys handled elsewhere.
        if line[:1].isspace():
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped):
            raise ConfigurationError(f"invalid front-matter line: {line!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2) and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        result[key] = value
    return result


def validate_agent_front_matter(text: str, *, workflow: str) -> dict[str, str]:
    """Validate the agent front matter for required fields and capabilities.

    The agent ``name`` must be present and unique-looking. Capabilities
    (``permission.*``) are parsed but treated as *requests*: Plan 4's policy
    engine intersects them with organization and workflow policy. Here we only
    enforce that the front matter is well-formed and that the requested mode is
    supported. Review workflows must remain non-code-writing regardless of
    profile content, so any ``edit: allow`` request is rejected here.
    """
    front = parse_front_matter(text)
    name = front.get("name")
    if not name:
        raise ConfigurationError("agent file front matter must declare a 'name'")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", name):
        raise ConfigurationError(f"agent name must be a safe identifier, got {name!r}")
    mode = front.get("mode", "primary")
    if mode not in {"primary", "subagent"}:
        raise ConfigurationError(
            f"agent mode must be primary or subagent, got {mode!r}"
        )
    # Review workflows must remain non-code-writing regardless of profile.
    if workflow in {"pr-documentation-review", "issue-feedback"}:
        body_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        body = body_match.group(1) if body_match else ""
        if re.search(r"(?m)^\s*edit\s*:\s*allow\s*$", body):
            raise ConfigurationError(
                "review workflows must not request edit permission from an agent"
            )
    return front


def validate_skill_front_matter(text: str) -> dict[str, str]:
    """Validate the skill front matter for a required ``name``."""
    front = parse_front_matter(text)
    if not front.get("name"):
        raise ConfigurationError("skill file front matter must declare a 'name'")
    return front


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleManifest:
    """Validated bundle manifest."""

    schema_version: int
    profile_name: str
    allowed_workflows: tuple[str, ...]
    agent_file: str
    skill_files: tuple[str, ...]
    prompt_template: str
    model_profile: str
    output_contract: str
    limits: dict[str, Any]
    manifest_sha256: str
    additional_agent_files: tuple[str, ...] = ()
    bundle_policy: dict[str, Any] = field(default_factory=dict)


def parse_manifest(payload: Any, *, workflow: str) -> BundleManifest:
    """Parse and validate a bundle manifest dictionary."""
    if not isinstance(payload, dict):
        raise ConfigurationError("bundle manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ConfigurationError(
            f"schema_version must be one of {SUPPORTED_SCHEMA_VERSIONS}; got {schema_version!r}"
        )
    profile_name = payload.get("profile_name")
    if not isinstance(profile_name, str) or not PROFILE_PATTERN.match(profile_name):
        raise ConfigurationError("profile_name must match [a-z0-9][a-z0-9-]{0,62}")
    raw_workflows = payload.get("allowed_workflows")
    if not isinstance(raw_workflows, list) or not raw_workflows:
        raise ConfigurationError("allowed_workflows must be a non-empty list")
    allowed_workflows: list[str] = []
    for item in raw_workflows:
        if not isinstance(item, str) or item not in SUPPORTED_WORKFLOWS:
            raise ConfigurationError(f"unknown workflow in allowed_workflows: {item!r}")
        allowed_workflows.append(item)
    if workflow not in allowed_workflows:
        raise ConfigurationError(
            f"bundle profile {profile_name!r} does not allow workflow {workflow!r}"
        )
    agent_file = normalize_bundle_path(payload.get("agent_file"))
    raw_skills = payload.get("skill_files", [])
    if not isinstance(raw_skills, list):
        raise ConfigurationError("skill_files must be a list")
    skill_files = tuple(normalize_bundle_path(s) for s in raw_skills)
    if len(skill_files) != len({s for s in skill_files}):
        raise ConfigurationError("skill_files must be unique")
    raw_additional_agents = payload.get("additional_agent_files", [])
    if not isinstance(raw_additional_agents, list):
        raise ConfigurationError("additional_agent_files must be a list")
    additional_agent_files = tuple(
        normalize_bundle_path(s) for s in raw_additional_agents
    )
    if len(additional_agent_files) != len({s for s in additional_agent_files}):
        raise ConfigurationError("additional_agent_files must be unique")
    if agent_file in additional_agent_files:
        raise ConfigurationError("additional_agent_files must not duplicate agent_file")
    prompt_template = normalize_bundle_path(payload.get("prompt_template"))
    model_profile = payload.get("model_profile")
    if not isinstance(model_profile, str) or not PROFILE_PATTERN.match(model_profile):
        raise ConfigurationError("model_profile must match [a-z0-9][a-z0-9-]{0,62}")
    output_contract = payload.get("output_contract")
    if (
        not isinstance(output_contract, str)
        or output_contract not in SUPPORTED_OUTPUT_CONTRACTS
    ):
        raise ConfigurationError(
            f"output_contract must be one of {sorted(SUPPORTED_OUTPUT_CONTRACTS)}; got {output_contract!r}"
        )
    limits = payload.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigurationError("limits must be a JSON object")
    bundle_policy = payload.get("policy", {})
    if not isinstance(bundle_policy, dict):
        raise ConfigurationError("policy must be a JSON object")
    manifest_sha256 = sha256_json(payload)
    return BundleManifest(
        schema_version=schema_version,
        profile_name=profile_name,
        allowed_workflows=tuple(allowed_workflows),
        agent_file=agent_file,
        skill_files=skill_files,
        prompt_template=prompt_template,
        model_profile=model_profile,
        output_contract=output_contract,
        limits=dict(limits),
        manifest_sha256=manifest_sha256,
        additional_agent_files=additional_agent_files,
        bundle_policy=dict(bundle_policy),
    )


# ---------------------------------------------------------------------------
# Resolved bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedBundle:
    """Immutable, fully validated resolved configuration."""

    source_alias: str
    repository: str | None
    resolved_sha: str | None
    profile_name: str
    workflow: str
    manifest: BundleManifest
    agent_name: str
    skill_names: tuple[str, ...]
    prompt_template_text: str
    prompt_template_sha256: str
    content_hashes: dict[str, str]
    agent_file: str
    skill_files: tuple[str, ...]
    bundle_root: str
    additional_agent_files: tuple[str, ...] = ()
    additional_agent_names: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_alias": self.source_alias,
            "repository": self.repository,
            "resolved_sha": self.resolved_sha,
            "profile": self.profile_name,
            "workflow": self.workflow,
            "manifest_sha256": self.manifest.manifest_sha256,
            "agent_name": self.agent_name,
            "skill_names": list(self.skill_names),
            "additional_agent_names": list(self.additional_agent_names),
            "prompt_template_text": self.prompt_template_text,
            "prompt_template_sha256": self.prompt_template_sha256,
            "output_contract": self.manifest.output_contract,
            "model_profile": self.manifest.model_profile,
            "agent_file": self.agent_file,
            "skill_files": list(self.skill_files),
            "additional_agent_files": list(self.additional_agent_files),
            "bundle_root": self.bundle_root,
            "content_hashes": dict(self.content_hashes),
            "limits": dict(self.manifest.limits),
            "bundle_policy": dict(self.manifest.bundle_policy),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


# ---------------------------------------------------------------------------
# Local resolution
# ---------------------------------------------------------------------------


def _read_bounded(path: Path, *, max_bytes: int, label: str) -> bytes:
    """Read at most ``max_bytes`` from ``path`` and enforce UTF-8 validity.

    Bounds the read before full allocation: reads in chunks and aborts as soon
    as the size limit is exceeded, so an oversized file is never fully loaded
    into memory.
    """
    chunks: list[bytes] = []
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ConfigurationError(f"{label} exceeds {max_bytes} bytes: {path}")
            chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationError(
            f"{label} must be valid UTF-8: {path}: {error}"
        ) from error
    return raw


def _load_hashes(bundle_root: Path) -> dict[str, str]:
    hashes_path = bundle_root / "hashes.json"
    if not hashes_path.is_file():
        raise ConfigurationError("bundle must contain hashes.json")
    raw = _read_bounded(hashes_path, max_bytes=MAX_MANIFEST_BYTES, label="hashes.json")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"hashes.json must be valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ConfigurationError(
            "hashes.json must be a JSON object mapping paths to digests"
        )
    hashes: dict[str, str] = {}
    for rel, digest in data.items():
        normalized = normalize_bundle_path(rel)
        if not isinstance(digest, str) or not SHA256_PATTERN.match(digest):
            raise ConfigurationError(
                f"invalid SHA-256 digest for {rel!r} in hashes.json"
            )
        hashes[normalized] = digest
    return hashes


def _load_content(
    bundle_root: Path,
    rel: str,
    *,
    hashes: dict[str, str],
    seen: set[str],
    total: int,
) -> tuple[str, int]:
    """Load, hash-check, and UTF-8 validate a single declared content file."""
    if rel in seen:
        raise ConfigurationError(f"duplicate declared content path: {rel}")
    seen.add(rel)
    if rel not in hashes:
        raise ConfigurationError(f"no hash declared for {rel} in hashes.json")
    path = bundle_root / rel
    assert_no_symlink(path)
    if not is_contained(bundle_root, path):
        raise ConfigurationError(f"content path escapes bundle root: {rel}")
    if not path.is_file():
        raise ConfigurationError(f"declared content file is missing: {rel}")
    raw = _read_bounded(path, max_bytes=MAX_FILE_BYTES, label=rel)
    total += len(raw)
    if total > MAX_TOTAL_BYTES:
        raise ConfigurationError("total bundle content exceeds the byte limit")
    actual = sha256_bytes(raw)
    if actual != hashes[rel]:
        raise ConfigurationError(
            f"hash mismatch for {rel}: expected {hashes[rel]}, got {actual}"
        )
    return raw.decode("utf-8"), total


def resolve_local_bundle(
    *,
    bundle_root: Path,
    profile: str,
    workflow: str,
) -> ResolvedBundle:
    """Resolve and validate a local configuration bundle from the trusted checkout."""
    if not isinstance(profile, str) or not PROFILE_PATTERN.match(profile):
        raise ConfigurationError("profile must match [a-z0-9][a-z0-9-]{0,62}")
    profile_dir = bundle_root / profile
    if not profile_dir.is_dir():
        raise ConfigurationError(f"local bundle profile not found: {profile}")
    if profile_dir.is_symlink():
        raise ConfigurationError("bundle profile directory must not be a symlink")
    manifest_path = profile_dir / "bundle.json"
    if not manifest_path.is_file():
        raise ConfigurationError("bundle.json manifest is missing")
    assert_no_symlink(manifest_path)
    if not is_contained(bundle_root, manifest_path):
        raise ConfigurationError("manifest path escapes bundle root")
    manifest_raw = _read_bounded(
        manifest_path, max_bytes=MAX_MANIFEST_BYTES, label="bundle.json"
    )
    try:
        manifest_payload = json.loads(manifest_raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"bundle.json must be valid JSON: {error}") from error
    manifest = parse_manifest(manifest_payload, workflow=workflow)
    if manifest.profile_name != profile:
        raise ConfigurationError(
            f"bundle profile_name {manifest.profile_name!r} does not match requested profile {profile!r}"
        )

    hashes = _load_hashes(profile_dir)
    seen: set[str] = set()
    total = 0

    declared_paths = [
        manifest.agent_file,
        *manifest.additional_agent_files,
        *manifest.skill_files,
        manifest.prompt_template,
    ]
    if len(declared_paths) > MAX_BUNDLE_FILES:
        raise ConfigurationError(
            f"bundle declares more than {MAX_BUNDLE_FILES} content files"
        )

    agent_text, total = _load_content(
        profile_dir, manifest.agent_file, hashes=hashes, seen=seen, total=total
    )
    agent_front = validate_agent_front_matter(agent_text, workflow=workflow)

    additional_agent_names: list[str] = []
    for rel in manifest.additional_agent_files:
        text, total = _load_content(
            profile_dir, rel, hashes=hashes, seen=seen, total=total
        )
        front = validate_agent_front_matter(text, workflow=workflow)
        name = front["name"]
        if name == agent_front["name"]:
            raise ConfigurationError(
                f"additional agent {rel!r} duplicates primary agent name {name!r}"
            )
        if name in additional_agent_names:
            raise ConfigurationError(f"duplicate additional agent name: {name}")
        additional_agent_names.append(name)

    skill_texts: list[str] = []
    skill_names: list[str] = []
    for rel in manifest.skill_files:
        text, total = _load_content(
            profile_dir, rel, hashes=hashes, seen=seen, total=total
        )
        front = validate_skill_front_matter(text)
        name = front["name"]
        if name in skill_names:
            raise ConfigurationError(f"duplicate skill name: {name}")
        skill_names.append(name)
        skill_texts.append(text)

    prompt_text, total = _load_content(
        profile_dir, manifest.prompt_template, hashes=hashes, seen=seen, total=total
    )
    prompt_sha = sha256_bytes(prompt_text.encode("utf-8"))

    # Any path listed in hashes.json that is not declared by the manifest is
    # rejected: the manifest is the authority for what the runner reads.
    extra = set(hashes) - seen
    if extra:
        raise ConfigurationError(
            f"hashes.json declares undeclared content: {sorted(extra)}"
        )

    return ResolvedBundle(
        source_alias="local",
        repository=None,
        resolved_sha=None,
        profile_name=profile,
        workflow=workflow,
        manifest=manifest,
        agent_name=agent_front["name"],
        skill_names=tuple(skill_names),
        prompt_template_text=prompt_text,
        prompt_template_sha256=prompt_sha,
        content_hashes=dict(hashes),
        agent_file=manifest.agent_file,
        skill_files=manifest.skill_files,
        bundle_root=str(profile_dir),
        additional_agent_files=manifest.additional_agent_files,
        additional_agent_names=tuple(additional_agent_names),
    )


# ---------------------------------------------------------------------------
# Remote fetch client
# ---------------------------------------------------------------------------


class RemoteFetchClient(Protocol):
    """Injectable transport for fetching remote bundle content.

    Implementations must return a tuple of ``(content_bytes, resolved_sha)``
    for the manifest, and a mapping of declared relative paths to bytes for
    content. Tests provide a fake implementation; production uses
    :class:`GitHubContentsClient`.
    """

    def fetch_manifest(
        self, *, repository: str, root: str, profile: str, sha: str
    ) -> tuple[bytes, str]: ...

    def fetch_content(
        self, *, repository: str, root: str, path: str, sha: str
    ) -> bytes: ...


class GitHubContentsClient:
    """Bounded GitHub Contents API client for remote bundle fetching.

    Uses GitHub's authenticated REST Contents API
    (``api.github.com/repos/<owner>/<repo>/contents/<path>?ref=<sha>``) to fetch
    individual files at a pinned commit SHA. The response is JSON with a
    ``content`` field containing base64-encoded file bytes. The bearer token
    authenticates access to private central configuration repositories; the
    token is never logged or included in artifacts.

    Enforces timeouts, bounded retries (transient 5xx/network only; 4xx fails
    immediately), and per-response byte limits. Reads the response in bounded
    chunks rather than buffering unbounded content.
    """

    def __init__(
        self,
        *,
        token: str,
        timeout: int = FETCH_TIMEOUT_SECONDS,
        max_retries: int = FETCH_MAX_RETRIES,
        max_response_bytes: int = FETCH_RESPONSE_BYTES,
    ) -> None:
        if not token:
            raise ConfigurationError(
                "a GitHub token is required for remote bundle fetch"
            )
        self._token = token
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes

    def _contents_url(self, repository: str, path: str, sha: str) -> str:
        return f"https://api.github.com/repos/{repository}/contents/{path}?ref={sha}"

    def _get(self, url: str) -> bytes:
        """Fetch and decode a single file from the GitHub Contents API.

        Reads the JSON response in bounded chunks, decodes the base64
        ``content``, and never logs the token. Retries only transient errors.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "generic-agentic-workflows-resolver",
                    },
                )
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    # Read in bounded chunks; reject oversized responses.
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self._max_response_bytes:
                            raise ConfigurationError(
                                f"remote response exceeds {self._max_response_bytes} bytes: {url}"
                            )
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise ConfigurationError(
                        f"remote Contents API returned non-JSON for {url}: {error}"
                    ) from error
                if not isinstance(payload, dict) or payload.get("type") != "file":
                    raise ConfigurationError(
                        f"remote Contents API did not return a file for {url}"
                    )
                import base64

                content = payload.get("content", "")
                encoding = payload.get("encoding", "base64")
                if encoding != "base64" or not isinstance(content, str):
                    raise ConfigurationError(
                        f"remote Contents API returned unsupported encoding for {url}"
                    )
                data = base64.b64decode(content)
                if len(data) > self._max_response_bytes:
                    raise ConfigurationError(
                        f"remote file exceeds {self._max_response_bytes} bytes: {url}"
                    )
                return data
            except urllib.error.HTTPError as error:
                # Non-retryable HTTP errors (4xx) fail immediately.
                last_error = error
                if 400 <= error.code < 500:
                    raise ConfigurationError(
                        f"remote fetch failed with HTTP {error.code} for {url}"
                    ) from error
            except urllib.error.URLError as error:
                last_error = error
        raise ConfigurationError(
            f"remote fetch failed after {self._max_retries} attempts: {last_error}"
        )

    def fetch_manifest(
        self, *, repository: str, root: str, profile: str, sha: str
    ) -> tuple[bytes, str]:
        path = f"{root}/{profile}/bundle.json"
        return self._get(self._contents_url(repository, path, sha)), sha

    def fetch_content(
        self, *, repository: str, root: str, path: str, sha: str
    ) -> bytes:
        return self._get(self._contents_url(repository, path, sha))


def _remote_cache_dir() -> Path:
    base = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    cache = Path(base) / "agentic-bundle-cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _cache_key(repository: str, sha: str, profile: str) -> str:
    return f"{repository.replace('/', '_')}_{sha}_{profile}"


def resolve_remote_bundle(
    *,
    source_alias: str,
    configuration_ref: str,
    profile: str,
    workflow: str,
    client: RemoteFetchClient,
    cache_dir: Path | None = None,
) -> ResolvedBundle:
    """Resolve an allowlisted remote bundle pinned to a full commit SHA.

    Fails closed if the alias is unknown, the SHA is not exactly 40 lowercase
    hex characters, or any fetch/hash/schema validation fails. Cached content
    is keyed by ``(repository, sha, profile)`` and re-validated before use.
    """
    if source_alias not in REMOTE_SOURCE_ALIASES:
        raise ConfigurationError(
            f"unknown remote source alias: {source_alias!r}; "
            f"allowlisted aliases are {sorted(REMOTE_SOURCE_ALIASES)}"
        )
    if not isinstance(configuration_ref, str) or not SHA_PATTERN.match(
        configuration_ref
    ):
        raise ConfigurationError(
            "remote configuration_ref must be a full 40-character lowercase commit SHA"
        )
    if not isinstance(profile, str) or not PROFILE_PATTERN.match(profile):
        raise ConfigurationError("profile must match [a-z0-9][a-z0-9-]{0,62}")
    alias = REMOTE_SOURCE_ALIASES[source_alias]
    repository = alias["repository"]
    root = alias["root"]

    cache = cache_dir if cache_dir is not None else _remote_cache_dir()
    key = _cache_key(repository, configuration_ref, profile)
    slot = cache / key
    profile_dir = slot / profile
    if profile_dir.is_dir():
        try:
            return _resolved_from_local(
                profile_dir=profile_dir,
                source_alias=source_alias,
                repository=repository,
                resolved_sha=configuration_ref,
                workflow=workflow,
            )
        except ConfigurationError:
            # A corrupted cache entry must never be used; remove and refetch.
            import shutil

            shutil.rmtree(slot, ignore_errors=True)

    # Robust slot creation: handle a partial/leftover slot from a prior
    # interrupted run by removing it before creating a fresh one.
    if slot.exists():
        import shutil

        shutil.rmtree(slot, ignore_errors=True)
    slot.mkdir(parents=True, exist_ok=False)
    try:
        manifest_raw, resolved_sha = client.fetch_manifest(
            repository=repository, root=root, profile=profile, sha=configuration_ref
        )
        if len(manifest_raw) > MAX_MANIFEST_BYTES:
            raise ConfigurationError("remote manifest exceeds the byte limit")
        if resolved_sha != configuration_ref:
            raise ConfigurationError(
                "remote fetch returned a different SHA than requested; refusing to use it"
            )
        try:
            manifest_payload = json.loads(manifest_raw)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"remote bundle.json must be valid JSON: {error}"
            ) from error
        manifest = parse_manifest(manifest_payload, workflow=workflow)
        if manifest.profile_name != profile:
            raise ConfigurationError(
                f"remote bundle profile_name {manifest.profile_name!r} does not match {profile!r}"
            )
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "bundle.json").write_bytes(manifest_raw)

        hashes_raw = client.fetch_content(
            repository=repository,
            root=root,
            path=f"{root}/{profile}/hashes.json",
            sha=configuration_ref,
        )
        try:
            hashes_payload = json.loads(hashes_raw)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"remote hashes.json must be valid JSON: {error}"
            ) from error
        if not isinstance(hashes_payload, dict):
            raise ConfigurationError("remote hashes.json must be a JSON object")
        hashes: dict[str, str] = {}
        for rel, digest in hashes_payload.items():
            normalized = normalize_bundle_path(rel)
            if not isinstance(digest, str) or not SHA256_PATTERN.match(digest):
                raise ConfigurationError(
                    f"invalid SHA-256 digest for {rel!r} in remote hashes.json"
                )
            hashes[normalized] = digest
        (profile_dir / "hashes.json").write_bytes(hashes_raw)

        seen: set[str] = set()
        total = 0
        declared = [
            manifest.agent_file,
            *manifest.additional_agent_files,
            *manifest.skill_files,
            manifest.prompt_template,
        ]
        if len(declared) > MAX_BUNDLE_FILES:
            raise ConfigurationError(
                f"remote bundle declares more than {MAX_BUNDLE_FILES} content files"
            )
        for rel in declared:
            if rel in seen:
                raise ConfigurationError(f"duplicate declared content path: {rel}")
            seen.add(rel)
            if rel not in hashes:
                raise ConfigurationError(
                    f"no hash declared for {rel} in remote hashes.json"
                )
            content = client.fetch_content(
                repository=repository,
                root=root,
                path=f"{root}/{profile}/{rel}",
                sha=configuration_ref,
            )
            if len(content) > MAX_FILE_BYTES:
                raise ConfigurationError(
                    f"remote content file exceeds {MAX_FILE_BYTES} bytes: {rel}"
                )
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise ConfigurationError(
                    "remote bundle total content exceeds the byte limit"
                )
            actual = sha256_bytes(content)
            if actual != hashes[rel]:
                raise ConfigurationError(
                    f"remote hash mismatch for {rel}: expected {hashes[rel]}, got {actual}"
                )
            (profile_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (profile_dir / rel).write_bytes(content)

        extra = set(hashes) - seen
        if extra:
            raise ConfigurationError(
                f"remote hashes.json declares undeclared content: {sorted(extra)}"
            )

        resolved = _resolved_from_local(
            profile_dir=profile_dir,
            source_alias=source_alias,
            repository=repository,
            resolved_sha=resolved_sha,
            workflow=workflow,
        )
        return resolved
    except ConfigurationError:
        # Never leave a partially materialized remote bundle in the cache.
        import shutil

        shutil.rmtree(slot, ignore_errors=True)
        raise


def _resolved_from_local(
    *,
    profile_dir: Path,
    source_alias: str,
    repository: str,
    resolved_sha: str,
    workflow: str,
) -> ResolvedBundle:
    """Resolve a materialized profile dir and stamp it with remote metadata."""
    resolved = resolve_local_bundle(
        bundle_root=profile_dir.parent, profile=profile_dir.name, workflow=workflow
    )
    return ResolvedBundle(
        source_alias=source_alias,
        repository=repository,
        resolved_sha=resolved_sha,
        profile_name=resolved.profile_name,
        workflow=resolved.workflow,
        manifest=resolved.manifest,
        agent_name=resolved.agent_name,
        skill_names=resolved.skill_names,
        prompt_template_text=resolved.prompt_template_text,
        prompt_template_sha256=resolved.prompt_template_sha256,
        content_hashes=resolved.content_hashes,
        agent_file=resolved.agent_file,
        skill_files=resolved.skill_files,
        bundle_root=resolved.bundle_root,
        additional_agent_files=resolved.additional_agent_files,
        additional_agent_names=resolved.additional_agent_names,
    )


# ---------------------------------------------------------------------------
# Verified agent/skill materialization for OpenCode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StagedContent:
    """A staged, hash-reverified file written for OpenCode to scan.

    ``backup_path`` holds a byte-for-byte backup of any pre-existing file at
    ``staged_path`` (``None`` when the destination did not exist before
    staging). ``cleanup_staged`` restores the original bytes (or removes the
    file when there was none) so staging never causes data loss or
    self-triggers denied-path checks.
    """

    rel: str
    staged_path: Path
    name: str
    kind: str  # "agent" or "skill"
    backup_path: Path | None = None
    existed: bool = False


def materialize_to_opencode_root(
    resolved: Mapping[str, Any] | ResolvedBundle,
    target_root: Path,
) -> list[StagedContent]:
    """Materialize hash-verified agent/skill files into an OpenCode scan root.

    Reads each declared agent/skill file from the resolved bundle's
    ``bundle_root``, re-verifies its SHA-256 against ``content_hashes`` before
    writing, writes it under ``target_root/.opencode/agents/<name>.md`` (or
    ``.opencode/skills/<name>/SKILL.md``), and re-verifies the written bytes.

    Fails closed if any hash mismatches; no fallback to unverified copies.

    **Collision-safe**: when a destination file already exists (for example a
    tracked ``.opencode/agents/executor.md`` in the repository checkout), its
    bytes are backed up to a temporary file and restored verbatim by
    :func:`cleanup_staged`. This prevents data loss and prevents the staged
    file from appearing as a changed path in diff enforcement.

    OpenCode is then invoked with ``--dir <target_root>`` (or run from
    ``target_root``) so it scans only the verified configuration and cannot
    fall back to unverified checked-out agents.
    """
    if isinstance(resolved, ResolvedBundle):
        data = resolved.to_dict()
    else:
        data = dict(resolved)
    bundle_root = Path(data["bundle_root"])
    content_hashes: dict[str, str] = dict(data.get("content_hashes", {}))
    staged: list[StagedContent] = []

    agent_pairs = [(data.get("agent_file", ""), data.get("agent_name", ""), "agent")]
    for rel, name in zip(
        data.get("additional_agent_files", []), data.get("additional_agent_names", [])
    ):
        agent_pairs.append((rel, name, "agent"))
    for rel, name in zip(data.get("skill_files", []), data.get("skill_names", [])):
        agent_pairs.append((rel, name, "skill"))

    for rel, name, kind in agent_pairs:
        if not rel or not name:
            raise ConfigurationError(
                f"cannot stage content with missing rel/name: rel={rel!r} name={name!r}"
            )
        if rel not in content_hashes:
            raise ConfigurationError(
                f"no hash for declared content {rel!r}; refusing to stage"
            )
        src = bundle_root / rel
        assert_no_symlink(src)
        if not src.is_file():
            raise ConfigurationError(
                f"verified content file missing from bundle root: {rel}"
            )
        raw = _read_bounded(src, max_bytes=MAX_FILE_BYTES, label=rel)
        if sha256_bytes(raw) != content_hashes[rel]:
            raise ConfigurationError(
                f"hash mismatch before staging {rel!r}: bundle root content changed after resolution"
            )
        if kind == "agent":
            dest = target_root / ".opencode" / "agents" / f"{name}.md"
        else:
            dest = target_root / ".opencode" / "skills" / name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Back up any pre-existing destination file byte-for-byte so cleanup
        # can restore it exactly. This makes staging collision-safe.
        backup_path: Path | None = None
        existed = False
        if dest.exists():
            existed = True
            backup_path = dest.with_suffix(dest.suffix + ".agentic-backup")
            backup_path.write_bytes(dest.read_bytes())
        dest.write_bytes(raw)
        # Re-verify the staged bytes match the verified hash exactly.
        written = dest.read_bytes()
        if sha256_bytes(written) != content_hashes[rel]:
            # Restore the original before failing.
            if existed and backup_path is not None:
                dest.write_bytes(backup_path.read_bytes())
                backup_path.unlink()
            raise ConfigurationError(f"staged file hash mismatch for {rel!r}")
        staged.append(
            StagedContent(
                rel=rel,
                staged_path=dest,
                name=name,
                kind=kind,
                backup_path=backup_path,
                existed=existed,
            )
        )
    return staged


def cleanup_staged(staged: list[StagedContent]) -> None:
    """Restore pre-existing files and remove staged files cleanly.

    For each staged entry:
    - if a backup exists (the destination pre-existed), restore the original
      bytes verbatim and remove the backup;
    - otherwise remove the staged file (it was newly created by staging).

    Empty parent directories created by staging are removed only if they are
    still empty and are not the ``.opencode`` root. This ensures no data loss
    and a clean git status after staging into a workspace checkout.
    """
    for entry in staged:
        if entry.existed and entry.backup_path is not None:
            # Restore the original bytes exactly.
            entry.staged_path.write_bytes(entry.backup_path.read_bytes())
            try:
                entry.backup_path.unlink()
            except FileNotFoundError:
                pass
        else:
            try:
                entry.staged_path.unlink()
            except FileNotFoundError:
                pass
            # Remove newly-empty parents (skills/<name>, skills) but never
            # remove non-empty directories or the .opencode root itself.
            parent = entry.staged_path.parent
            opencode_root = (
                entry.staged_path.parents[1]
                if len(entry.staged_path.parents) > 1
                else None
            )
            for ancestor in [parent, parent.parent]:
                try:
                    if ancestor == opencode_root:
                        continue
                    ancestor.rmdir()
                except (OSError, ValueError):
                    break


# ---------------------------------------------------------------------------
# Real OpenCode schema smoke check
# ---------------------------------------------------------------------------


def opencode_config_smoke_check(
    target_root: Path,
    *,
    binary: str = "opencode",
    timeout: int = 60,
) -> tuple[bool, str]:
    """Run ``opencode debug config`` in ``target_root`` and report schema errors.

    This is a non-network, non-provider config/schema load check. It confirms
    the staged agents/skills are loadable by the real OpenCode binary without
    calling a paid provider or mutating GitHub. Returns ``(ok, message)``.

    Skips cleanly (returns ``(True, "skipped: <reason>")``) only when the
    binary is not installed, so callers can run this in CI where the binary is
    available and in local dev where it may be absent.
    """
    import shutil
    import subprocess

    if shutil.which(binary) is None:
        return True, f"skipped: {binary!r} binary not found"
    try:
        result = subprocess.run(
            [binary, "debug", "config"],
            cwd=str(target_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{binary} debug config timed out after {timeout}s"
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return (
            False,
            f"{binary} debug config failed (rc={result.returncode}):\n{combined[:1000]}",
        )
    return True, "ok"


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------


LEGACY_DEPRECATION_WARNING = (
    "CUSTOM_AGENT_FILE/CUSTOM_SKILL_FILE are deprecated; use a configuration "
    "bundle (configuration_profile). These variables will be removed in the "
    "next major workflow release after the published migration window."
)


def resolve_legacy_bundle(
    *,
    agent_file: str,
    skill_file: str | None,
    workflow: str,
    repo_root: Path,
    output_contract: str,
    model_profile: str = "legacy",
) -> ResolvedBundle:
    """Build an in-memory bundle from legacy custom-file variables.

    This preserves Plan 1 behavior during the documented migration window. It
    emits a deprecation warning to stderr and constructs a synthetic bundle
    manifest validated by the same path/hash logic. Legacy files must live
    inside the trusted ``repo_root`` and must not be symlinks.
    """
    print(f"::warning::{LEGACY_DEPRECATION_WARNING}", file=sys.stderr)
    if not isinstance(agent_file, str) or not agent_file:
        raise ConfigurationError("legacy CUSTOM_AGENT_FILE must be a non-empty path")
    normalized_agent = normalize_bundle_path(agent_file)
    agent_path = repo_root / normalized_agent
    assert_no_symlink(agent_path)
    if not is_contained(repo_root, agent_path) or not agent_path.is_file():
        raise ConfigurationError(
            f"legacy agent file not found or escapes repo root: {agent_file}"
        )
    agent_raw = _read_bounded(
        agent_path, max_bytes=MAX_FILE_BYTES, label=normalized_agent
    )
    agent_text = agent_raw.decode("utf-8")
    agent_front = validate_agent_front_matter(agent_text, workflow=workflow)

    skill_files: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    skill_texts: list[str] = []
    if skill_file:
        normalized_skill = normalize_bundle_path(skill_file)
        skill_path = repo_root / normalized_skill
        assert_no_symlink(skill_path)
        if not is_contained(repo_root, skill_path) or not skill_path.is_file():
            raise ConfigurationError(
                f"legacy skill file not found or escapes repo root: {skill_file}"
            )
        skill_raw = _read_bounded(
            skill_path, max_bytes=MAX_FILE_BYTES, label=normalized_skill
        )
        skill_text = skill_raw.decode("utf-8")
        validate_skill_front_matter(skill_text)
        skill_files = (normalized_skill,)
        skill_names = (parse_front_matter(skill_text)["name"],)
        skill_texts = [skill_text]

    prompt_text = _default_legacy_prompt(workflow)
    prompt_sha = sha256_bytes(prompt_text.encode("utf-8"))

    content_hashes = {
        normalized_agent: sha256_bytes(agent_raw),
    }
    for rel, text in zip(skill_files, skill_texts):
        content_hashes[rel] = sha256_bytes(text.encode("utf-8"))

    manifest_payload = {
        "schema_version": 1,
        "profile_name": "legacy",
        "allowed_workflows": [workflow],
        "agent_file": normalized_agent,
        "skill_files": list(skill_files),
        "prompt_template": "<legacy>",
        "model_profile": model_profile,
        "output_contract": output_contract,
        "limits": {},
    }
    manifest = parse_manifest(manifest_payload, workflow=workflow)

    return ResolvedBundle(
        source_alias="local",
        repository=None,
        resolved_sha=None,
        profile_name="legacy",
        workflow=workflow,
        manifest=manifest,
        agent_name=agent_front["name"],
        skill_names=skill_names,
        prompt_template_text=prompt_text,
        prompt_template_sha256=prompt_sha,
        content_hashes=content_hashes,
        agent_file=normalized_agent,
        skill_files=skill_files,
        bundle_root=str(repo_root),
        additional_agent_files=(),
        additional_agent_names=(),
    )


def _default_legacy_prompt(workflow: str) -> str:
    """Return the workflow-owned default prompt used for legacy configurations.

    This mirrors the hard-coded prompts that Plan 1 wired into the workflow
    YAML so legacy callers see identical model instructions during the
    migration window.
    """
    if workflow == "pr-documentation-review":
        return "Evaluate this pull request diff."
    if workflow == "issue-feedback":
        return (
            "Review this open issue and identify missing information, risks, or "
            "useful next steps. Be concise and only offer constructive feedback."
        )
    if workflow == "issue-implementation":
        return (
            "Create a concise, secure plan for the supplied GitHub issue and "
            "delegate its implementation to the executor agent."
        )
    return "Provide feedback on the supplied content."


# ---------------------------------------------------------------------------
# Redacted failure record
# ---------------------------------------------------------------------------


def redacted_failure_record(
    *,
    workflow: str,
    source_alias: str,
    profile: str,
    configuration_ref: str | None,
    error: Exception,
) -> dict[str, Any]:
    """Build a redacted attempted-resolution record for a resolution failure.

    Never includes credentials, full file contents, or untrusted text. The
    error message is included verbatim because the resolver only produces
    non-secret messages.
    """
    record: dict[str, Any] = {
        "workflow": workflow,
        "source_alias": source_alias,
        "profile": profile,
        "configuration_ref": configuration_ref,
        "result": "failed",
        "error": type(error).__name__,
        "message": str(error),
    }
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve and validate an agentic configuration bundle."
    )
    parser.add_argument(
        "--workflow", required=True, choices=sorted(SUPPORTED_WORKFLOWS)
    )
    parser.add_argument("--bundle-root", default=".opencode/configuration")
    parser.add_argument("--configuration-source", default="local")
    parser.add_argument("--configuration-ref", default=None)
    parser.add_argument("--configuration-profile", default=None)
    parser.add_argument(
        "--result", default=None, help="Path to write the resolved bundle JSON to."
    )
    parser.add_argument(
        "--legacy-agent-file",
        default=None,
        help="Legacy CUSTOM_AGENT_FILE path; emits a deprecation warning.",
    )
    parser.add_argument(
        "--legacy-skill-file",
        default=None,
        help="Legacy CUSTOM_SKILL_FILE path; emits a deprecation warning.",
    )
    parser.add_argument(
        "--output-contract",
        default=None,
        help="Output contract to assume for legacy resolution.",
    )
    parser.add_argument(
        "--github-step-summary",
        default=None,
        help="Path to append a job summary to.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.legacy_agent_file:
            if not args.output_contract:
                parser.error("--output-contract is required for legacy resolution")
            resolved = resolve_legacy_bundle(
                agent_file=args.legacy_agent_file,
                skill_file=args.legacy_skill_file,
                workflow=args.workflow,
                repo_root=Path.cwd(),
                output_contract=args.output_contract,
            )
        elif args.configuration_source == "local":
            if not args.configuration_profile:
                parser.error(
                    "--configuration-profile is required (or use --legacy-agent-file)"
                )
            resolved = resolve_local_bundle(
                bundle_root=Path(args.bundle_root),
                profile=args.configuration_profile,
                workflow=args.workflow,
            )
        else:
            if not args.configuration_profile:
                parser.error("--configuration-profile is required for remote sources")
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            client = GitHubContentsClient(token=token or "")
            resolved = resolve_remote_bundle(
                source_alias=args.configuration_source,
                configuration_ref=args.configuration_ref or "",
                profile=args.configuration_profile,
                workflow=args.workflow,
                client=client,
            )
    except ConfigurationError as error:
        print(f"::error::Configuration resolution failed: {error}", file=sys.stderr)
        return 1
    payload = resolved.to_json()
    print(payload)
    if args.result:
        Path(args.result).write_text(payload + "\n", encoding="utf-8")
    if args.github_step_summary:
        write_job_summary(resolved, Path(args.github_step_summary))
    return 0


def write_job_summary(resolved: ResolvedBundle, summary_path: Path) -> None:
    lines = [
        "### Resolved agentic configuration",
        "",
        f"- Workflow: `{resolved.workflow}`",
        f"- Source: `{resolved.source_alias}`",
    ]
    if resolved.repository:
        lines.append(f"- Repository: `{resolved.repository}`")
    if resolved.resolved_sha:
        lines.append(f"- Resolved SHA: `{resolved.resolved_sha}`")
    lines.extend(
        [
            f"- Profile: `{resolved.profile_name}`",
            f"- Agent: `{resolved.agent_name}`",
            f"- Skills: `{', '.join(resolved.skill_names) or '<none>'}`",
            f"- Model profile: `{resolved.manifest.model_profile}`",
            f"- Output contract: `{resolved.manifest.output_contract}`",
            f"- Manifest SHA-256: `{resolved.manifest.manifest_sha256}`",
            f"- Prompt template SHA-256: `{resolved.prompt_template_sha256}`",
        ]
    )
    existing = ""
    if summary_path.exists():
        existing = summary_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    summary_path.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
