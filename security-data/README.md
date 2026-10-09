# Offline security-data bundle

Scanners in the image build run without internet access. Instead they read a
signed bundle of vulnerability databases and compliance content that a separate
connected pipeline refreshes once a day.

## What the bundle contains

| Directory | Content | Used by |
|---|---|---|
| `grype/` | Grype vulnerability database | `scripts/grype_scan_image.sh` |
| `trivy/` | Trivy vulnerability and Java databases | `scripts/scan_image.sh` |
| `osv/` | OSV-Scanner offline databases for Go, Maven, npm, and PyPI ([offline mode](https://github.com/google/osv-scanner/blob/main/docs/offline-mode.md)) | `scripts/scan_image.sh` |
| `clamav/` | ClamAV malware signatures | `scripts/scan_image.sh` |
| `advisories/cisa-kev.json` | CISA [Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) | `factory/grype.py`, the release policy |
| `scap/` | ComplianceAsCode SCAP datastreams (`ssg-rhel9-ds.xml`, `ssg-rhel10-ds.xml`) | `scripts/compliance_scan.sh` |
| `generated-at` | UTC time the bundle was built | database freshness checks |
| `manifest.sha256` | SHA-256 digest of every file above | `scripts/fetch_security_bundle.sh` |

## How it flows

1. **Build and sign** (daily, CronJob `daily-security-data` →
   PipelineRun `security-data-on-schedule` → Task `factory-security-data`).
   `scripts/build_security_bundle.sh` downloads everything, writes the manifest,
   packs `security-data.tar.gz`, and signs it with the intake cosign key.
2. **Publish.** `scripts/publish_security_bundle.sh` uploads the archive and its
   signature to `<FACTORY_SOURCE_REPOSITORY>/security-data/<timestamp>/` in
   Artifactory, then rewrites `security-data/current.json` to point at it. The
   pointer is written last, so a reader never sees a half-finished upload.
3. **Fetch and verify** (every image build, prepare stage).
   `scripts/fetch_security_bundle.sh` reads the pointer, checks the archive's
   SHA-256 against it, verifies the cosign signature with the intake public key,
   unpacks it into `work/<image>/security-data/`, and checks every file against
   `manifest.sha256`. Any mismatch fails the stage.
4. **Seal.** The prepare stage seals the unpacked bundle as `security-data`. The
   scan and compliance stages verify that seal before reading the data, so they
   use exactly the files prepare verified.

## Freshness

The catalog setting `policy.maximumDatabaseAgeHours` (72 hours by default) caps
how old the data may be. The scan stage measures age from the **earlier** of the
bundle's `generated-at` and the Grype database build time, and the KEV catalog's
`dateReleased` must also fall inside the window. Stale or future-dated data makes
the release gate deny. With a daily refresh there are about two days of slack
before builds start failing for this reason.

`dateReleased` is when CISA last changed the catalog, not when the factory
downloaded it. If CISA publishes no update for more than 72 hours (for example
over a long holiday weekend), scans deny even with a fresh bundle. The fix is a
human decision about the freshness rule, not a scanner ignore.

## Requirements for the intake runner image

The bundle is built in `toolchain/Containerfile.factory-intake-runner`, which
already carries `grype`, `trivy`, `osv-scanner`, `freshclam`, `cosign`, `curl`,
`jq`, and the ComplianceAsCode datastreams at `COMPLIANCE_AS_CODE_DATASTREAM_DIR`
(`deploy/base/settings.yaml`). Its egress allowlist (NetworkPolicy
`approved-upstreams`) must reach the Grype, Trivy, OSV, and ClamAV database
mirrors and the CISA KEV feed.

## Local runs

The scan stage is not run outside the pipeline. To inspect a bundle by hand,
download and verify it as in step 3 and point `FACTORY_SECURITY_DATA` at the
unpacked directory before calling `scripts/scan_image.sh`.
