# Scope: what the pipeline does and what it leaves to others

[Project overview](../README.md) · [Architecture](architecture.md) · [Configuration](configuration.md) · [Operations](operations.md)

The factory is a cloud-native build-and-evidence pipeline. Every stage runs as
an ordinary Kubernetes pod with daemonless command-line tools. No stage runs a
container engine, runs the candidate image, or needs node-level features beyond
what Kubernetes 1.28 with containerd 1.7 provides.

This page is the contract. When a capability is listed as out of scope, the
pipeline neither performs it nor gates on it, and agents must not propose
adding it back as a fix.

## In scope

| Capability | Where | Tools |
|---|---|---|
| Intake: mirror Iron Bank sources, checksum-verify manifest resources, publish signed resource locks | `intake` | git, curl, skopeo, cosign |
| Signed offline vulnerability data (Grype, Trivy, OSV databases, CISA KEV) | `security-data` | grype, trivy, osv-scanner, cosign |
| Rootless image build with controlled RPM sources | `build` | BuildKit + RootlessKit |
| SBOMs (CycloneDX, SPDX) | `sbom` | syft |
| Vulnerability scanning and normalized findings | `scan`, `assessment` | grype, trivy, osv-scanner |
| Release gate | `gate` | opa |
| Quarantine import, signing, attestations, digest-preserving promotion | `quarantine`, `attest`, `promote` | skopeo, cosign, oras |
| Provenance | `attest`, Tekton Chains | Chains, cosign |
| Operational agents (triage, remediation proposals, upstream sync) | agent tasks | Claude Code |

## Out of scope

These are assumed to happen outside the pipeline, or are not required for a
release from it. The consuming environment owns them.

| Capability | Previously | Why it left |
|---|---|---|
| Runtime product tests (start the product, check ports, users, entrypoint) | `test` stage, rootless Podman, release gate `testsPassed` | Needs a container engine and nested `/proc` inside a pod, which needs pod user namespaces (not available on 1.28). Runtime behaviour is better tested where the image is deployed. |
| RPM integrity (`rpm -V` against allow lists) | part of the `test` stage | Ran inside the candidate with Podman. |
| Compliance scanning (OpenSCAP / SCAP Security Guide, STIG profiles) | `compliance` stage on FIPS nodes, release gate `compliancePassed` | Ran `oscap-chroot` under `podman unshare` and needed dedicated FIPS nodes. |
| Malware scanning (ClamAV) of image filesystems and mirrored resources | `scan` and `intake` stages | Needed `podman unshare` to unpack the rootfs and a signature feed. Intake still enforces upstream checksums. |
| Local kind / Rancher Desktop harness | `tests/integration/kind`, `make harness-*` | A second, laptop-specific runtime that tested a different configuration from production. Use a real cluster with `deploy/overlays/local`. |

The release attestations no longer include `compliance:v1` or `test:v1`
predicates, and `scripts/verify_release_evidence.sh` no longer requires them.
If a consuming environment produces its own compliance or test evidence, it
should attest that evidence to the same image digest with its own key.

## Platform floor

- Kubernetes 1.28 or later, containerd 1.7 or later. No pod user namespaces,
  `procMount`, or `securityContext.appArmorProfile`.
- Tekton Pipelines v1.15 (its stated floor is Kubernetes 1.28), Tekton Chains,
  Pipelines-as-Code.
- ValidatingAdmissionPolicy served as `admissionregistration.k8s.io/v1beta1`
  (feature gate and runtime-config enabled on 1.28).
- Nodes that allow unprivileged user namespaces. The build step's RootlessKit
  creates one itself.

[configuration.md](configuration.md) has the details for each.

## Security trade-off accepted with this scope

Without pod user namespaces, the build pod's UID 10001 and its subordinate
range `100000:65536` are real host UIDs shared by every build pod on a node. A
container escape lands as an unprivileged host user, not in an isolated ID
range. Run build pods on a dedicated, tainted node pool and keep them
single-use. The build run step remains non-root, unprivileged, and limited to
`CAP_SETUID`/`CAP_SETGID` in its bounding set.
