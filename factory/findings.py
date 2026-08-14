from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SEVERITIES = {"UNKNOWN", "NEGLIGIBLE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _finding(
    scanner: str,
    identifier: str,
    component: str,
    version: str,
    severity: str,
    fixed_version: str | None,
) -> dict[str, Any]:
    normalized = severity.upper()
    if normalized not in SEVERITIES:
        normalized = "UNKNOWN"
    return {
        "id": identifier,
        "scanner": scanner,
        "component": component,
        "installedVersion": version,
        "severity": normalized,
        "fixedVersion": fixed_version or "",
        "fixAvailable": bool(fixed_version),
        "knownExploited": False,
        "applicable": True,
        "new": True,
        "exception": None,
    }


def parse_grype(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for match in data.get("matches", []):
        vulnerability = match.get("vulnerability", {})
        artifact = match.get("artifact", {})
        fix = vulnerability.get("fix", {})
        versions = fix.get("versions") or []
        findings.append(
            _finding(
                "grype",
                vulnerability.get("id", "UNKNOWN"),
                artifact.get("purl") or artifact.get("name", "unknown"),
                artifact.get("version", "unknown"),
                vulnerability.get("severity", "UNKNOWN"),
                versions[0] if versions else None,
            )
        )
    return findings


def parse_trivy(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for result in data.get("Results", []):
        for vulnerability in result.get("Vulnerabilities") or []:
            findings.append(
                _finding(
                    "trivy",
                    vulnerability.get("VulnerabilityID", "UNKNOWN"),
                    vulnerability.get("PkgIdentifier", {}).get("PURL")
                    or vulnerability.get("PkgName", "unknown"),
                    vulnerability.get("InstalledVersion", "unknown"),
                    vulnerability.get("Severity", "UNKNOWN"),
                    vulnerability.get("FixedVersion"),
                )
            )
    return findings


def parse_osv(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for result in data.get("results", []):
        for package in result.get("packages", []):
            package_info = package.get("package", {})
            for vulnerability in package.get("vulnerabilities", []):
                findings.append(
                    _finding(
                        "osv-scanner",
                        vulnerability.get("id", "UNKNOWN"),
                        package_info.get("purl") or package_info.get("name", "unknown"),
                        package_info.get("version", "unknown"),
                        vulnerability.get("database_specific", {}).get("severity", "UNKNOWN"),
                        None,
                    )
                )
    return findings


def normalize(
    grype: Path,
    trivy: Path,
    osv: Path | None = None,
    kev: Path | None = None,
    baseline: Path | None = None,
) -> dict[str, Any]:
    all_findings = []
    all_findings.extend(parse_grype(json.loads(grype.read_text(encoding="utf-8"))))
    all_findings.extend(parse_trivy(json.loads(trivy.read_text(encoding="utf-8"))))
    if osv and osv.exists():
        all_findings.extend(parse_osv(json.loads(osv.read_text(encoding="utf-8"))))

    kev_ids: set[str] = set()
    if kev and kev.exists():
        kev_data = json.loads(kev.read_text(encoding="utf-8"))
        kev_ids = {
            item["cveID"] for item in kev_data.get("vulnerabilities", []) if item.get("cveID")
        }
    baseline_keys: set[str] = set()
    if baseline and baseline.exists():
        baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
        baseline_keys = {item["correlationKey"] for item in baseline_data.get("findings", [])}

    # Retain scanner-specific matches but provide a deterministic correlation key.
    for finding in all_findings:
        finding["correlationKey"] = "|".join(
            [finding["id"], finding["component"], finding["installedVersion"]]
        )
        finding["knownExploited"] = finding["id"] in kev_ids
        finding["new"] = finding["correlationKey"] not in baseline_keys
    all_findings.sort(key=lambda item: (item["id"], item["component"], item["scanner"]))
    return {"schemaVersion": "1.0", "findings": all_findings}
