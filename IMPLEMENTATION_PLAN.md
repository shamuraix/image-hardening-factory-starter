# Implementation plan

## Quick roadmap

1. Intake and signed resource locks
2. Rootless OCI builds with internal RPM source control
3. SBOM + delegated scanner evidence + compliance/test evidence
4. Policy-gated import, signing, and digest-preserving promotion
5. Optional remediation/reproducibility extensions

---

## Detailed milestones

### Milestone 1 — Intake and immutable resource locks

Deliver:

- Internal source mirror flow for catalog sources
- Manifest resource resolution with checksum enforcement
- Signed `resource-lock.json` generation and publication

Done when:

- Identical inputs produce canonical lock output
- Builds cannot consume undeclared resources
- Intake permissions cannot write release repositories

### Milestone 2 — Rootless build pipeline foundation

Deliver:

- Rootless BuildKit image build path
- Internal RPM source selection (`private-mirror` / `public-upstream` where approved)
- OCI archive + metadata production

Done when:

- Build path has no unauthorized external egress
- Base identity and RPM source identity are recorded in build metadata/provenance

### Milestone 3 — Application image overlay pipeline

Deliver:

- Catalog dependency fan-out for Atlassian images
- Isolated build context/evidence directories per image
- Parallelized dependent image scheduling

Done when:

- Base-image selection behavior matches catalog dependency graph
- A single app build does not force unrelated base rebuilds

### Milestone 4 — Evidence and delegated assessment

Deliver:

- Syft CycloneDX/SPDX SBOMs
- Scanner evidence (Grype/Trivy/OSV + normalized findings)
- Delegated assessment status artifact
- Compliance and product-test evidence

Done when:

- Assessment and policy consume the exact built candidate digest
- Evidence remains visible and signed for audit/remediation

### Milestone 5 — Policy gate and controlled release

Deliver:

- OPA gate input/result generation
- Protected quarantine import
- Cosign signature + required attestations
- Digest-preserving promotion with verification before/after copy

Done when:

- Build jobs cannot publish to quarantine/release directly
- Promotion never mutates the promoted digest

### Milestone 6 — Optional extension stages

Deliver:

- Helmper/Copacetic/Hummingbird optional evidence hooks
- Read-only AI remediation summary support
- Guarded remediation-branch publication flow

Done when:

- Optional stages add evidence without weakening trust boundaries
- Auto-merge remains disabled for remediation outputs

### Milestone 7 — UBI canary evolution

Deliver:

- UBI canary path for compatibility qualification
- Controlled promotion separation for canary vs release

Done when:

- Canary cannot be promoted to release by configuration mistake
- Migration requires explicit catalog/policy/operator approval
