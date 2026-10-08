from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from factory import release

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "1" * 64
REPOSITORY = "artifactory.internal.example/factory-app-quarantine/atlassian/jira-lts"


def _work_dir(root: Path, allow: bool = True) -> Path:
    work = root / "work/jira-lts"
    (work / "evidence").mkdir(parents=True)
    (work / "image-metadata.json").write_text(
        json.dumps({"digest": DIGEST, "sourceRevision": "a" * 40, "factoryRevision": "b" * 40})
    )
    (work / "import-result.json").write_text(
        json.dumps(
            {"imported": True, "imageRef": f"{REPOSITORY}:11.3.11-build-1", "digest": DIGEST}
        )
    )
    (work / "evidence-bundle.json").write_text(
        json.dumps({"manifestDigest": "sha256:" + "2" * 64, "sha256": "3" * 64})
    )
    (work / "evidence/gate-result.json").write_text(
        json.dumps({"allow": allow, "warn": ["Vulnerability warning: X (y)"], "deny": []})
    )
    return work


class ReleaseRequestTests(unittest.TestCase):
    def test_build_request_from_sealed_evidence_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = _work_dir(root)
            request = release.build_request(
                work, ROOT / "catalog/images/jira-lts.yaml", "gov1", "jira-lts-on-push-abc"
            )
            self.assertEqual(request["candidate"]["tag"], "11.3.11-build-1")
            self.assertEqual(request["candidate"]["repository"], REPOSITORY)
            self.assertEqual(request["gate"]["warnings"], ["Vulnerability warning: X (y)"])
            path = root / release.request_path("gov1", "jira-lts")
            path.parent.mkdir(parents=True)
            path.write_text(release.dump_request(request))
            loaded = release.load_request(path, ROOT / "catalog/images")
            self.assertEqual(loaded["candidate"]["digest"], DIGEST)

    def test_denied_or_mismatched_candidates_cannot_be_requested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = _work_dir(Path(directory), allow=False)
            with self.assertRaisesRegex(release.ReleaseRequestError, "gate denied"):
                release.build_request(work, ROOT / "catalog/images/jira-lts.yaml", "commercial")

    def test_request_location_and_contents_must_agree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = release.build_request(
                _work_dir(root), ROOT / "catalog/images/jira-lts.yaml", "commercial"
            )
            wrong = root / "releases/gov2/jira-lts.yaml"
            wrong.parent.mkdir(parents=True)
            wrong.write_text(release.dump_request(request))
            with self.assertRaisesRegex(release.ReleaseRequestError, "metadata.environment"):
                release.load_request(wrong, ROOT / "catalog/images")
            renamed = root / "releases/commercial/bitbucket-lts.yaml"
            renamed.parent.mkdir(parents=True)
            renamed.write_text(release.dump_request(request))
            with self.assertRaisesRegex(release.ReleaseRequestError, "file name"):
                release.load_request(renamed, ROOT / "catalog/images")
            request["candidate"]["digest"] = "sha256:short"
            bad = root / "releases/commercial/jira-lts.yaml"
            bad.write_text(yaml.safe_dump(request))
            with self.assertRaises(release.ReleaseRequestError):
                release.load_request(bad, ROOT / "catalog/images")

    def test_canary_images_release_only_to_commercial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = release.build_request(
                _work_dir(root), ROOT / "catalog/images/jira-lts.yaml", "gov1"
            )
            request["metadata"]["image"] = "ubi10-minimal"
            request["candidate"]["repository"] = (
                "registry.example/base-quarantine/bases/ubi10-minimal"
            )
            path = root / "releases/gov1/ubi10-minimal.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(yaml.safe_dump(request))
            with self.assertRaisesRegex(release.ReleaseRequestError, "canary"):
                release.load_request(path, ROOT / "catalog/images")

    def test_release_merge_must_change_exactly_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
                subprocess.run(["git", "config", key, value], cwd=repo, check=True)
            shutil.copytree(ROOT / "catalog", repo / "catalog")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            request = release.build_request(
                _work_dir(Path(directory) / "scratch"),
                ROOT / "catalog/images/jira-lts.yaml",
                "commercial",
            )
            path = repo / "releases/commercial/jira-lts.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(release.dump_request(request))
            subprocess.run(["git", "add", "releases"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "release"], cwd=repo, check=True)
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            resolved = release.resolve(repo, "catalog/images", commit)
            self.assertEqual(resolved["metadata"]["image"], "jira-lts")

            other = repo / "releases/gov1/jira-lts.yaml"
            other.parent.mkdir(parents=True)
            request["metadata"]["environment"] = "gov1"
            other.write_text(release.dump_request(request))
            path.write_text(
                release.dump_request(
                    {**request, "metadata": {"image": "jira-lts", "environment": "commercial"}}
                )
                + "# touch\n"
            )
            subprocess.run(["git", "add", "releases"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "two"], cwd=repo, check=True)
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip()
            with self.assertRaisesRegex(release.ReleaseRequestError, "exactly one"):
                release.resolve(repo, "catalog/images", commit)


if __name__ == "__main__":
    unittest.main()
