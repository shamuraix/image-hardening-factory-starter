# Implementation plan

This plan converts the ten recommended steps into deployable work packages.
Each milestone leaves the factory in a usable and testable state.

## Milestone 1 — Intake and immutable locks

Deliverables:

- Mirror the five Repo One repositories into internal GitLab.
- Validate every source revision as a full 40-character commit.
- Resolve every `hardening_manifest.yaml` HTTP and OCI resource.
- Verify declared SHA-256/SHA-512 values before upload.
- Store resources under content-addressed Artifactory paths.
- Generate `resource-lock.json` containing source, resource, base-image,
  repository-metadata, and toolchain digests.
- Sign the lock before transfer into a disconnected enclave.

Acceptance criteria:

- Repeating intake with identical inputs produces the same canonical lock.
- No build job can use an URL or resource absent from the lock.
- The connected intake service account cannot write release repositories.

## Milestone 2 — Harden and publish UBI 9.8

Deliverables:

- Patch the UBI Dockerfile to require an internal base reference by digest.
- Build using an immutable UBI RPM snapshot from Artifactory.
- Run the supplied hardening scripts explicitly.
- Run OpenSCAP/ComplianceAsCode, RPM integrity, crypto-policy, FIPS-host, and
  structural tests.
- Produce OCI image, SBOM, vulnerability results, provenance and gate result.

Acceptance criteria:

- The build runner has no external egress.
- The base and `repomd.xml` digests appear in provenance.
- All applicable release rules pass before quarantine import.

## Milestone 3 — Atlassian UBI 9.8 overlay

Deliverables:

- Patch Bitbucket, Confluence and Jira to require the released internal UBI 9
  image by digest.
- Change manifest base metadata from 9.7 to 9.8.
- Fix Bitbucket's exposed HTTP port discrepancy.
- Fix Jira's JSM OBR cleanup command.
- Add RPM integrity tests for every forced `rpm -e --nodeps` operation.
- Exclude the bundled legacy Helm charts from release scope.

Acceptance criteria:

- Every patch applies cleanly to its pinned upstream commit.
- Version labels, resource filenames and product build arguments agree.
- No Dockerfile contains Registry1 or a public base image reference.

## Milestone 4 — Parallel Atlassian builds

Deliverables:

- Dependency-aware child-pipeline generator.
- UBI 9 completion triggers Bitbucket, Confluence and Jira builds in parallel.
- Each image has an isolated build context and evidence directory.
- Failure of one application does not conceal results for the other two.

Acceptance criteria:

- Selecting `ubi9-minimal` renders all four affected pipelines.
- Selecting only `jira-lts` does not rebuild unrelated images.
- All application builds use the same approved UBI digest.

## Milestone 5 — SBOM, scanning and policy gate

Deliverables:

- Syft CycloneDX and SPDX SBOMs.
- CrowdStrike FCS image assessment, native JSON report and CycloneDX SBOM.
- Grype SBOM scan, Trivy image/config scan and OSV source scan retained as
  informational evidence.
- Red Hat CSAF/OVAL, CISA KEV and EPSS data supplied from Artifactory.
- Normalized finding document and OPA decision.

Acceptance criteria:

- The exact OCI archive produced by the build is assessed by FCS through an
  isolated local Podman API socket.
- The Falcon image assessment policy is the sole vulnerability-scanner release
  authority; a nonzero exit or invalid/missing FCS evidence denies release.
- Findings from Grype, Trivy, OSV and ClamAV remain visible and signed but
  cannot independently deny a gate or promotion.
- Compliance, product tests, SBOM validity, signatures and approvals remain
  independently blocking controls.

## Milestone 6 — Product integration testing

Deliverables:

- Base image structure and crypto tests.
- Ephemeral PostgreSQL tests for all three products.
- Bitbucket Git HTTP/SSH and supported Git-version tests.
- Confluence HTTP, Synchrony and graceful-shutdown tests.
- Jira/JSM plugin loading, HTTP and cluster-port tests.

Acceptance criteria:

- Tests run against the exact candidate digest.
- Startup and SIGTERM shutdown succeed as the configured non-root UID.
- `rpm -Va` deviations match a reviewed allowlist.

## Milestone 7 — Signing, attestations and pull promotion

Deliverables:

- Protected quarantine importer.
- Cosign signatures using environment-local encrypted keys, stored with the
  subject in Artifactory as OCI referrers, with transparency logging disabled.
- Signed in-toto attestations for SBOM, provenance, scans, compliance, tests,
  gate decision and approval.
- Pull-based promotion script that verifies source signatures and copies the
  subject plus OCI referrer closure.

Acceptance criteria:

- Build runners cannot push to quarantine or release repositories.
- Promotion copies the source digest without mutation.
- Gov1/Gov2 require environment-local `.us` approval and local signing.

## Milestone 8 — Read-only AI summaries

Deliverables:

- Schema-constrained remediation summary.
- Retrieval inputs limited to signed evidence, approved vendor security data,
  internal repository candidates and source files.
- Prompt-injection-resistant handling of advisory and source text.

Acceptance criteria:

- The agent has no write, signing, promotion or exception credential.
- Every proposed version is proven to exist in Artifactory.
- Unverifiable guidance is labeled blocked rather than guessed.

## Milestone 9 — Agent-generated patch merge requests

Deliverables:

- Agent writes only to an ephemeral overlay working tree.
- Deterministic tools perform dependency solving and checksum calculation.
- A trusted broker, not the agent, creates the GitLab branch and MR.
- Clean-checkout rebuild and rescan pipeline verifies the proposal.

Acceptance criteria:

- AI cannot edit `policies/`, `exceptions/`, VEX or signing configuration.
- Auto-merge remains disabled.
- The MR includes before/after findings, SBOM delta and test evidence.

## Milestone 10 — UBI 10 canary

Deliverables:

- Independent UBI 10.2 build and evidence pipeline.
- Validated RHEL 10 SCAP content and vulnerability-data coverage.
- Optional Atlassian compatibility branches; no production dependency change.
- Runtime, Java, native library, font and vendor-support comparison report.

Acceptance criteria:

- UBI 10 is not promoted as an Atlassian base by policy or tag accident.
- Migration requires an explicit catalog dependency change and human approval.
- UBI 9 rollback remains available by digest throughout the evaluation.

## Recommended delivery sequence

| Sprint | Scope | Exit artifact |
|---|---|---|
| 1 | Milestone 1 | Signed locks in Artifactory |
| 2 | Milestone 2 | Released UBI 9.8 digest |
| 3 | Milestones 3–4 | Three parallel application candidates |
| 4 | Milestone 5 | Enforced release policy |
| 5 | Milestone 6 | Product integration evidence |
| 6 | Milestone 7 | Signed pull-based release |
| 7 | Milestone 8 | Read-only remediation summaries |
| 8 | Milestone 9 | Guarded patch MRs |
| Parallel | Milestone 10 | UBI 10 compatibility report |
