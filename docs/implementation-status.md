# Implementation status

## Quick summary

This repository is a strong integration reference implementation, but production
readiness still depends on environment-specific Jenkins/Kubernetes, repository,
credential, and organizational controls.

---

## Detailed status matrix

| Capability | Code status | Environment work remaining |
|---|---|---|
| Five-image catalog and schema | Implemented and tested | Establish change ownership/governance |
| Dependency-aware Jenkins execution plan | Implemented and tested | Validate against deployed Jenkins plugin versions |
| Source mirroring and resource locks | Implemented and tested | Configure approved origins and retention policy |
| Rootless BuildKit OCI build path | Implemented | Harden cluster runtime policy and trust-class isolation |
| SBOM and scanner evidence pipeline | Implemented | Maintain signed tool/data bundle lifecycle |
| Delegated scanner assessment backend | Implemented as gate input | Validate policy ownership and exception governance |
| OpenSCAP compliance stage | Implemented | Finalize environment tailoring and rule applicability |
| Product baseline tests | Implemented baseline | Add licensed data-service and clustering qualification |
| OPA policy gate | Implemented | Enforce policy review/change control in release process |
| Quarantine import | Implemented | Configure workload identity + least-privilege write scope |
| Cosign signing/attestation | Implemented | Provision and rotate environment-specific signing keys |
| Promotion/referrer copy | Implemented | Confirm destination registry behavior and verification SLO |
| AI remediation summary/branch broker | Implemented with guardrails | Deploy approved inference endpoint and keep auto-merge disabled |
| Optional Helmper/Copacetic/Hummingbird hooks | Implemented | Decide which are informational vs enforced in each environment |
| UBI 10 canary path | Implemented in catalog/pipeline | Complete product/vendor compatibility acceptance |

---

## Activation guidance

First production activation should stop after quarantine until evidence,
policy, signing, and promotion behaviors are independently reviewed by release
owners.

Unit tests and local harness checks are necessary but not sufficient for full
production activation; environment gates in `docs/operations.md` remain
mandatory.
