---
name: evidence-model
description: Where factory evidence lives, what each file contains, and how the OPA release gate decides. Use when reading scan, compliance, test, or gate results, or reasoning about why a candidate was denied.
---

# Factory evidence and the release gate

All evidence for an image is under `work/<image>/` on the PipelineRun workspace.

| File | Producer | Key content |
|---|---|---|
| `image-metadata.json` | build | `digest`, `sourceRevision`, `baseRef`/`baseDigest`, `rpmSource`, `factoryRevision` |
| `resource-lock.json` | intake (signed) | locked upstream resources with digests |
| `evidence/sbom.cdx.json`, `sbom.spdx.json` | Syft | components (`name`, `version`, `purl`) |
| `evidence/scans/{grype,trivy,osv}.json` | scanners | raw scanner output |
| `evidence/findings.json` | `factory.findings` | normalized, de-duplicated findings |
| `evidence/scans/delegated/status.json` | scan stage | `assessmentPassed`, `digest`, `scanner` |
| `evidence/database-status.json` | scan stage | scanner DB `generatedAt`, versions |
| `evidence/compliance/result.json` | OpenSCAP | `passed`, rule counts |
| `evidence/tests/result.json` | product tests | `passed`, `exitCode` |
| `evidence/gate-input.json` / `gate-result.json` | OPA | `allow`, `deny[]`, `warn[]` |
| `import-result.json`, `evidence-bundle.json` | quarantine | quarantine ref/digest, evidence referrer digest |

## Normalized finding fields

`id`, `scanner`, `component` (purl when known), `installedVersion`, `severity`
(`UNKNOWN`…`CRITICAL`), `fixedVersion`, `fixAvailable`, `knownExploited` (CISA
KEV), `inApplicationArchive` (all locations under `/opt/`, `/app/`, `/srv/app/`,
`/workspace/` and none under system paths), `new` (not in baseline).

## Gate decision (`policies/rego/factory/release/release.rego`)

Deny when any of: invalid SBOM; compliance failed; tests failed; invalid digest;
assessment not passed or for a different digest; scanner DB older than
`policy.maximumDatabaseAgeHours` (72h); or a finding is blocked and not excepted:

- `UNKNOWN` severity always blocks;
- `CRITICAL` (if `block.critical`), fixable `HIGH` (`block.fixableHigh`),
  new `HIGH` (`block.newHigh`), any KEV (`block.knownExploited`).

A fixable HIGH/CRITICAL **outside** the application archive becomes a **warning**
instead of a deny. An approved exception (`policies/exceptions/approved.json`)
suppresses a blocking finding only when `id`, `component`, and
`installedVersion` all match **and** a fix exists. Exceptions never bypass
evidence identity, freshness, compliance, tests, or signatures.

## Integrity

Each stage seals its outputs (`work/<image>/.seals/<stage>.sha256`) and passes the
seal digest as a Tekton result; consumers verify before reading. Treat any
mismatch as tampering, never as a flake.
