#!/usr/bin/env python3
"""Update ``hashes.json`` for one or more configuration bundles.

This is a developer convenience script. It recomputes SHA-256 digests for every
content file declared in a bundle manifest (``bundle.json``) and writes them
into that bundle's ``hashes.json``. It is the UV-native replacement for the
manual ``sha256sum`` workflow documented in ``CONTRIBUTING.md``.

The script reuses the resolver's own hashing and path-normalization helpers so
the digests it writes are byte-for-byte identical to what
:mod:`agentic_configuration` validates at runtime. It never hashes
``manifest.json`` or ``bundle.json`` themselves (the resolver does not require
those in ``hashes.json``), and it never touches files outside the bundle
directory.

Usage::

    # Update every bundle under the default configuration root:
    uv run scripts/update_hashes.py

    # Update a single profile:
    uv run scripts/update_hashes.py --profile documentation-review

    # Point at a non-default bundle root:
    uv run scripts/update_hashes.py --bundle-root .opencode/configuration

    # Dry run: print the diff without writing:
    uv run scripts/update_hashes.py --dry-run

Exit codes:
    0  all bundles updated (or already up to date)
    1  one or more bundles could not be updated (missing files, bad manifest)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

#: Default bundle root relative to the repository root.
DEFAULT_BUNDLE_ROOT = ".opencode/configuration"

#: Maximum bytes for a single content file (mirrors the resolver bound).
MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB


def _load_resolver() -> Any:
    """Load :mod:`agentic_configuration` from ``scripts/`` without a package."""
    spec = importlib.util.spec_from_file_location(
        "agentic_configuration", SCRIPTS_DIR / "agentic_configuration.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load agentic_configuration module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentic_configuration"] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return data


def _declared_content_files(manifest: dict[str, Any]) -> list[str]:
    """Return the ordered, de-duplicated list of declared content paths."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            paths.append(value)
            seen.add(value)

    _add(manifest.get("agent_file"))
    for skill in manifest.get("skill_files", []) or []:
        _add(skill)
    for extra in manifest.get("additional_agent_files", []) or []:
        _add(extra)
    _add(manifest.get("prompt_template"))
    return paths


def _compute_hashes(
    bundle_dir: Path, paths: list[str], resolver: Any
) -> dict[str, str]:
    """Compute SHA-256 digests for each declared content file."""
    hashes: dict[str, str] = {}
    for rel in paths:
        normalized = resolver.normalize_bundle_path(rel)
        path = bundle_dir / normalized
        if not path.is_file():
            raise FileNotFoundError(
                f"declared content file is missing: {normalized} (in {bundle_dir})"
            )
        if not resolver.is_contained(bundle_dir, path):
            raise ValueError(
                f"content path escapes bundle root: {normalized} (in {bundle_dir})"
            )
        resolver.assert_no_symlink(path)
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(
                f"content file exceeds {MAX_FILE_BYTES} bytes: {normalized}"
            )
        hashes[normalized] = resolver.sha256_bytes(raw)
    return hashes


def _format_hash_diff(current: dict[str, str], updated: dict[str, str]) -> list[str]:
    """Return human-readable lines describing the differences between two maps."""
    lines: list[str] = []
    all_keys = sorted(set(current) | set(updated))
    for key in all_keys:
        old = current.get(key)
        new = updated.get(key)
        if old is None:
            lines.append(f"  + {key}: {new} (added)")
        elif new is None:
            lines.append(f"  - {key}: {old} (removed)")
        elif old != new:
            lines.append(f"  ~ {key}:")
            lines.append(f"      {old} -> {new}")
    if not lines:
        lines.append("  (no changes)")
    return lines


def update_bundle(
    bundle_dir: Path, *, resolver: Any, dry_run: bool = False
) -> tuple[bool, list[str]]:
    """Update ``hashes.json`` for a single bundle directory.

    Returns ``(changed, log_lines)`` where ``changed`` is True when the hashes
    would be (or were) written and ``log_lines`` is a human-readable report.
    """
    manifest_path = bundle_dir / "bundle.json"
    hashes_path = bundle_dir / "hashes.json"
    manifest = _load_json(manifest_path, label="bundle manifest")
    paths = _declared_content_files(manifest)
    if not paths:
        return False, [f"  {bundle_dir.name}: no content files declared; skipped"]
    updated = _compute_hashes(bundle_dir, paths, resolver)
    current: dict[str, str] = {}
    if hashes_path.is_file():
        try:
            current = _load_json(hashes_path, label="hashes.json")
        except ValueError:
            current = {}
    changed = current != updated
    log_lines = [f"  {bundle_dir.name}:"]
    log_lines.extend(_format_hash_diff(current, updated))
    if changed and not dry_run:
        hashes_path.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return changed, log_lines


def discover_bundles(bundle_root: Path) -> list[Path]:
    """Return every bundle directory that contains a ``bundle.json``."""
    if not bundle_root.is_dir():
        return []
    bundles: list[Path] = []
    for entry in sorted(bundle_root.iterdir()):
        if entry.is_dir() and (entry / "bundle.json").is_file():
            bundles.append(entry)
    return bundles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Update hashes.json for agentic configuration bundles."
    )
    parser.add_argument(
        "--bundle-root",
        default=DEFAULT_BUNDLE_ROOT,
        help=f"Bundle root directory (default: {DEFAULT_BUNDLE_ROOT}).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Only update the named profile (may be repeated). Defaults to all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing files.",
    )
    args = parser.parse_args(argv)

    bundle_root = Path(args.bundle_root)
    if not bundle_root.is_absolute():
        bundle_root = REPO_ROOT / bundle_root
    if not bundle_root.is_dir():
        print(f"error: bundle root not found: {bundle_root}", file=sys.stderr)
        return 1

    resolver = _load_resolver()
    bundles = discover_bundles(bundle_root)
    if args.profiles:
        wanted = set(args.profiles)
        bundles = [b for b in bundles if b.name in wanted]
        missing = wanted - {b.name for b in bundles}
        if missing:
            print(
                f"error: profile(s) not found: {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            return 1
    if not bundles:
        print(f"no bundles found under {bundle_root}", file=sys.stderr)
        return 1

    print(f"{'DRY RUN: ' if args.dry_run else ''}updating hashes for "
          f"{len(bundles)} bundle(s) under {bundle_root}")
    any_changed = False
    failures = 0
    for bundle_dir in bundles:
        try:
            changed, log_lines = update_bundle(
                bundle_dir, resolver=resolver, dry_run=args.dry_run
            )
        except (FileNotFoundError, ValueError) as error:
            print(f"error: {bundle_dir.name}: {error}", file=sys.stderr)
            failures += 1
            continue
        any_changed = any_changed or changed
        for line in log_lines:
            print(line)

    if failures:
        print(f"\n{failures} bundle(s) failed.", file=sys.stderr)
        return 1
    if any_changed:
        action = "would update" if args.dry_run else "updated"
        print(f"\nHashes {action} for {len(bundles)} bundle(s).")
    else:
        print(f"\nAll {len(bundles)} bundle(s) already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
