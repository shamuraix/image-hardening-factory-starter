# Offline security-data bundle

The connected security-data job builds a signed bundle containing:

- Grype vulnerability database.
- Trivy vulnerability and Java databases.
- OSV offline databases.
- CISA KEV and EPSS datasets.
- Red Hat CSAF and OVAL data.
- ComplianceAsCode SCAP datastreams.
- ClamAV signature databases.
- Tool/database timestamps and content digests.

Use `scripts/build_security_bundle.sh` on a connected, ephemeral intake runner.
The resulting archive and signature are published to Artifactory. The factory
runner image must include a verified unpacked bundle at `/opt/security-data`.
