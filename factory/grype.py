"""Validate offline Grype evidence before it can become authoritative gate input."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factory.findings import parse_grype


def validate_report(report: dict[str, Any], database: dict[str, Any]) -> None:
    if database.get("valid") is not True or database.get("error"):
        raise ValueError("Grype database is invalid")
    built = datetime.fromisoformat(database["built"])
    if built.tzinfo is None:
        raise ValueError("Grype database timestamp must have a timezone")
    if report.get("descriptor", {}).get("name") != "grype":
        raise ValueError("Grype report identity is missing")
    if not isinstance(report.get("matches"), list):
        raise TypeError("Grype matches must be an array")
    for match in report["matches"]:
        vulnerability, artifact = match["vulnerability"], match["artifact"]
        if not all(
            isinstance(vulnerability.get(key), str) and vulnerability[key]
            for key in ("id", "severity")
        ):
            raise ValueError("Grype vulnerability is incomplete")
        if not isinstance(artifact.get("name"), str) or not artifact["name"]:
            raise ValueError("Grype artifact identity is missing")
        versions = vulnerability.get("fix", {}).get("versions", [])
        if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
            raise ValueError("Grype fix versions must be an array of strings")


def validate_identity(evidence: Path, digest: str) -> None:
    identity = json.loads((evidence / "sbom.identity.json").read_text())
    if identity["digest"] != digest:
        raise ValueError("SBOM was generated for another image")
    for name in ("sbom.cdx.json", "sbom.spdx.json"):
        if identity[name] != hashlib.sha256((evidence / name).read_bytes()).hexdigest():
            raise ValueError("SBOM changed after generation")


def validate_kev(kev: dict[str, Any], maximum_age_hours: float) -> set[str]:
    if not isinstance(kev.get("vulnerabilities"), list):
        raise TypeError("KEV feed is invalid")
    released = datetime.fromisoformat(kev["dateReleased"])
    if released.tzinfo is None:
        raise ValueError("KEV dateReleased must include a timezone")
    age = (datetime.now(UTC) - released).total_seconds() / 3600
    if not 0 <= age <= maximum_age_hours:
        raise ValueError("KEV feed is stale or from the future")
    return {item["cveID"] for item in kev["vulnerabilities"]}


def approved_baseline(path: Path, key: Path, image: str) -> set[str]:
    subprocess.run(
        [
            "cosign",
            "verify-blob",
            "--key",
            str(key),
            "--insecure-ignore-tlog",
            "--signature",
            str(path) + ".sig",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    baseline = json.loads(path.read_text())
    if (
        baseline.get("approved") is not True
        or baseline.get("image") != image
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", baseline.get("imageDigest", ""))
    ):
        raise ValueError("Baseline approval or image identity is invalid")
    return {item["correlationKey"] for item in baseline["findings"]}


def assess(work: Path, digest: str, kev_path: Path, maximum_age_hours: float = 72) -> None:
    evidence = work / "evidence"
    scans = evidence / "scans/grype"
    validate_identity(evidence, digest)
    if json.loads((work / "image-metadata.json").read_text())["digest"] != digest:
        raise ValueError("Grype candidate digest differs from build metadata")
    report = json.loads((scans / "report.json").read_text())
    database = json.loads((scans / "database.json").read_text())
    validate_report(report, database)
    kev = json.loads(kev_path.read_text())
    kev_ids = validate_kev(kev, maximum_age_hours)
    baseline_keys: set[str] = set()
    baseline_digest = None
    if os.environ.get("FACTORY_GRYPE_BASELINE"):
        baseline_keys = approved_baseline(
            Path(os.environ["FACTORY_GRYPE_BASELINE"]),
            Path(os.environ["FACTORY_BASELINE_PUBLIC_KEY"]),
            os.environ["FACTORY_IMAGE"],
        )
    if os.environ.get("FACTORY_GRYPE_BASELINE"):
        baseline_digest = (
            "sha256:"
            + hashlib.sha256(Path(os.environ["FACTORY_GRYPE_BASELINE"]).read_bytes()).hexdigest()
        )
    findings = parse_grype(report)
    for finding in findings:
        finding["knownExploited"] = finding["id"] in kev_ids
        finding["correlationKey"] = "|".join(
            [finding["id"], finding["component"], finding["installedVersion"]]
        )
        finding["new"] = finding["correlationKey"] not in baseline_keys
    (scans / "findings.json").write_text(json.dumps({"findings": findings}) + "\n")
    (scans / "status.json").write_text(
        json.dumps(
            {
                "backend": "grype",
                "scanner": "syft-grype",
                "digest": digest,
                "assessmentPassed": True,
                "scannerVersion": report["descriptor"].get("version"),
                "baselineDigest": baseline_digest,
                "kevDigest": "sha256:" + hashlib.sha256(kev_path.read_bytes()).hexdigest(),
                "kevReleasedAt": kev["dateReleased"],
                "databaseBuilt": database["built"],
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    assess(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), float(sys.argv[4]))
