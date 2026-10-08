# Evidence model

## Quick evidence checklist

A releasable candidate must have signed evidence tied to the **same image digest**.

Minimum expected evidence categories:

- resource lock
- SBOM(s)
- delegated scanner assessment status + normalized findings
- compliance result
- test result
- gate result
- provenance
- approval (for governed release paths)

If digest identity diverges at any point, release must stop.

---

## Advanced evidence reference

### Signed predicate set

| Predicate | Producer | Minimum contents |
|---|---|---|
| Resource lock | Intake | Source commit, URLs/checksums, OCI digests |
| CycloneDX SBOM | Syft | OS packages + application dependency inventory |
| SPDX SBOM | Syft | SPDX representation of image inventory |
| SLSA-style provenance | Signing stage (from build metadata) | Source/base/resources/builder/output digest |
| Delegated scanner decision | Assessment stage | Backend ID, scanner identity, digest, `assessmentPassed` |
| Vulnerability findings | Normalization flow | Scanner findings, fixability, applicability context, exceptions |
| Compliance result | OpenSCAP stage | Pass/fail summary + retained supporting artifacts |
| Test result | Product test stage | Pass/fail summary + retained logs |
| Gate decision | OPA | Allow/deny with reasons (and warnings when applicable) |
| Approval | Release pipeline (merger of the release request) | Approver identity, environment, subject digest, timestamp |
| Tekton Chains provenance | Chains controller | SLSA v1 provenance for quarantine/promotion TaskRuns and PipelineRuns, incl. evidence bundle digest |

### Policy and evidence interplay

- Gate policy evaluates signed/structured evidence, not mutable tags.
- Assessment status and gate decision both depend on normalized evidence and
  policy thresholds/exceptions.
- Warnings do not override deny conditions.

### Evidence transport and integrity

- **Within a PipelineRun**: each stage seals its outputs
  (`work/<image>/.seals/<stage>.sha256`); the manifest digest travels as a Tekton
  result and every consumer verifies it before reading.
- **Between build and release**: after quarantine import the evidence directory
  is attached to the candidate as an OCI referrer (`publish_evidence_bundle.sh`).
  The release request pins the referrer's manifest digest and the tarball
  SHA-256; `fetch_evidence_bundle.sh` refuses anything else.

### Identity rules

- Attestations must reference the exact subject digest.
- Promotion must preserve digest identity and copy required evidence artifacts.
- Verification should occur before and after destination copy.
