# Evidence model

Every released subject digest must have the following signed predicates:

| Predicate | Producer | Minimum contents |
|---|---|---|
| Resource lock | Intake | Source commit, URLs, checksums, OCI digests, RPM snapshot digest |
| CycloneDX SBOM | Syft | OS packages, source-built Git, nested Atlassian JARs |
| SPDX SBOM | Syft | Independent standards representation |
| SLSA provenance | Protected build metadata job | Source, base, resources, builder and output digest |
| FCS image assessment | CrowdStrike FCS | Native assessment, exact image digest, scanner exit and validation status |
| FCS CycloneDX SBOM | CrowdStrike FCS | FCS component inventory for assessment traceability |
| FCS scan decision | FCS scan job | Structured status document (`status.json`) recording scanner version, exit code, report/SBOM validity, and the overall `assessmentPassed` flag consumed by the OPA gate |
| Informational vulnerability | Grype/Trivy/OSV normalizer | Scanner matches, applicability, KEV, fixes and exceptions |
| Compliance | OpenSCAP | ARF, profile, failed/not-applicable/error controls |
| Tests | Product test job | Image digest, runtime checks and logs |
| Gate | OPA | Policy bundle digest, allow/deny and reasons |
| Approval | Protected environment job | Approver identity, environment, subject and timestamp |

The release broker must verify that the complete required predicate set refers
to the same subject digest before promotion. A tag is never evidence identity.

Syft's SPDX JSON is the canonical SPDX artifact. FCS does not publicly expose
an SPDX image-report format, so its native JSON and CycloneDX output are kept
without a lossy, factory-defined conversion. Only the FCS assessment status is
consumed as vulnerability-scanner policy input; the normalized legacy findings
remain available for comparison, remediation and audit.
