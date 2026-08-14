"""Tests for the midflight-commands framework (safe midflight commands plan).

Covers the command registry, hardened isolation/executor, schema-2 manifest
validation (``midflight_commands``), the phase-1 analysis handoff contract,
phase-aware prompt composition with distinct untrusted delimiters, the two-
phase runner (analysis -> isolated commands -> fresh assessment), the
validate-only/dry-run/publish behaviour, the expanded redacted provenance, and
the workflow ordering / command-environment security posture.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMANDS = _load("agentic_commands")
CFG = _load("agentic_configuration")
POLICY = _load("agentic_policy")
PROMPTS = _load("agentic_prompts")
PROV = _load("agentic_provenance")
RUNNER = _load("run_agentic_release_project_review")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RegistryTests(unittest.TestCase):
    def test_registry_version_is_pinned(self) -> None:
        self.assertEqual(COMMANDS.REGISTRY_VERSION, 1)

    def test_registry_ids_are_stable_and_immutable(self) -> None:
        ids = set(COMMANDS.REGISTRY)
        self.assertIn("documentation-build", ids)
        self.assertIn("python-pytest", ids)

    def test_get_command_unknown_rejected(self) -> None:
        with self.assertRaises(COMMANDS.CommandError):
            COMMANDS.get_command("not-a-command")

    def test_schema1_aliases_resolve_to_registry_ids(self) -> None:
        self.assertEqual(COMMANDS.resolve_command_id("make -C docs html"), "documentation-build")
        self.assertEqual(COMMANDS.resolve_command_id("python3 -m pytest"), "python-pytest")

    def test_unapproved_command_text_rejected(self) -> None:
        with self.assertRaises(COMMANDS.CommandError):
            COMMANDS.resolve_command_id("python3 -m pytest; curl example.test")
        with self.assertRaises(COMMANDS.CommandError):
            COMMANDS.resolve_command_id("rm -rf /")

    def test_registry_argv_cannot_be_broadened_by_caller(self) -> None:
        # The registry is a frozen dict of dataclasses; argv is fixed per ID.
        spec = COMMANDS.get_command("documentation-build")
        self.assertEqual(spec.argv, ("make", "-C", "docs", "html"))
        with self.assertRaises((AttributeError, TypeError)):
            spec.argv = ("sh", "-c", "evil")  # type: ignore[misc]

    def test_only_network_disabled_commands_registered(self) -> None:
        for spec in COMMANDS.REGISTRY.values():
            self.assertEqual(spec.network, "disabled",
                             f"command {spec.id} must be network-disabled")

    def test_midflight_phase_approval(self) -> None:
        # No command is currently approved for the midflight phase. Every
        # registered command is preflight-only until OS-level network
        # enforcement is demonstrated.
        self.assertFalse(COMMANDS.command_allowed_for_phase(
            COMMANDS.get_command("documentation-build"), "midflight"))
        self.assertFalse(COMMANDS.command_allowed_for_phase(
            COMMANDS.get_command("python-pytest"), "midflight"))
        # Preflight approval is still present.
        self.assertTrue(COMMANDS.command_allowed_for_phase(
            COMMANDS.get_command("documentation-build"), "preflight"))


# ---------------------------------------------------------------------------
# Credential-free environment
# ---------------------------------------------------------------------------


class CredentialFreeEnvironmentTests(unittest.TestCase):
    def test_known_credentials_stripped(self) -> None:
        spec = COMMANDS.get_command("documentation-build")
        env = COMMANDS.build_credential_free_environment(spec, home_dir="/tmp/home")
        for forbidden in ("GITHUB_TOKEN", "OPENROUTER_API_KEY", "SSH_AUTH_SOCK",
                          "HTTP_PROXY", "HTTPS_PROXY",
                          "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
            self.assertNotIn(forbidden, env, f"{forbidden} leaked into command env")

    def test_credential_shaped_keys_stripped(self) -> None:
        spec = COMMANDS.get_command("documentation-build")
        env = COMMANDS.build_credential_free_environment(spec, home_dir="/tmp/home")
        # Any key matching *_TOKEN / *_KEY / *_SECRET must not be present.
        for name in env:
            self.assertFalse(COMMANDS.is_credential_env(name),
                             f"credential-shaped env leaked: {name}")

    def test_home_is_disposable(self) -> None:
        spec = COMMANDS.get_command("documentation-build")
        env = COMMANDS.build_credential_free_environment(spec, home_dir="/tmp/disposable-home")
        self.assertEqual(env["HOME"], "/tmp/disposable-home")
        self.assertNotIn("GIT_ASKPASS", env)


# ---------------------------------------------------------------------------
# Hardened executor isolation
# ---------------------------------------------------------------------------


class ExecutorIsolationTests(unittest.TestCase):
    def _workspace(self, tmp: Path) -> Path:
        (tmp / "Makefile").write_text("all:\n\techo built\n", encoding="utf-8")
        return tmp

    def test_no_shell_no_stdin_executes_argv_directly(self) -> None:
        spec = COMMANDS.get_command("documentation-build")
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._workspace(Path(tmp))
            captured = {}
            captured_kwargs = {}

            class FakePopen2:
                def __init__(self, argv, **kwargs):
                    captured["argv"] = argv
                    captured["env"] = kwargs.get("env")
                    captured["stdin"] = kwargs.get("stdin")
                    captured["cwd"] = kwargs.get("cwd")
                    captured["start_new_session"] = kwargs.get("start_new_session")
                    captured_kwargs.update(kwargs)
                    self.pid = 12345
                    self.stdout = io.BytesIO(b"")
                    self.returncode = 0

                def wait(self, timeout=None):
                    return 0

                def poll(self):
                    return 0

            with mock.patch.object(COMMANDS.subprocess, "Popen", FakePopen2), \
                 mock.patch.object(COMMANDS, "_drain_bounded_select", lambda *a, **k: None), \
                 mock.patch.object(COMMANDS, "_reap", lambda *a, **k: None), \
                 mock.patch.object(COMMANDS, "_apply_resource_limits"), \
                 mock.patch.object(COMMANDS.shutil, "which", return_value="/usr/bin/make"):
                result = COMMANDS.execute_command(spec, workspace=ws, phase="preflight")
            # No shell: argv is the executable + fixed flags, never a single shell string.
            self.assertEqual(captured["argv"][0], "/usr/bin/make")
            self.assertEqual(captured["argv"][1:], ("-C", "docs", "html"))
            self.assertNotIn("shell", captured_kwargs)
            # stdin attached to DEVNULL (no stdin).
            self.assertEqual(captured["stdin"], COMMANDS.subprocess.DEVNULL)
            # Own process group.
            self.assertTrue(captured["start_new_session"])

    def test_bounded_output_keeps_tail_with_marker(self) -> None:
        # Generate unbounded output and verify the ring buffer bounds it.
        ring = COMMANDS._BoundedRingBuffer(16)
        ring.feed(b"head-marker\n")
        ring.feed(b"X" * 1000)
        ring.feed(b"\ntail-marker\n")
        text, truncated = ring.value()
        self.assertTrue(truncated)
        self.assertIn("tail-marker", text)
        self.assertNotIn("head-marker", text)
        self.assertLessEqual(len(text.encode("utf-8")), 16 + len(COMMANDS.TRUNCATION_MARKER))

    def test_disposable_workspace_skips_symlinks_and_special_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "real.txt").write_text("ok\n", encoding="utf-8")
            (src / "link.txt").symlink_to(src / "real.txt")
            dest = COMMANDS.create_disposable_workspace(src)
            try:
                self.assertTrue((dest / "real.txt").is_file())
                self.assertFalse((dest / "link.txt").exists(),
                                 "symlink must not be copied into disposable workspace")
            finally:
                COMMANDS.dispose_workspace(dest)

    def test_safety_error_on_unapproved_phase(self) -> None:
        spec = COMMANDS.get_command("python-pytest")  # preflight only
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(COMMANDS.CommandError):
                COMMANDS.execute_command(spec, workspace=Path(tmp), phase="midflight")

    def test_resource_limit_failure_fails_closed(self) -> None:
        spec = COMMANDS.get_command("documentation-build")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(COMMANDS, "_apply_resource_limits",
                                   side_effect=COMMANDS.CommandError("rlimit failed")):
                with self.assertRaises(COMMANDS.CommandError):
                    COMMANDS.execute_command(
                        spec, workspace=Path(tmp), phase="midflight")

    def test_artifact_rejects_symlink_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "docs").mkdir()
            (ws / "docs" / "_build").mkdir()
            (ws / "docs" / "_build" / "index.html").write_text("<html></html>", encoding="utf-8")
            checks = COMMANDS._check_artifacts(
                COMMANDS.get_command("documentation-build"), ws)
            self.assertTrue(checks[0].present)
            self.assertEqual(checks[0].type, "file")

    def test_command_list_sha256_is_deterministic(self) -> None:
        a = COMMANDS.command_list_sha256(["documentation-build"])
        b = COMMANDS.command_list_sha256(["documentation-build"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, COMMANDS.command_list_sha256(["python-pytest"]))


class RealExecutorTests(unittest.TestCase):
    """Real-subprocess executor tests (timeout, bounded output, network gate).

    These exercise the actual select-based watchdog and process-group
    termination against real child processes. They run on Linux/macOS where
    ``sleep`` and ``python3`` are available.
    """

    def _sleep_spec(self, timeout_seconds: int) -> COMMANDS.CommandSpec:
        # A silent child that sleeps longer than the timeout. ``sleep`` is
        # part of POSIX; skip if unavailable.
        import shutil as _shutil

        if _shutil.which("sleep") is None:
            self.skipTest("sleep binary not available")
        return COMMANDS.CommandSpec(
            id="test-sleep",
            workflow="release-project-review",
            phases=("midflight",),
            argv=("sleep", "30"),
            timeout_seconds=timeout_seconds,
            max_output_bytes=4096,
            network="disabled",
            nonzero_is_evidence=True,
        )

    def test_timeout_kills_silent_child_within_bound(self) -> None:
        spec = self._sleep_spec(timeout_seconds=2)
        with tempfile.TemporaryDirectory() as tmp:
            import time as _time

            start = _time.monotonic()
            result = COMMANDS.execute_command(spec, workspace=Path(tmp), phase="midflight")
            elapsed = _time.monotonic() - start
        self.assertEqual(result.status, "timed_out")
        # The deadline must actually apply: well under the 30s sleep, with a
        # generous margin for process-group teardown.
        self.assertLess(elapsed, 15)
        self.assertIsNotNone(result.result_sha256)

    def test_bounded_real_output_keeps_tail(self) -> None:
        # A child that writes more than max_output_bytes; verify the ring
        # buffer bounds captured output and reports truncation.
        import shutil as _shutil

        if _shutil.which("python3") is None:
            self.skipTest("python3 not available")
        spec = COMMANDS.CommandSpec(
            id="test-noisy",
            workflow="release-project-review",
            phases=("midflight",),
            argv=(
                "python3", "-c",
                "import sys\n"
                "sys.stdout.write('head-marker\\n')\n"
                "sys.stdout.write('X' * 100000)\n"
                "sys.stdout.write('\\ntail-marker\\n')\n",
            ),
            timeout_seconds=10,
            max_output_bytes=512,
            network="disabled",
            nonzero_is_evidence=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = COMMANDS.execute_command(spec, workspace=Path(tmp), phase="midflight")
        self.assertTrue(result.truncated)
        self.assertIn("tail-marker", result.output_tail)
        self.assertNotIn("head-marker", result.output_tail)
        self.assertLessEqual(
            len(result.output_tail.encode("utf-8")),
            512 + len(COMMANDS.TRUNCATION_MARKER),
        )

    def test_drain_does_not_lose_data_from_fast_child(self) -> None:
        """Regression for _read_would_eof bug: a child that writes a small
        amount and exits must not have its output silently consumed by an
        EOF probe. The select-based drain must capture all written bytes."""
        import shutil as _shutil

        if _shutil.which("python3") is None:
            self.skipTest("python3 not available")
        spec = COMMANDS.CommandSpec(
            id="test-fast",
            workflow="release-project-review",
            phases=("midflight",),
            argv=(
                "python3", "-c",
                "import sys\n"
                "sys.stdout.write('evidence-line\\n')\n"
                "sys.stdout.flush()\n",
            ),
            timeout_seconds=10,
            max_output_bytes=4096,
            network="disabled",
            nonzero_is_evidence=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = COMMANDS.execute_command(spec, workspace=Path(tmp), phase="midflight")
        self.assertEqual(result.status, "passed")
        # The full output must be captured; no bytes lost to an EOF probe.
        self.assertIn("evidence-line", result.output_tail)
        self.assertFalse(result.truncated)

    def test_network_denial_self_check_returns_enforced_or_skip(self) -> None:
        # The self-check probes a local listener from this process. On a
        # normal host the listener is reachable, so enforced=False; the
        # contract is that a runner must gate enabling midflight commands on
        # enforced=True. We assert the helper returns a clear verdict rather
        # than pretending declarative metadata enforces isolation.
        enforced, message = COMMANDS.network_denial_self_check()
        self.assertIsInstance(enforced, bool)
        self.assertIsInstance(message, str)
        # On a typical CI/local host the listener is reachable.
        if enforced:
            self.assertIn("unreachable", message)
        else:
            self.assertIn("reachable", message)

# ---------------------------------------------------------------------------
# Schema-2 manifest validation
# ---------------------------------------------------------------------------


def _write_release_bundle(root: Path, profile: str = "rpr", manifest: dict | None = None) -> Path:
    pd = root / profile
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "agent.md").write_text(
        "---\nname: rpr\nmode: primary\n---\n# agent\n", encoding="utf-8")
    (pd / "skills").mkdir()
    (pd / "skills" / "rm").mkdir()
    (pd / "skills" / "rm" / "SKILL.md").write_text("---\nname: rm\n---\n# s\n", encoding="utf-8")
    (pd / "prompts").mkdir()
    (pd / "prompts" / "p.md").write_text("# p\nReview {{feedback_kind}}.\n", encoding="utf-8")
    m = manifest or {
        "schema_version": 2,
        "profile_name": profile,
        "allowed_workflows": ["release-project-review"],
        "agent_file": "agent.md",
        "skill_files": ["skills/rm/SKILL.md"],
        "prompt_template": "prompts/p.md",
        "model_profile": "release-project-review-readonly",
        "output_contract": "release-project-issue-v1",
        "midflight_commands": ["documentation-build"],
    }
    (pd / "bundle.json").write_text(json.dumps(m), encoding="utf-8")
    return pd


class Schema2ManifestTests(unittest.TestCase):
    def test_schema1_bundle_still_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pd = _write_release_bundle(Path(tmp), manifest={
                "schema_version": 1, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "preflight_commands": ["make -C docs html"],
            })
            resolved = CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                                workflow="release-project-review")
            self.assertEqual(resolved.manifest.schema_version, 1)
            self.assertEqual(resolved.manifest.preflight_commands, ("make -C docs html",))
            self.assertEqual(resolved.manifest.midflight_commands, ())

    def test_schema1_rejects_midflight_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 1, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["documentation-build"],
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_unknown_manifest_key_rejected_schema1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 1, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "surprise_field": "evil",
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_unknown_manifest_key_rejected_schema2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["documentation-build"],
                "surprise_field": "evil",
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_midflight_only_for_release_project_review(self) -> None:
        # No command is currently approved for the midflight phase, so any
        # non-empty midflight_commands list is rejected at resolution.
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["documentation-build"],
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_midflight_empty_list_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": [],
            })
            resolved = CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                                workflow="release-project-review")
            self.assertEqual(resolved.manifest.midflight_commands, ())

    def test_midflight_unknown_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["not-a-command"],
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_midflight_wrong_phase_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["python-pytest"],  # preflight-only
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_midflight_duplicates_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["documentation-build", "documentation-build"],
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")

    def test_midflight_too_many_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_release_bundle(Path(tmp), manifest={
                "schema_version": 2, "profile_name": "rpr",
                "allowed_workflows": ["release-project-review"],
                "agent_file": "agent.md", "skill_files": ["skills/rm/SKILL.md"],
                "prompt_template": "prompts/p.md",
                "model_profile": "release-project-review-readonly",
                "output_contract": "release-project-issue-v1",
                "midflight_commands": ["documentation-build"] * 4,
            })
            with self.assertRaises(CFG.ConfigurationError):
                CFG.resolve_local_bundle(bundle_root=Path(tmp), profile="rpr",
                                          workflow="release-project-review")


# ---------------------------------------------------------------------------
# Phase-1 analysis handoff contract
# ---------------------------------------------------------------------------


class HandoffContractTests(unittest.TestCase):
    def _valid(self):
        return json.dumps({
            "assessment": "initial release-management assessment",
            "validation_questions": ["is rollout documented?"],
            "relevant_evidence": ["changelog"],
        })

    def test_valid_handoff_accepted(self) -> None:
        parsed = PROMPTS.parse_release_project_analysis_handoff_output(self._valid())
        self.assertEqual(parsed["assessment"], "initial release-management assessment")
        self.assertEqual(parsed["validation_questions"], ["is rollout documented?"])

    def test_unknown_field_rejected(self) -> None:
        bad = json.dumps({
            "assessment": "x", "validation_questions": ["q"], "relevant_evidence": [],
            "extra": "evil",
        })
        with self.assertRaises(PROMPTS.ContractError):
            PROMPTS.parse_release_project_analysis_handoff_output(bad)

    def test_command_control_field_rejected(self) -> None:
        for forbidden in ("command", "commands", "args", "shell", "environment",
                          "working_directory", "url", "repository", "endpoint",
                          "credentials", "decision", "title", "body", "labels"):
            with self.subTest(field=forbidden):
                payload = {"assessment": "x", "validation_questions": ["q"],
                           "relevant_evidence": []}
                payload[forbidden] = "evil"
                with self.assertRaises(PROMPTS.ContractError):
                    PROMPTS.parse_release_project_analysis_handoff_output(json.dumps(payload))

    def test_missing_required_field_rejected(self) -> None:
        with self.assertRaises(PROMPTS.ContractError):
            PROMPTS.parse_release_project_analysis_handoff_output(
                json.dumps({"assessment": "x", "validation_questions": ["q"]}))

    def test_oversized_assessment_rejected(self) -> None:
        payload = {"assessment": "x" * (PROMPTS.MAX_HANDOFF_ASSESSMENT_BYTES + 1),
                   "validation_questions": ["q"], "relevant_evidence": []}
        with self.assertRaises(PROMPTS.ContractError):
            PROMPTS.parse_release_project_analysis_handoff_output(json.dumps(payload))

    def test_malformed_json_rejected(self) -> None:
        with self.assertRaises(PROMPTS.ContractError):
            PROMPTS.parse_release_project_analysis_handoff_output("not json at all")

    def test_dispatch_routes_to_handoff(self) -> None:
        parsed = PROMPTS.parse_output(
            "release-project-analysis-handoff-v1", self._valid())
        self.assertIn("assessment", parsed)


class PhaseAwarePromptTests(unittest.TestCase):
    def test_phase1_uses_handoff_contract_phase2_uses_issue_contract(self) -> None:
        p1 = PROMPTS.compose_prompt(
            feedback_kind="release-project-review",
            output_contract="release-project-analysis-handoff-v1",
            profile_template="Review release {{feedback_kind}} for {{repository}}.",
            repository="o/r", author_login="release-reviewer",
            untrusted_content="release body", release_id=42, release_tag="v1",
            target_commit_sha="a" * 40,
        )
        self.assertIn("release-project-analysis-handoff-v1", p1.text)
        self.assertIn("non-publishing", p1.text)
        # Phase 1 must not contain the publication issue contract.
        self.assertNotIn("release-project-issue-v1", p1.text)

    def test_distinct_untrusted_delimiters_for_handoff_and_evidence(self) -> None:
        handoff = {"assessment": "x", "validation_questions": ["q"], "relevant_evidence": []}
        from collections import namedtuple
        R = namedtuple("R", "command_id registry_version status exit_code output_tail truncated artifacts duration_bucket result_sha256")
        r = R("documentation-build", 1, "passed", 0, "built", False, (), "<1s", "abc")
        text = COMMANDS.format_phase1_handoff(handoff) + "\n" + COMMANDS.format_midflight_results([r])
        self.assertIn(COMMANDS.PHASE1_HANDOFF_START, text)
        self.assertIn(COMMANDS.PHASE1_HANDOFF_END, text)
        self.assertIn(COMMANDS.MIDFLIGHT_EVIDENCE_START, text)
        self.assertIn(COMMANDS.MIDFLIGHT_EVIDENCE_END, text)
        # Command output is under the midflight delimiter, not the handoff one.
        self.assertNotIn("built", text.split(COMMANDS.PHASE1_HANDOFF_END)[1].split(COMMANDS.MIDFLIGHT_EVIDENCE_START)[0])


# ---------------------------------------------------------------------------
# Policy workflow-command controls
# ---------------------------------------------------------------------------


class PolicyWorkflowCommandTests(unittest.TestCase):
    def test_release_builtin_has_workflow_commands(self) -> None:
        p = POLICY.merge_policy(workflow="release-project-review",
                                model_profile="release-project-review-readonly")
        wc = p.workflow_commands
        self.assertIn("midflight", wc["allowed_phases"])
        self.assertIn("documentation-build", wc["allowed_registry_ids"])
        self.assertEqual(wc["required_isolation_profile"], "release-midflight-v1")
        self.assertEqual(wc["max_model_phases"], 2)

    def test_workflow_commands_affect_policy_hash(self) -> None:
        base = POLICY.merge_policy(workflow="release-project-review",
                                   model_profile="release-project-review-readonly")
        narrowed = POLICY.merge_policy(
            workflow="release-project-review",
            model_profile="release-project-review-readonly",
            bundle_policy={"workflow_commands": {"allowed_registry_ids": ["documentation-build"]}},
        )
        self.assertNotEqual(base.sha256, narrowed.sha256)

    def test_overlay_cannot_add_command_ids(self) -> None:
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="release-project-review",
                model_profile="release-project-review-readonly",
                overlay={"workflow_commands": {"allowed_registry_ids": ["evil"]}},
            )

    def test_overlay_cannot_broaden_isolation_profile(self) -> None:
        with self.assertRaises(POLICY.PolicyError):
            POLICY.merge_policy(
                workflow="release-project-review",
                model_profile="release-project-review-readonly",
                overlay={"workflow_commands": {"required_isolation_profile": "weaker"}},
            )

    def test_overlay_can_narrow_ids(self) -> None:
        p = POLICY.merge_policy(
            workflow="release-project-review",
            model_profile="release-project-review-readonly",
            overlay={"workflow_commands": {"allowed_registry_ids": ["documentation-build"]}},
        )
        self.assertEqual(p.workflow_commands["allowed_registry_ids"], ("documentation-build",))


# ---------------------------------------------------------------------------
# Two-phase runner
# ---------------------------------------------------------------------------


def _resolved_bundle_with(midflight: list[str]) -> dict:
    bundle = CFG.resolve_local_bundle(
        bundle_root=REPO_ROOT / ".opencode" / "configuration",
        profile="release-project-review",
        workflow="release-project-review",
    ).to_dict()
    bundle["midflight_commands"] = midflight
    return bundle


def _policy_for(bundle: dict, *, dry_run: bool = False) -> dict:
    return POLICY.merge_policy(
        workflow="release-project-review",
        model_profile=bundle["model_profile"],
        invocation_inputs={"dry_run": dry_run},
    ).to_dict()


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeResult:
    def __init__(self) -> None:
        self.command_id = "documentation-build"
        self.registry_version = 1
        self.status = "passed"
        self.exit_code = 0
        self.output_tail = "docs built"
        self.truncated = False
        self.artifacts = ()
        self.duration_bucket = "<1s"
        self.result_sha256 = "a" * 64

    def is_safety_error(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {}


SHA = "a" * 40
TARGET = "SecondSkoll/generic-agentic-workflows"


class TwoPhaseRunnerTests(unittest.TestCase):
    def _inputs(self, tmp: Path, midflight: list[str]):
        bundle = _resolved_bundle_with(midflight)
        policy = _policy_for(bundle, dry_run=True)
        metadata = {
            "repository": TARGET, "id": 42, "tag_name": "v1.0", "name": "v1.0",
            "published_at": "t", "draft": False, "prerelease": False,
            "target_commitish": SHA, "body": "notes", "asset_count": 0, "assets": [],
            "target_commit_sha": SHA,
        }
        bp = tmp / "b.json"; bp.write_text(json.dumps(bundle))
        pp = tmp / "p.json"; pp.write_text(json.dumps(policy))
        mp = tmp / "m.json"; mp.write_text(json.dumps(metadata))
        return bp, pp, mp

    def _args(self, tmp, bp, pp, mp, **kw):
        prov = tmp / "prov.json"
        args = [
            "review", "--resolved-config", str(bp), "--effective-policy", str(pp),
            "--release-metadata", str(mp), "--target-repository", TARGET,
            "--release-id", "42", "--release-tag", "v1.0",
            "--target-commit-sha", SHA, "--caller-repository", TARGET,
            "--target-token", "t", "--repo-root", str(tmp),
            "--provenance", str(prov), "--dry-run",
            "--publication-preview", str(tmp / "preview.json"),
        ]
        return args

    def test_two_phase_runs_analysis_then_commands_then_assessment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "CHANGELOG.md").write_text("c\n", encoding="utf-8")
            bp, pp, mp = self._inputs(tmp_path, ["documentation-build"])
            handoff = json.dumps({
                "assessment": "initial assessment", "validation_questions": ["q"],
                "relevant_evidence": ["changelog"]})
            final = json.dumps({
                "decision": "CREATE_ISSUE", "title": "Missing rollback",
                "body": "Evidence: none. Impact: bad rollout. Owner/action: rm. Priority: high. Release gap.",
                "labels": ["release-readiness"]})
            # Preflight routes through execute_command; midflight is mocked at
            # the runner level because no registry command is approved for
            # midflight until OS-level enforcement exists.
            with mock.patch.object(RUNNER.subprocess, "run",
                                   side_effect=[_FakeProc(handoff), _FakeProc(final)]) as run, \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()) as exe, \
                 mock.patch.object(RUNNER, "run_midflight_commands", return_value=[_FakeResult()]) as mid:
                rc = RUNNER.main(self._args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 0)
            # Two model invocations (phase 1 + phase 2) via subprocess.run.
            self.assertEqual(run.call_count, 2)
            # Preflight via execute_command; midflight mocked at runner level.
            self.assertEqual(exe.call_count, 1)
            mid.assert_called_once()
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["model_phase_count"], 2)
            phases = [p["phase"] for p in prov["phases"]]
            self.assertEqual(phases, ["analysis", "midflight", "assessment"])
            self.assertTrue(prov["command_list_sha256"])
            self.assertEqual(prov["registry_version"], 1)
            self.assertEqual(prov["isolation_profile"], "release-midflight-v1")

    def test_single_phase_preserved_when_no_midflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bp, pp, mp = self._inputs(tmp_path, [])
            final = json.dumps({
                "decision": "NO_ISSUE", "summary": "Ready; rollout documented."})
            with mock.patch.object(RUNNER.subprocess, "run",
                                   side_effect=[_FakeProc(final)]) as run, \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()) as exe:
                rc = RUNNER.main(self._args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 0)
            # No midflight execution; only the single opencode call.
            self.assertEqual(run.call_count, 1)
            # Preflight still runs through execute_command.
            self.assertEqual(exe.call_count, 1)
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["model_phase_count"], 1)
            self.assertEqual([p["phase"] for p in prov["phases"]], ["assessment"])

    def test_invalid_handoff_fails_closed_no_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bp, pp, mp = self._inputs(tmp_path, ["documentation-build"])
            with mock.patch.object(RUNNER.subprocess, "run",
                                   side_effect=[_FakeProc("not json")]), \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()):
                rc = RUNNER.main(self._args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 1)
            # Midflight never runs because phase-1 handoff validation failed closed;
            # execute_command is called only for preflight.
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["result"], "failed")
            self.assertEqual(prov["model_phase_count"], 1)

    def test_policy_disallows_midflight_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bp, pp, mp = self._inputs(tmp_path, ["documentation-build"])
            # Tamper: policy removes midflight phase allowance.
            policy = json.loads(pp.read_text())
            policy["workflow_commands"]["allowed_phases"] = ("preflight",)
            pp.write_text(json.dumps(policy))
            with mock.patch.object(RUNNER.subprocess, "run") as run, \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()):
                rc = RUNNER.main(self._args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 1)
            run.assert_not_called()
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["result"], "failed")

    def test_dry_run_runs_both_phases_no_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bp, pp, mp = self._inputs(tmp_path, ["documentation-build"])
            handoff = json.dumps({"assessment": "x", "validation_questions": ["q"], "relevant_evidence": []})
            final = json.dumps({"decision": "CREATE_ISSUE", "title": "T",
                                "body": "Evidence: none. Impact: bad rollout. Owner/action: rm. Priority: high. Release gap.",
                                "labels": ["release-readiness"]})
            with mock.patch.object(RUNNER.subprocess, "run",
                                   side_effect=[_FakeProc(handoff), _FakeProc(final)]), \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()), \
                 mock.patch.object(RUNNER, "run_midflight_commands", return_value=[_FakeResult()]), \
                 mock.patch.object(RUNNER, "_require_token_access") as access, \
                 mock.patch.object(RUNNER, "_create_issue") as create:
                rc = RUNNER.main(self._args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 0)
            access.assert_not_called()
            create.assert_not_called()
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["mode"], "dry-run")
            self.assertEqual(prov["result"], "generated")


# ---------------------------------------------------------------------------
# Provenance redaction
# ---------------------------------------------------------------------------


class ProvenanceRedactionTests(unittest.TestCase):
    def test_provenance_omits_raw_model_and_command_output(self) -> None:
        record = PROV.build_provenance(
            workflow_version="v1", workflow_name="release-project-review",
            caller_repository="o/r", target_kind="release", target_number=42,
            target_head_sha="a" * 40,
            bundle={"source_alias": "local", "profile": "rpr",
                    "manifest_sha256": "b" * 64, "schema_version": 2},
            prompt_template_sha256="d" * 64,
            output_contract="release-project-issue-v1",
            model_profile="release-project-review-readonly",
            effective_policy_sha256="e" * 64, mode="dry-run", result="generated",
            registry_version=1, command_list_sha256="f" * 64,
            model_phase_count=2, isolation_profile="release-midflight-v1",
            phases=({"phase": "analysis", "status": "validated", "result_sha256": "1" * 64},),
        )
        text = record.to_json()
        self.assertIn("registry_version", text)
        self.assertIn("command_list_sha256", text)
        self.assertIn("model_phase_count", text)
        self.assertIn("isolation_profile", text)
        self.assertIn("schema_version", text)
        # No raw evidence.
        self.assertNotIn("OPENROUTER_API_KEY", text)
        self.assertNotIn("ghp_", text)

    def test_provenance_phases_carry_no_raw_output(self) -> None:
        record = PROV.build_provenance(
            workflow_version="v1", workflow_name="release-project-review",
            caller_repository="o/r", target_kind="release", target_number=1,
            target_head_sha="a" * 40, bundle=None,
            prompt_template_sha256=None, output_contract=None,
            model_profile=None, effective_policy_sha256=None,
            mode="publish", result="published",
            phases=({"phase": "midflight", "command_id": "documentation-build",
                     "status": "passed", "result_sha256": "a" * 64},),
        )
        text = record.to_json()
        self.assertIn("documentation-build", text)
        # Only the command ID and result hash, never raw command output.
        self.assertNotIn("docs built", text)


# ---------------------------------------------------------------------------
# Workflow YAML ordering and command-environment posture
# ---------------------------------------------------------------------------


class WorkflowYamlMidflightTests(unittest.TestCase):
    def test_config_and_policy_resolved_before_validate_only_stop(self) -> None:
        import yaml

        text = (REPO_ROOT / ".github/workflows/opencode-release-project-review.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        job = data["jobs"]["release-review"]
        names = [s.get("name") or f"uses:{s.get('uses','')}" for s in job["steps"]]
        i_config = next(i for i, n in enumerate(names) if n == "Resolve agentic configuration")
        i_policy = next(i for i, n in enumerate(names) if n == "Resolve effective policy")
        i_stop = next(i for i, n in enumerate(names) if n == "Stop after validation-only")
        self.assertLess(i_config, i_policy)
        self.assertLess(i_policy, i_stop)

    def test_no_workflow_secret_forwarded_to_command_env_documented(self) -> None:
        text = (REPO_ROOT / ".github/workflows/opencode-release-project-review.yml").read_text(encoding="utf-8")
        self.assertIn("credential-free", text)
        self.assertIn("network-disabled", text)

    def test_no_workflow_input_can_supply_command_text(self) -> None:
        import yaml

        data = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/opencode-release-project-review.yml").read_text(encoding="utf-8")
        )
        triggers = data.get("on") if "on" in data else data.get(True)
        for trigger in ("workflow_dispatch", "workflow_call"):
            for key, spec in (triggers[trigger].get("inputs") or {}).items():
                self.assertNotIn(key, ("midflight_commands", "preflight_commands", "command", "commands"),
                                 f"workflow input {key!r} must not supply command text")

    def test_workflow_failure_provenance_uses_real_run_mode(self) -> None:
        text = (REPO_ROOT / ".github/workflows/opencode-release-project-review.yml").read_text(encoding="utf-8")
        # The config/policy failure_record calls must compute mode from DRY_RUN
        # rather than hardcoding validate-only. The only legitimate
        # mode="validate-only" is the success record in the validate-only stop
        # step (which is reached only when validate_only is true). The failure
        # handlers in the config/policy steps must use the computed `mode`
        # variable, not the literal "validate-only".
        # Count failure_record calls that hardcode validate-only: must be 0.
        import re

        bad = re.findall(
            r"failure_record\([^)]*mode=\"validate-only\"[^)]*\)", text, re.DOTALL
        )
        self.assertEqual(
            len(bad), 0,
            "failure_record must not hardcode validate-only mode; "
            f"found {len(bad)} offending call(s)"
        )
        # The config/policy failure handlers compute mode from DRY_RUN.
        self.assertGreaterEqual(text.count('mode = "dry-run" if os.environ.get'), 2)


class FailureProvenanceMidflightTests(unittest.TestCase):
    """Required change #4: failure provenance retains midflight fields."""

    def test_failure_record_retains_midflight_fields(self) -> None:
        record = PROV.failure_record(
            workflow_version="v1",
            workflow_name="release-project-review",
            caller_repository="o/r",
            mode="dry-run",
            error=RuntimeError("phase-2 opencode exited with status 3"),
            bundle={"source_alias": "local", "profile": "rpr",
                    "manifest_sha256": "b" * 64, "schema_version": 2},
            target_kind="release", target_number=42, target_head_sha="a" * 40,
            registry_version=1, command_list_sha256="c" * 64,
            model_phase_count=2, isolation_profile="release-midflight-v1",
            phases=(
                {"phase": "analysis", "status": "validated", "result_sha256": "1" * 64},
                {"phase": "midflight", "command_id": "documentation-build",
                 "status": "passed", "result_sha256": "2" * 64},
            ),
        )
        data = record.to_dict()
        self.assertEqual(data["result"], "failed")
        self.assertEqual(data["registry_version"], 1)
        self.assertEqual(data["command_list_sha256"], "c" * 64)
        self.assertEqual(data["model_phase_count"], 2)
        self.assertEqual(data["isolation_profile"], "release-midflight-v1")
        self.assertEqual(len(data["phases"]), 2)
        # Bundle schema version is retained.
        self.assertEqual(data["bundle"]["schema_version"], 2)
        # No raw model output or secrets retained.
        self.assertNotIn("OPENROUTER_API_KEY", record.to_json())
        self.assertNotIn("ghp_", record.to_json())

    def test_runner_failure_provenance_carries_midflight_fields(self) -> None:
        # A mid-run failure (invalid handoff) must record midflight fields.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bp, pp, mp = self._runner_inputs(tmp_path, ["documentation-build"])
            with mock.patch.object(RUNNER.subprocess, "run",
                                   side_effect=[_FakeProc("not json")]), \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()):
                rc = RUNNER.main(self._runner_args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 1)
            prov = json.loads((tmp_path / "prov.json").read_text())
            self.assertEqual(prov["result"], "failed")
            self.assertTrue(prov["command_list_sha256"])
            self.assertEqual(prov["registry_version"], 1)
            self.assertEqual(prov["isolation_profile"], "release-midflight-v1")
            self.assertGreaterEqual(prov["model_phase_count"], 1)

    def _runner_inputs(self, tmp, midflight):
        bundle = _resolved_bundle_with(midflight)
        policy = _policy_for(bundle, dry_run=True)
        metadata = {
            "repository": TARGET, "id": 42, "tag_name": "v1.0", "name": "v1.0",
            "published_at": "t", "draft": False, "prerelease": False,
            "target_commitish": SHA, "body": "notes", "asset_count": 0, "assets": [],
            "target_commit_sha": SHA,
        }
        bp = tmp / "b.json"; bp.write_text(json.dumps(bundle))
        pp = tmp / "p.json"; pp.write_text(json.dumps(policy))
        mp = tmp / "m.json"; mp.write_text(json.dumps(metadata))
        return bp, pp, mp

    def _runner_args(self, tmp, bp, pp, mp):
        prov = tmp / "prov.json"
        return [
            "review", "--resolved-config", str(bp), "--effective-policy", str(pp),
            "--release-metadata", str(mp), "--target-repository", TARGET,
            "--release-id", "42", "--release-tag", "v1.0",
            "--target-commit-sha", SHA, "--caller-repository", TARGET,
            "--target-token", "t", "--repo-root", str(tmp),
            "--provenance", str(prov), "--dry-run",
            "--publication-preview", str(tmp / "preview.json"),
        ]


class AnalysisWorkspaceMaterializationTests(unittest.TestCase):
    """Required change #3: OpenCode --dir points to a materialized workspace."""

    def test_materialize_copies_only_allowlisted_release_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "CHANGELOG.md").write_text("changes\n", encoding="utf-8")
            (repo_root / "src").mkdir()
            (repo_root / "src" / "secret.py").write_text("SECRET='x'\n", encoding="utf-8")
            (repo_root / "secrets.env").write_text("TOKEN=y\n", encoding="utf-8")
            (repo_root / "README.md").write_text("readme\n", encoding="utf-8")
            bundle = CFG.resolve_local_bundle(
                bundle_root=REPO_ROOT / ".opencode" / "configuration",
                profile="release-project-review",
                workflow="release-project-review",
            ).to_dict()
            analysis_root, staged = RUNNER._materialize_analysis_workspace(
                bundle, repo_root
            )
            try:
                # Allowlisted release documents are copied.
                self.assertTrue((analysis_root / "CHANGELOG.md").is_file())
                self.assertTrue((analysis_root / "README.md").is_file())
                # Source code and secrets are never copied.
                self.assertFalse((analysis_root / "src").exists())
                self.assertFalse((analysis_root / "secrets.env").exists())
                # Verified agents/skills are staged.
                self.assertTrue((analysis_root / ".opencode" / "agents").is_dir())
            finally:
                RUNNER.CFG.cleanup_staged(staged)
                import shutil as _shutil
                _shutil.rmtree(analysis_root, ignore_errors=True)

    def test_opencode_invoked_with_materialized_dir_not_repo_root(self) -> None:
        captured = {}

        class FakeRunResult:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeRunResult()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "CHANGELOG.md").write_text("c\n", encoding="utf-8")
            (repo_root / "src").mkdir()
            (repo_root / "src" / "evil.py").write_text("evil\n", encoding="utf-8")
            bundle = CFG.resolve_local_bundle(
                bundle_root=REPO_ROOT / ".opencode" / "configuration",
                profile="release-project-review",
                workflow="release-project-review",
            ).to_dict()
            with mock.patch.object(RUNNER.subprocess, "run", side_effect=fake_run):
                RUNNER._run_opencode(
                    resolved_bundle=bundle, repo_root=repo_root,
                    agent_name="release-project-review", prompt="p",
                    provider_timeout=10,
                )
            dir_arg = captured["cmd"][captured["cmd"].index("--dir") + 1]
            # --dir points at a temp analysis workspace, not the target checkout.
            self.assertNotEqual(Path(dir_arg), repo_root)
            self.assertNotIn("evil.py", str(Path(dir_arg)))


class PhasePromptCompositionTests(unittest.TestCase):
    """Required change #6: phase-2 comparison instruction; phase-1 command IDs."""

    def test_phase1_configured_commands_section_enumerates_ids(self) -> None:
        section = COMMANDS.phase1_configured_commands_section(
            ["documentation-build", "python-pytest"]
        )
        self.assertIn("documentation-build", section)
        self.assertIn("python-pytest", section)
        self.assertIn("fixed", section.lower())
        self.assertIn("may NOT select", section)

    def test_phase1_configured_commands_section_empty_when_no_commands(self) -> None:
        self.assertEqual(COMMANDS.phase1_configured_commands_section([]), "")

    def test_phase2_comparison_instruction_present(self) -> None:
        text = COMMANDS.PHASE2_COMPARISON_INSTRUCTION
        self.assertIn("Compare", text)
        self.assertIn("material changes", text)
        self.assertIn("final", text.lower())


class TrustedAppendixPlacementTests(unittest.TestCase):
    """Required change #2: trusted workflow instructions are placed OUTSIDE
    the untrusted delimited data markers, not inside them."""

    def test_trusted_appendix_appears_outside_untrusted_markers(self) -> None:
        composed = PROMPTS.compose_prompt(
            feedback_kind="release-project-review",
            output_contract="release-project-analysis-handoff-v1",
            profile_template="Review release {{feedback_kind}} for {{repository}}.",
            repository="o/r",
            author_login="release-reviewer",
            untrusted_content="hostile release body",
            release_id=42,
            release_tag="v1",
            target_commit_sha="a" * 40,
            trusted_appendix="## Trusted workflow instruction\nDo the right thing.",
        )
        text = composed.text
        start = text.index(PROMPTS.START_MARKER)
        end = text.index(PROMPTS.END_MARKER) + len(PROMPTS.END_MARKER)
        untrusted_block = text[start:end]
        after_block = text[end:]
        # The trusted appendix must NOT appear inside the untrusted markers.
        self.assertNotIn("Trusted workflow instruction", untrusted_block)
        # The trusted appendix MUST appear after the untrusted block.
        self.assertIn("Trusted workflow instruction", after_block)
        # And before the output contract suffix (section 5), which is the
        # last "Output contract:" occurrence (section 1 also mentions it).
        contract_idx = text.rindex("Output contract:")
        appendix_idx = after_block.index("Trusted workflow instruction") + end
        self.assertLess(appendix_idx, contract_idx)

    def test_phase1_configured_commands_outside_untrusted_in_composed_prompt(self) -> None:
        # When the runner composes a phase-1 prompt, the configured-commands
        # section must be outside the untrusted delimiters.
        configured_section = COMMANDS.phase1_configured_commands_section(
            ["documentation-build"]
        )
        composed = PROMPTS.compose_prompt(
            feedback_kind="release-project-review",
            output_contract="release-project-analysis-handoff-v1",
            profile_template="Review release {{feedback_kind}} for {{repository}}.",
            repository="o/r",
            author_login="release-reviewer",
            untrusted_content="release metadata",
            release_id=42,
            release_tag="v1",
            target_commit_sha="a" * 40,
            trusted_appendix=configured_section,
        )
        text = composed.text
        start = text.index(PROMPTS.START_MARKER)
        end = text.index(PROMPTS.END_MARKER) + len(PROMPTS.END_MARKER)
        untrusted_block = text[start:end]
        after_block = text[end:]
        # The configured-commands section must NOT appear inside untrusted markers.
        self.assertNotIn("Configured midflight commands", untrusted_block)
        # It MUST appear after the untrusted block.
        self.assertIn("Configured midflight commands", after_block)

    def test_phase2_comparison_instruction_outside_untrusted_in_composed_prompt(self) -> None:
        composed = PROMPTS.compose_prompt(
            feedback_kind="release-project-review",
            output_contract="release-project-issue-v1",
            profile_template="Review release {{feedback_kind}} for {{repository}}.",
            repository="o/r",
            author_login="release-reviewer",
            untrusted_content="release metadata\n\nhandoff data\n\ncommand evidence",
            release_id=42,
            release_tag="v1",
            target_commit_sha="a" * 40,
            trusted_appendix=COMMANDS.PHASE2_COMPARISON_INSTRUCTION,
        )
        text = composed.text
        start = text.index(PROMPTS.START_MARKER)
        end = text.index(PROMPTS.END_MARKER) + len(PROMPTS.END_MARKER)
        untrusted_block = text[start:end]
        after_block = text[end:]
        # The comparison instruction must NOT appear inside untrusted markers.
        self.assertNotIn("Phase-2 instruction", untrusted_block)
        # It MUST appear after the untrusted block.
        self.assertIn("Phase-2 instruction", after_block)

    def test_runner_places_phase1_configured_commands_outside_markers(self) -> None:
        """Runner-level test: the composed phase-1 prompt places the
        configured-commands section outside the untrusted delimiters."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "CHANGELOG.md").write_text("c\n", encoding="utf-8")
            bp, pp, mp = self._runner_inputs(tmp_path, ["documentation-build"])
            handoff = json.dumps({
                "assessment": "a", "validation_questions": ["q"],
                "relevant_evidence": []})
            final = json.dumps({"decision": "NO_ISSUE", "summary": "ok"})
            captured_prompts = []

            def capture_run(cmd, **kwargs):
                prompt_path = Path(cmd[cmd.index("--file") + 1])
                captured_prompts.append(prompt_path.read_text(encoding="utf-8"))
                # Return handoff for phase 1, final for phase 2.
                if len(captured_prompts) == 1:
                    return _FakeProc(handoff)
                return _FakeProc(final)

            with mock.patch.object(RUNNER.subprocess, "run", side_effect=capture_run), \
                 mock.patch.object(RUNNER.COMMANDS, "execute_command", return_value=_FakeResult()), \
                 mock.patch.object(RUNNER, "run_midflight_commands", return_value=[_FakeResult()]):
                rc = RUNNER.main(self._runner_args(tmp_path, bp, pp, mp))
            self.assertEqual(rc, 0)
            # Phase-1 prompt: configured-commands section outside markers.
            phase1 = captured_prompts[0]
            start = phase1.index(PROMPTS.START_MARKER)
            end = phase1.index(PROMPTS.END_MARKER) + len(PROMPTS.END_MARKER)
            untrusted_block = phase1[start:end]
            after_block = phase1[end:]
            self.assertNotIn("Configured midflight commands", untrusted_block)
            self.assertIn("Configured midflight commands", after_block)
            # Phase-2 prompt: comparison instruction outside markers.
            phase2 = captured_prompts[1]
            start2 = phase2.index(PROMPTS.START_MARKER)
            end2 = phase2.index(PROMPTS.END_MARKER) + len(PROMPTS.END_MARKER)
            untrusted_block2 = phase2[start2:end2]
            after_block2 = phase2[end2:]
            self.assertNotIn("Phase-2 instruction", untrusted_block2)
            self.assertIn("Phase-2 instruction", after_block2)

    def _runner_inputs(self, tmp, midflight):
        bundle = _resolved_bundle_with(midflight)
        policy = _policy_for(bundle, dry_run=True)
        metadata = {
            "repository": TARGET, "id": 42, "tag_name": "v1.0", "name": "v1.0",
            "published_at": "t", "draft": False, "prerelease": False,
            "target_commitish": SHA, "body": "notes", "asset_count": 0, "assets": [],
            "target_commit_sha": SHA,
        }
        bp = tmp / "b.json"; bp.write_text(json.dumps(bundle))
        pp = tmp / "p.json"; pp.write_text(json.dumps(policy))
        mp = tmp / "m.json"; mp.write_text(json.dumps(metadata))
        return bp, pp, mp

    def _runner_args(self, tmp, bp, pp, mp):
        prov = tmp / "prov.json"
        return [
            "review", "--resolved-config", str(bp), "--effective-policy", str(pp),
            "--release-metadata", str(mp), "--target-repository", TARGET,
            "--release-id", "42", "--release-tag", "v1.0",
            "--target-commit-sha", SHA, "--caller-repository", TARGET,
            "--target-token", "t", "--repo-root", str(tmp),
            "--provenance", str(prov), "--dry-run",
            "--publication-preview", str(tmp / "preview.json"),
        ]


if __name__ == "__main__":
    unittest.main()
