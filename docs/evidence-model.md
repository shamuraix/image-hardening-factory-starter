# Evidence model

[Project overview](../README.md) · [Architecture](architecture.md) · [Operations](operations.md)

**Evidence** is the set of files that describe one built image (the
**candidate**). Every piece of evidence names the candidate's image digest
(`sha256:...`). If any piece names a different digest, release stops.

## What a releasable candidate needs

- a signed resource lock (the exact upstream inputs)
- SBOMs (software bills of materials) in CycloneDX and SPDX format
- a scanner assessment and normalized vulnerability findings
- a compliance result
- a test result
- a gate result (the release policy decision)
- build provenance
- an approval (the merge of the release request)

## Signed attestations

At release, `scripts/sign_and_attest.sh` signs the candidate with
[cosign](https://docs.sigstore.dev/cosign/signing/overview/) and attaches each
item below as a signed attestation (a signed statement about the digest).

| Attestation | Produced by | Contents |
|---|---|---|
| Resource lock | intake | source commit, resource URLs and checksums, OCI digests |
| CycloneDX SBOM | Syft (sbom stage) | OS packages and application dependencies |
| SPDX SBOM | Syft (sbom stage) | the same inventory in SPDX format |
| Provenance (`https://slsa.dev/provenance/v1`) | `scripts/generate_provenance.py` at signing time | source, base, resources, builder, output digest |
| Scanner decision (`delegated-scanners-decision`) | scan stage | backend, scanner identity, digest, `assessmentPassed`, Grype validation details |
| Raw scanner reports | scan stage | Grype, Trivy, and OSV-Scanner JSON; Syft version |
| Vulnerability findings | scan stage (`factory.cli normalize-findings`) | normalized findings from all scanners, with fix and KEV flags |
| Compliance result | compliance stage (OpenSCAP) | pass/fail and rule counts |
| Test result | test stage | pass/fail and exit code |
| Gate decision | gate stage (OPA, Open Policy Agent) | `allow`, `deny[]`, `warn[]`, scanner backend, digest |
| Approval | release pipeline | approver (who merged the release request), environment, digest, time |

Separately, Tekton Chains signs the image and stores provenance for the
quarantine and promotion runs with its own key. See
[architecture.md](architecture.md#signatures-and-provenance).

## How the scan stage works

`scripts/scan_image.sh` runs in the scan stage. It reads only the offline
[security-data bundle](../security-data/README.md) that the prepare stage
verified and sealed.

1. **Grype first.** It calls `scripts/grype_scan_image.sh`, which scans the
   CycloneDX SBOM with [Grype](https://github.com/anchore/grype) and then runs
   `factory/grype.py` to validate the result.
2. **Other scanners.** Only if Grype validation passes, it runs
   [Trivy](https://trivy.dev/) on the image archive and
   [OSV-Scanner](https://google.github.io/osv-scanner/) on the build context
   (only when it contains a `go.mod`, `pom.xml`, `package-lock.json`, or
   `requirements.txt`), both offline. Their behavior is unchanged.
3. **Normalize.** `factory.cli normalize-findings` merges the three reports into
   `evidence/findings.json` and marks KEV (CISA Known Exploited Vulnerabilities)
   entries.
4. **Malware.** ClamAV scans the unpacked image.
5. **Status.** It writes `evidence/database-status.json` and
   `evidence/scans/delegated/status.json` (the assessment).

### What makes Grype validation fail

Grype validation fails closed: any problem stops the scan stage. The checks are:

- The Grype database is invalid, or its build time has no time zone.
- The Grype report is malformed (missing identity, matches, IDs, severities,
  package names, or well-formed fix versions).
- The SBOM was generated for a different digest, or changed after it was
  generated. The sbom stage records the digest and both SBOM hashes in
  `evidence/sbom.identity.json`.
- The CISA KEV catalog's `dateReleased` is older than the catalog's
  `policy.maximumDatabaseAgeHours` (72 hours today) or is in the future.
- A baseline file is present and signed, but its cosign signature does not
  verify, or it is not an approved baseline for this image.

### Effect on the run and on other scanners

- When Grype validation fails, `scan_image.sh` exits before Trivy, OSV-Scanner,
  and ClamAV run, and no assessment status is written.
- The scan stage is allowed to fail, so its partial evidence is kept. The
  assessment stage then fails because the assessment status is missing. Tekton
  marks the PipelineRun failed and the gate never runs, so the candidate cannot
  be quarantined or released. The `failure-triage` agent runs; the
  `cve-remediation` agent does not, because it only runs when the gate fails.
- When Grype validation passes, Trivy and OSV-Scanner results feed the findings
  as before.

### Baselines and "new" findings

A **baseline** lists findings that a human already accepted for an image. A
finding that is in the baseline is "not new", which relaxes the policy rule that
blocks new HIGH findings (`policy.block.newHigh`).

- Only a signed baseline is used. A baseline needs `<image>.json`, its `.sig`,
  and a public key. An unsigned baseline is ignored with a warning.
- The scan stage applies a baseline to Trivy and OSV findings only when Grype
  verified it.
- Without a verified baseline, every finding from every scanner is marked new.
  The gate may therefore deny more often.
- The factory does not ship baselines today. The bundle built by
  `scripts/build_security_bundle.sh` has no `baselines/` directory, so in the
  pipeline every finding is currently new.

### Database freshness

`evidence/database-status.json` records `generatedAt`. It is the **earlier** of
the bundle's `generated-at` time and the Grype database build time. The gate
denies when this is older than `policy.maximumDatabaseAgeHours` or in the
future. This is stricter than using the bundle time alone.

The KEV check has a known risk. CISA sets `dateReleased` when it last changed
the catalog, not when the factory downloaded it. If CISA publishes no update for
more than 72 hours, every scan fails even with a fresh bundle. Deciding how to
handle that is a policy decision for a human; see
[security-data/README.md](../security-data/README.md#freshness).

### What is unaffected

Images with valid data and a signed baseline, or no baseline, behave as before
apart from the stricter freshness time. At release,
`scripts/verify_release_evidence.sh` accepts only the `delegated-scanners`
scanner backend.

## Gate decision

The gate (`policies/rego/factory/release/release.rego`) denies when any of
these is true:

- the SBOM is invalid, compliance failed, or tests failed;
- the candidate digest is invalid;
- the assessment did not pass, has the wrong scanner identity, or names a
  different digest;
- the vulnerability data is stale, missing, or from the future;
- a finding is blocked and not excepted (rules in the
  [`evidence-model` skill](../.claude/skills/evidence-model/SKILL.md)).

Warnings never cancel a deny. Exceptions in `policies/exceptions/approved.json`
apply only to vulnerability findings; they never bypass identity, freshness,
compliance, tests, or signatures.

## Moving evidence between stages and pipelines

- **Within a PipelineRun**: each stage writes a seal, a list of hashes of its
  output files (`work/<image>/.seals/<stage>.sha256`). The hash of that list is
  passed as a Tekton result. Every later stage checks the seal before reading.
  See [architecture.md](architecture.md#sealing-stage-outputs).
- **From build to release**: after quarantine import, the evidence directory is
  attached to the candidate as an OCI referrer (`scripts/publish_evidence_bundle.sh`).
  The release request pins the referrer's manifest digest and the archive's
  SHA-256; `scripts/fetch_evidence_bundle.sh` refuses anything else.

## Identity rules

- Attestations reference the exact candidate digest.
- Promotion keeps the digest and copies the evidence with it.
- `scripts/promote_image.sh` verifies evidence before and after the copy.
