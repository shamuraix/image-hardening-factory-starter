"""Behavioural tests for scripts/tekton/artifacts.sh (stash replacement)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/tekton/artifacts.sh"


class ArtifactSealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.directory.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
            subprocess.run(["git", "config", key, value], cwd=self.repo, check=True)
        (self.repo / "README").write_text("tracked\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        self.commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        self.work = self.repo / "work/jira-lts"
        (self.work / "evidence/scans").mkdir(parents=True)
        (self.work / "evidence/scans/grype.json").write_text("{}\n")
        (self.work / "evidence/findings.json").write_text('{"findings": []}\n')
        self.env = {**os.environ, "FACTORY_WORK_DIR": "work/jira-lts"}

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def seal(self) -> str:
        result_file = self.repo / "result"
        outcome = self.run_script(
            "seal",
            "scan",
            str(result_file),
            "work/jira-lts/evidence/scans/**",
            "work/jira-lts/evidence/findings.json",
            "work/jira-lts/evidence/missing.json",
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        digest = result_file.read_text()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn("2 file(s)", outcome.stdout)
        return digest

    def test_verify_accepts_untouched_outputs(self) -> None:
        digest = self.seal()
        self.assertEqual(self.run_script("verify", f"scan={digest}").returncode, 0)

    def test_verify_detects_modified_evidence(self) -> None:
        digest = self.seal()
        (self.work / "evidence/findings.json").write_text('{"findings": [], "x": 1}\n')
        outcome = self.run_script("verify", f"scan={digest}")
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("modified after sealing", outcome.stderr)

    def test_verify_detects_rewritten_manifest(self) -> None:
        digest = self.seal()
        (self.work / "evidence/findings.json").write_text("tampered\n")
        subprocess.run(
            [
                "bash",
                "-c",
                "sha256sum work/jira-lts/evidence/findings.json work/jira-lts/evidence/scans/grype.json > work/jira-lts/.seals/scan.sha256",
            ],
            cwd=self.repo,
            check=True,
        )
        outcome = self.run_script("verify", f"scan={digest}")
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("manifest was modified", outcome.stderr)

    def test_verify_requires_a_digest_from_a_completed_task(self) -> None:
        self.seal()
        outcome = self.run_script("verify", "scan=")
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("no valid digest", outcome.stderr)

    def test_verify_source_rejects_modified_checkout_but_ignores_work(self) -> None:
        self.assertEqual(self.run_script("verify-source", self.commit).returncode, 0)
        (self.repo / "README").write_text("changed by an earlier task\n")
        outcome = self.run_script("verify-source", self.commit)
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("modified by an earlier task", outcome.stderr)

    def test_seal_rejects_patterns_outside_the_repository(self) -> None:
        outcome = self.run_script("seal", "scan", str(self.repo / "r"), "/etc/passwd")
        self.assertNotEqual(outcome.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class StepEnvironmentTests(unittest.TestCase):
    def test_env_sh_forbids_core_dumps_from_an_unlimited_start(self) -> None:
        # The pod starts with an unlimited core size; env.sh must lower both
        # limits (hard-only is EINVAL when the soft limit is higher) and leave
        # the hard limit at 0 so children cannot raise it.
        script = """
        set -e
        ulimit -S -c unlimited 2>/dev/null || ulimit -S -c 1024
        cd "$1" && source "$2"
        printf 'hard=%s soft=%s\\n' "$(ulimit -H -c)" "$(ulimit -S -c)"
        (ulimit -c 1 2>/dev/null) && echo raised || echo locked
        """
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", "-c", script, "bash", directory, str(ROOT / "scripts/tekton/env.sh")],
                env={
                    **os.environ,
                    "FACTORY_IMAGE": "ubi9-minimal",
                    "FACTORY_PIPELINERUN": "run-1",
                    "FACTORY_COMMIT_SHA": "0" * 40,
                },
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ulimit", result.stderr)
        self.assertIn("hard=0 soft=0", result.stdout)
        self.assertIn("locked", result.stdout)
