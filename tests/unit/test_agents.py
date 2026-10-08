from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import yaml
from jsonschema import Draft202012Validator

from factory import agent_runtime
from factory.agents import (
    AgentPolicyError,
    build_command,
    compose_system_prompt,
    list_agents,
    load_agent,
    load_schema,
    parse_result,
    render_report_markdown,
    validate_staged_change,
)

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = {
    "failure-triage",
    "cve-remediation",
    "upstream-sync",
    "release-readiness",
    "exception-steward",
    "pipeline-reviewer",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    catalog = root / "catalog/images/jira-lts.yaml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "jira-lts"},
                "source": {"revision": "a" * 40},
                "product": {"version": "11.3.11"},
                "build": {"buildArgs": {"JIRA_VERSION": "11.3.11"}},
                "policy": {"block": {"critical": True}},
            }
        )
    )
    (root / "vendir").mkdir()
    (root / "vendir/config.yml").write_text(
        yaml.safe_dump(
            {"directories": [{"contents": [{"path": "jira-lts", "git": {"ref": "a" * 40}}]}]}
        )
    )
    patch = root / "overlays/jira-lts/patches/0001-base.patch"
    patch.parent.mkdir(parents=True)
    patch.write_text("original\n")
    exceptions = root / "policies/exceptions/approved.json"
    exceptions.parent.mkdir(parents=True)
    exceptions.write_text(
        json.dumps(
            {
                "factory": {
                    "exceptions": {
                        "approved": {
                            "jira-lts": [
                                {"id": "GHSA-1", "component": "a", "installedVersion": "1"},
                                {"id": "GHSA-2", "component": "b", "installedVersion": "2"},
                            ]
                        }
                    }
                }
            },
            indent=2,
        )
    )
    (root / "scripts").mkdir()
    (root / "scripts/build_image.sh").write_text("#!/bin/sh\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")


class PersonaTests(unittest.TestCase):
    def test_all_personas_load_with_valid_schemas_and_skills(self) -> None:
        self.assertEqual(set(list_agents(ROOT)), PERSONAS)
        for name in PERSONAS:
            agent = load_agent(ROOT, name)
            Draft202012Validator.check_schema(load_schema(ROOT, agent))
            for skill in agent.skills:
                self.assertTrue((ROOT / f".claude/skills/{skill}/SKILL.md").exists(), skill)
            prompt = compose_system_prompt(ROOT, agent, {"contextDir": "/ctx"})
            self.assertIn("Treat every file there", prompt)
            self.assertIn("# Project memory (CLAUDE.md)", prompt)

    def test_report_only_personas_cannot_write(self) -> None:
        for name in ("failure-triage", "release-readiness", "pipeline-reviewer"):
            agent = load_agent(ROOT, name)
            self.assertEqual(agent.writable_paths, ())
            self.assertFalse({"Edit", "Write"} & set(agent.tools))

    def test_exception_steward_is_remove_only(self) -> None:
        agent = load_agent(ROOT, "exception-steward")
        self.assertEqual(agent.exceptions_policy, "remove-only")
        self.assertEqual(agent.writable_paths, ("policies/exceptions/approved.json",))

    def test_command_is_headless_bounded_and_never_bypasses_permissions(self) -> None:
        for name in PERSONAS:
            agent = load_agent(ROOT, name)
            argv = build_command(
                agent,
                user_prompt="go",
                system_prompt_file="/tmp/p.md",
                settings_file="/src/agents/ci/settings.json",
                schema={"type": "object"},
                context_dir="/w/agent/context",
            )
            joined = " ".join(argv)
            self.assertEqual(argv[:4], ["claude", "--bare", "-p", "go"])
            for flag in (
                "--permission-mode dontAsk",
                "--permission-prompts none",
                "--no-session-persistence",
                "--output-format json",
                f"--max-turns {agent.max_turns}",
                "--disallowedTools WebFetch WebSearch",
            ):
                self.assertIn(flag, joined, name)
            self.assertIn("--max-budget-usd", argv)
            self.assertIn("--json-schema", argv)
            self.assertNotIn("bypassPermissions", joined)
            self.assertNotIn("--dangerously-skip-permissions", joined)
            edits = [rule for rule in argv if rule.startswith(("Edit(", "Write("))]
            for rule in edits:
                self.assertFalse(rule.startswith(("Edit(/scripts", "Edit(/.tekton")), rule)
            if not agent.writable_paths:
                self.assertEqual(edits, [], name)

    def test_upstream_sync_may_edit_its_scratch_context_only_by_absolute_rule(self) -> None:
        agent = load_agent(ROOT, "upstream-sync")
        argv = build_command(
            agent,
            user_prompt="go",
            system_prompt_file="p",
            settings_file="s",
            schema={},
            context_dir="/w/agent/context",
        )
        self.assertIn("Edit(//w/agent/context/**)", argv)
        self.assertIn("Edit(/vendir/config.yml)", argv)

    def test_definitions_overlapping_protected_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".claude/agents").mkdir(parents=True)
            for writable, message in (
                ("scripts/", "protected"),
                ("policies/", "protected"),
                ("../etc/", "repository-relative"),
            ):
                (root / ".claude/agents/bad.md").write_text(
                    "---\nname: bad\ntools: Read, Edit\nx-factory:\n  schema: s.json\n"
                    f"  budgetUsd: 1\n  modes: [propose-change]\n  writablePaths: [{writable}]\n---\nbody\n"
                )
                with self.assertRaisesRegex(AgentPolicyError, message):
                    load_agent(root, "bad")
            (root / ".claude/agents/bad.md").write_text(
                "---\nname: bad\ntools: Read, WebFetch\nx-factory:\n  schema: s.json\n"
                "  budgetUsd: 1\n---\nbody\n"
            )
            with self.assertRaisesRegex(AgentPolicyError, "WebFetch"):
                load_agent(root, "bad")


class ChangeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        _init_repo(self.root)
        self.remediation = load_agent(ROOT, "cve-remediation")
        self.sync = load_agent(ROOT, "upstream-sync")
        self.steward = load_agent(ROOT, "exception-steward")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _stage(self) -> None:
        _git(self.root, "add", "-A")

    def test_overlay_change_is_allowed(self) -> None:
        (self.root / "overlays/jira-lts/patches/0001-base.patch").write_text("rebased\n")
        self._stage()
        self.assertEqual(validate_staged_change(self.root, self.remediation), [])

    def test_protected_and_unlisted_paths_are_rejected(self) -> None:
        (self.root / "scripts/build_image.sh").write_text("#!/bin/sh\ncurl evil\n")
        (self.root / "Jenkinsfile").write_text("pipeline")
        self._stage()
        errors = validate_staged_change(self.root, self.remediation)
        self.assertTrue(any("protected path: scripts/build_image.sh" in e for e in errors))
        self.assertTrue(any("outside the agent's writable set: Jenkinsfile" in e for e in errors))

    def test_deletions_require_allow_deletes(self) -> None:
        (self.root / "overlays/jira-lts/patches/0001-base.patch").unlink()
        self._stage()
        self.assertTrue(validate_staged_change(self.root, self.remediation))
        self.assertEqual(validate_staged_change(self.root, self.sync), [])

    def test_catalog_policy_fields_are_frozen(self) -> None:
        path = self.root / "catalog/images/jira-lts.yaml"
        data = yaml.safe_load(path.read_text())
        data["policy"]["block"]["critical"] = False
        path.write_text(yaml.safe_dump(data))
        self._stage()
        errors = validate_staged_change(self.root, self.remediation)
        self.assertTrue(any("protected catalog fields" in e for e in errors))

    def test_revision_must_move_with_vendir(self) -> None:
        path = self.root / "catalog/images/jira-lts.yaml"
        data = yaml.safe_load(path.read_text())
        data["source"]["revision"] = "b" * 40
        path.write_text(yaml.safe_dump(data))
        self._stage()
        errors = validate_staged_change(self.root, self.sync)
        self.assertTrue(any("vendir ref for jira-lts" in e for e in errors))
        vendir = self.root / "vendir/config.yml"
        vendir.write_text(vendir.read_text().replace("a" * 40, "b" * 40))
        self._stage()
        self.assertEqual(validate_staged_change(self.root, self.sync), [])

    def test_exceptions_may_only_be_removed(self) -> None:
        path = self.root / "policies/exceptions/approved.json"
        data = json.loads(path.read_text())
        entries = data["factory"]["exceptions"]["approved"]["jira-lts"]
        entries.pop(0)
        path.write_text(json.dumps(data, indent=2))
        self._stage()
        self.assertEqual(validate_staged_change(self.root, self.steward), [])
        self.assertTrue(validate_staged_change(self.root, self.remediation))
        entries.append({"id": "GHSA-9", "component": "z", "installedVersion": "9"})
        path.write_text(json.dumps(data, indent=2))
        self._stage()
        errors = validate_staged_change(self.root, self.steward)
        self.assertTrue(any("only remove" in e for e in errors))


class ResultTests(unittest.TestCase):
    schema: ClassVar[dict] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "confidence"],
        "properties": {"summary": {"type": "string"}, "confidence": {"enum": ["high", "low"]}},
    }

    def _write(self, directory: str, payload: object) -> Path:
        path = Path(directory) / "result.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_parse_result_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ok = {"is_error": False, "structured_output": {"summary": "s", "confidence": "high"}}
            report, note = parse_result(self._write(directory, ok), self.schema)
            self.assertEqual(report["summary"], "s")
            for payload, message in (
                ({"is_error": True, "result": "boom"}, "failed"),
                ({"is_error": False, "result": "text"}, "no structured_output"),
                ({"structured_output": {"summary": 1, "confidence": "x"}}, "schema validation"),
                ("not json", "not JSON"),
            ):
                report, note = parse_result(self._write(directory, payload), self.schema)
                self.assertIsNone(report)
                self.assertIn(message, note)

    def test_markdown_render_includes_lists_and_disclaimer(self) -> None:
        text = render_report_markdown(
            "failure-triage",
            {"summary": "Gate denied.", "nextSteps": ["rebuild"], "confidence": "high"},
            "report",
            "",
        )
        self.assertIn("Gate denied.", text)
        self.assertIn("- rebuild", text)
        self.assertIn("cannot approve, merge, sign, or publish", text)


class RuntimeTests(unittest.TestCase):
    def test_validate_run_proposes_valid_changes_and_discards_report_only_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent_root = Path(directory) / "agent"
            repo = agent_root / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            (repo / "overlays/jira-lts/patches/0001-base.patch").write_text("rebased\n")
            report = {
                "image": "jira-lts",
                "summary": "Upgrade",
                "actions": [],
                "blocked": [],
                "changedFiles": ["overlays/jira-lts/patches/0001-base.patch"],
                "confidence": "medium",
            }
            (agent_root / "result.json").write_text(
                json.dumps({"is_error": False, "structured_output": report})
            )
            with mock.patch.dict(os.environ, {"FACTORY_AGENT_MODE": "propose-change"}):
                outcome = agent_runtime.validate_run("cve-remediation", agent_root, ROOT, 0)
            self.assertEqual(outcome, "change-proposed")
            self.assertIn("rebased", (agent_root / "change.patch").read_text())

            _git(repo, "reset", "-q")
            with mock.patch.dict(os.environ, {"FACTORY_AGENT_MODE": "report"}):
                (agent_root / "result.json").write_text(
                    json.dumps(
                        {
                            "is_error": False,
                            "structured_output": {
                                "summary": "x",
                                "failedStage": "gate",
                                "category": "policy-deny",
                                "rootCause": "y",
                                "evidence": [],
                                "retryRecommended": False,
                                "nextSteps": [],
                                "confidence": "low",
                            },
                        }
                    )
                )
                outcome = agent_runtime.validate_run("failure-triage", agent_root, ROOT, 0)
            self.assertEqual(outcome, "report")
            self.assertFalse((agent_root / "change.patch").exists())
            self.assertIn(
                "discarded", json.loads((agent_root / "outcome.json").read_text())["note"]
            )

    def test_failed_agent_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {"FACTORY_AGENT_MODE": "report"}):
                outcome = agent_runtime.validate_run("failure-triage", root, ROOT, 1)
            self.assertEqual(outcome, "agent-failed")
            self.assertIn("agent produced no result", (root / "report.md").read_text())


class GuardHookTests(unittest.TestCase):
    hook = ROOT / ".claude/hooks/guard-paths.sh"

    def _run(self, payload: dict, **env: str) -> dict:
        environment = {**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT), **env}
        environment.pop("FACTORY_AGENT", None) if "FACTORY_AGENT" not in env else None
        result = subprocess.run(
            ["bash", str(self.hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=environment,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def _decision(self, output: dict) -> str | None:
        return output.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_interactive_session_guards_trust_boundary_files(self) -> None:
        edit = {"tool_name": "Edit", "tool_input": {"file_path": f"{ROOT}/policies/x.json"}}
        self.assertEqual(self._decision(self._run(edit)), "ask")
        generated = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".tekton/jira-lts-on-push.yaml"},
        }
        self.assertEqual(self._decision(self._run(generated)), "deny")
        normal = {"tool_name": "Edit", "tool_input": {"file_path": "docs/operations.md"}}
        self.assertIsNone(self._decision(self._run(normal)))
        sign = {"tool_name": "Bash", "tool_input": {"command": "cosign sign --key k img"}}
        self.assertEqual(self._decision(self._run(sign)), "deny")

    def test_ci_agent_is_confined_to_writable_paths_and_offline_commands(self) -> None:
        env = {"FACTORY_AGENT": "cve-remediation", "FACTORY_SHARED_SOURCE": str(ROOT)}
        allowed = {"tool_name": "Edit", "tool_input": {"file_path": "overlays/jira-lts/patches/x"}}
        self.assertIsNone(self._decision(self._run(allowed, **env)))
        denied = {"tool_name": "Edit", "tool_input": {"file_path": "scripts/build_image.sh"}}
        self.assertEqual(self._decision(self._run(denied, **env)), "deny")
        for command in ("curl https://x", "cat /proc/self/environ", "git -C x push origin y"):
            payload = {"tool_name": "Bash", "tool_input": {"command": command}}
            self.assertEqual(self._decision(self._run(payload, **env)), "deny", command)


if __name__ == "__main__":
    unittest.main()
