"""Tests for checked-in configuration bundle hash freshness."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_hashes", REPO_ROOT / "scripts/update_hashes.py"
)
assert SPEC and SPEC.loader
UPDATE_HASHES = importlib.util.module_from_spec(SPEC)
sys.modules["update_hashes"] = UPDATE_HASHES
SPEC.loader.exec_module(UPDATE_HASHES)


class CheckedInBundleHashTests(unittest.TestCase):
    def test_primary_bundle_hashes_are_current(self) -> None:
        self.assertEqual(UPDATE_HASHES.main(["--dry-run"]), 0)

    def test_local_example_bundle_hashes_are_current(self) -> None:
        self.assertEqual(
            UPDATE_HASHES.main(
                [
                    "--dry-run",
                    "--bundle-root",
                    "docs/examples/configuration-sources/local/.opencode/configuration",
                ]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()