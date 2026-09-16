# Implementation status

The repository is an integration reference implementation, but infrastructure-
specific integration cannot be completed without the target Jenkins and
Kubernetes configuration, Artifactory, runner images, repository certificates and approval
groups.

| Capability | Code status | Environment work remaining |
|---|---|---|
| Five-image catalog and schema | Implemented and tested | Establish change ownership |
| Dependency-aware Jenkins execution plan | Implemented and tested | Validate against the deployed Jenkins plugin versions |
| Source mirroring | Implemented | Pre-create internal mirror projects |
| Resource checksum/digest locks | Implemented and tested | Configure upstream allowlist and Artifactory |
| Immutable RPM snapshots | Implemented | Supply UBI-only source repository files |
| UBI/Atlassian overlays | Implemented; recheck against each pinned revision | Review and approve patch MRs |
| Rootless BuildKit OCI build | Implemented | Build and sign runner image with BuildKit/RootlessKit; configure the BuildKit pod template, user namespaces and security profiles; enforce egress ACL |
| SBOM and layered scanning | Implemented; Grype/Trivy/OSV/ClamAV informational | Populate signed security-data bundle |
| CrowdStrike FCS assessment | Implemented as authoritative image-security gate | Build dedicated runner with pinned CLI and configure tenant assessment policy/API credentials |
| OpenSCAP compliance | Implemented | Validate RHEL 9/10 tailoring and rule applicability |
| Product tests | Implemented baseline | Add licensed PostgreSQL/OpenSearch cluster tests |
| OPA gate | Implemented; configured/enabled FCS preferred, otherwise Syft/Grype | Govern tenant image-assessment policies and review policy changes |
| Quarantine import | Implemented | Configure OIDC token exchange and permissions |
| Cosign/Artifactory OCI signing | Implemented | Configure environment-local key pairs, Kubernetes workload identity mapping and public keys |
| Recursive promotion | ORAS referrers plus Cosign 2.x attachment copy | Confirm Artifactory OCI 1.1 referrer preservation |
| AI read-only summary | Implemented | Deploy approved internal model and candidate index |
| AI remediation branch broker | Guardrails implemented | Add patch-producing agent recipe and keep auto-merge disabled |
| Helmper/Copacetic/Hummingbird concept stages | Implemented as optional evidence hooks | Configure tool binaries/commands and promote from informative to enforced policy as needed |
| UBI 10 canary | Catalog/pipeline implemented | Complete vendor compatibility testing before adoption |

The first production activation should stop after UBI 9 quarantine until the
local compliance profile, RPM deviation allowlist and Artifactory referrer
behavior have been independently reviewed.

Unit tests and shell syntax checks do not establish a successful Jenkins/Kubernetes/Falcon release. The environment activation gates in [operations](operations.md) remain mandatory.
