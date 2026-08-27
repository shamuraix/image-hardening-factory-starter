# Implementation status

The repository is a deployable reference implementation, but infrastructure-
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
| UBI/Atlassian overlays | Implemented; apply-tested | Review and approve patch MRs |
| Rootless Buildah OCI build | Implemented | Build and sign runner image; enable unprivileged user namespaces and enforce egress ACL |
| SBOM and layered scanning | Implemented; Grype/Trivy/OSV/ClamAV informational | Populate signed security-data bundle |
| CrowdStrike FCS assessment | Implemented as authoritative image-security gate | Build dedicated runner with pinned CLI and configure tenant assessment policy/API credentials |
| OpenSCAP compliance | Implemented | Validate RHEL 9/10 tailoring and rule applicability |
| Product tests | Implemented baseline | Add licensed PostgreSQL/OpenSearch cluster tests |
| OPA gate | Implemented; FCS is the sole scanner authority | Govern tenant image-assessment policies and review policy changes |
| Quarantine import | Implemented | Configure OIDC token exchange and permissions |
| Cosign/Artifactory OCI signing | Implemented | Configure environment-local key pairs, OIDC identity mapping and public keys |
| Recursive promotion | Implemented | Confirm Artifactory OCI 1.1 referrer preservation |
| AI read-only summary | Implemented | Deploy approved internal model and candidate index |
| AI remediation branch broker | Guardrails implemented | Add patch-producing agent recipe and keep auto-merge disabled |
| UBI 10 canary | Catalog/pipeline implemented | Complete vendor compatibility testing before adoption |

The first production activation should stop after UBI 9 quarantine until the
local compliance profile, RPM deviation allowlist and Artifactory referrer
behavior have been independently reviewed.
