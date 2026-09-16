# Configuration reference

[Project overview](../README.md) · [Operations](operations.md)

## Jenkins on unprivileged Kubernetes agents

`Jenkinsfile` runs the image factory and `Jenkinsfile.intake` runs connected
intake. Both use the Jenkins Kubernetes plugin and add a `factory` container to
an administrator-managed pod template. Every template must set
`privileged: false`; it must not mount host paths, container-engine sockets,
host devices, or add Linux capabilities. The runner executes as UID 10001,
uses subordinate IDs for rootless BuildKit and Podman, and lets the outer pod
enforce cgroup limits. BuildKit uses its native snapshotter; VFS remains only
for Podman's scanner and product-test storage.

Runner nodes must allow unprivileged user namespaces. `/tmp` and
`/home/factory` must be writable, and the runtime seccomp profile must permit
the user-namespace operations used by rootless BuildKit and Podman. ClamAV and
OpenSCAP inspect an ownership-preserving Umoci unpack inside Podman's rootless
user namespace. FCS uses a job-local rootless Podman socket; no host socket is
mounted.

Configure a distinct pod template, Kubernetes ServiceAccount, namespace, and
NetworkPolicy for each trust class. Supply template names through these Jenkins
environment settings:

| Setting | Trust class |
|---|---|
| `FACTORY_K8S_INTAKE_POD_TEMPLATE` | Approved upstream and intake-only writes |
| `FACTORY_K8S_OFFLINE_POD_TEMPLATE` | Internal read-only analysis |
| `FACTORY_K8S_BUILDKIT_POD_TEMPLATE` | Rootless, internal-only build |
| `FACTORY_K8S_FIPS_POD_TEMPLATE` | FIPS-node compliance |
| `FACTORY_K8S_TEST_POD_TEMPLATE` | Rootless product tests |
| `FACTORY_K8S_FCS_POD_TEMPLATE` | Falcon-only protected egress |
| `FACTORY_K8S_AI_POD_TEMPLATE` | AI endpoint and read-only evidence |
| `FACTORY_K8S_REMEDIATION_POD_TEMPLATE` | SCM branch publication only |
| `FACTORY_K8S_IMPORT_POD_TEMPLATE` | Quarantine writes only |
| `FACTORY_K8S_SIGNING_POD_TEMPLATE` | Signature/referrer writes only |
| `FACTORY_K8S_PROMOTION_POD_TEMPLATE` | Verified release copy only |

Set `FACTORY_RUNNER_IMAGE`, `FACTORY_FCS_RUNNER_IMAGE`, and
`FACTORY_INTAKE_RUNNER_IMAGE` to signed, digest-qualified image references.
Configure an object-storage-backed Jenkins Artifact Manager because OCI archives
passed between ephemeral pods are too large for controller-local stashes.
Required Jenkins plugins are Kubernetes, Credentials Binding, Lockable
Resources, Pipeline: Input Step, and the selected Artifact Manager.

### BuildKit pod template

Register [the BuildKit pod YAML](../toolchain/jenkins-buildkit-pod.yaml) as the
administrator-managed template named by `FACTORY_K8S_BUILDKIT_POD_TEMPLATE`.
Replace its image placeholder and ServiceAccount with deployment values; the
pipeline overrides the `factory` image with the digest-pinned
`FACTORY_RUNNER_IMAGE`. Use Kubernetes 1.30+ for the structured AppArmor field
(older clusters require the equivalent container AppArmor annotation).
Size the pod's disk-backed `/tmp` and Jenkins workspace for unpacked base
layers, native snapshots and the candidate archive. The Jenkins agent and
`factory` container must share the workspace and compatible UID/GID 10001.

The build runs an ephemeral `buildkitd` **inside the factory container**, not a
shared service or privileged sidecar. `scripts/run_buildkit.sh` starts it in a
RootlessKit user namespace, waits for readiness over a private Unix socket,
and stops it and removes its native snapshots on success, failure or termination.
No Docker daemon, containerd, host socket, `/dev/fuse`, or persistent cache is
required. Pod eviction/SIGKILL relies on pod teardown to reclaim the `emptyDir`.
`BUILDKIT_HOST` and user daemon configuration are not used.

The example needs an explicit admission-policy exception: it is neither
Baseline nor Restricted Pod Security compliant. `newuidmap` and
`newgidmap` need their setuid bits, `allowPrivilegeEscalation: true`, and the
runtime's default SETUID/SETGID capability bounding set; do not drop all
capabilities or add capabilities. The example uses unconfined seccomp and
AppArmor profiles to allow nested user namespaces and mounts; substitute
audited localhost profiles only after testing the full build. Do not enable
privileged mode or host PID/network namespaces. Nodes must allow unprivileged
user namespaces, including any distribution-specific AppArmor userns policy.

Jenkins sets `FACTORY_BUILDKIT_NO_PROCESS_SANDBOX=true` only in the build stage.
This upstream BuildKit mode avoids nested `/proc` mounts denied by many
container runtimes, but build processes can signal or inspect other processes
in the factory container. In this mode RootlessKit also omits `--pidns`, because
that would remount `/proc` and reintroduce the same runtime restriction.
The container's PID namespace remains separate from Jenkins' `jnlp` container.
Only local sandboxed mode creates an extra RootlessKit PID namespace. A fresh
pod per stage and mandatory pod teardown contain lingering build processes;
they do not make a shared daemon safe for hostile tenants. Build only reviewed sources in the build trust class, bind only
read-only intake/RPM credentials there, and never place signing or promotion
credentials in the pod.

`FACTORY_BUILD_NETWORK` accepts `default` (production), `none`, or `host`.
RootlessKit shares its parent's network: in Jenkins that is the pod network,
not the node network; locally it permits the loopback RPM server. BuildKit's
rootless worker uses host networking within that namespace. Only explicit
`host` requests enable the `network.host` entitlement. Enforce internal-only
egress with the build trust class's NetworkPolicy, including DNS and required
Jenkins connectivity; a frontend network setting is not an egress firewall.

Build inputs use a digest-verified OCI layout supplied through a BuildKit
session, including bases produced by earlier Jenkins stages. The embedded
Dockerfile frontend does not need an external syntax image. Each build uses
fresh state rather than an untrusted/shared cache. A native source policy denies
remote image, HTTP and Git sources during the solve; download locked inputs
during preparation instead. `BASE_REF`, `BASE_MAJOR`, `SOURCE_DATE_EPOCH`, and
`BUILDKIT_SYNTAX` are reserved and cannot be overridden by catalog build args.
RPM configuration is passed
as a native secret into a writable tmpfs masking `/etc/yum.repos.d` for each
`RUN`, never copied into a layer. OCI output is handed to the unchanged
scanner/import/signing stages using its inspected final archive digest.
The source commit supplies `SOURCE_DATE_EPOCH` and the OCI creation/revision
labels. BuildKit's native `rewrite-timestamp=true` clamps newer layer timestamps
and preserves eligible base layers; it is not a guarantee of byte-for-byte
equivalence with the previous builder. Skopeo normalizes the archive reference
so downstream Podman and Skopeo select the exact `LOCAL_IMAGE_REF`.

## Jenkins settings and credentials

Infrastructure-specific URLs and resource names have no repository defaults.
Configure them at the Jenkins folder or job level:

| Setting | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` | Internal SCM namespace containing source mirrors |
| `SCM_REPOSITORY_URL` | Factory repository push URL used by the remediation broker |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Artifactory API base URL and OCI registry host |
| `FACTORY_SOURCE_REPOSITORY` | Generic repository for locks, snapshots, and intake content |
| `UPSTREAM_OCI_REPOSITORY` | OCI repository for digest-pinned upstream bases |
| `FACTORY_RPM_SNAPSHOT_UBI9_REPOSITORY` / `FACTORY_RPM_SNAPSHOT_UBI10_REPOSITORY` | Immutable RPM snapshot repositories |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` / `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | Protected candidate repositories |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | Release and canary repositories |
| `RPM_SNAPSHOT_UBI9_ID` / `RPM_SNAPSHOT_UBI10_ID` | Immutable snapshot identifiers consumed by builds |
| `FACTORY_DEFAULT_BRANCH` | Only branch allowed to import, sign, or promote |
| `FACTORY_IMPORT_LOCK_PREFIX` / `FACTORY_PROMOTION_LOCK_PREFIX` | Lockable Resources prefixes |
| `FACTORY_GOV_APPROVERS` | Jenkins RBAC group allowed to approve Gov release stages |
| `FACTORY_GOV_APPROVER_PATTERN` | Anchored allowlist pattern for authenticated approver IDs |
| `SCM_REMEDIATION_AUTHOR_NAME` / `SCM_REMEDIATION_AUTHOR_EMAIL` | Bot identity for remediation commits |
| `FACTORY_UPSTREAM_BRANCH` | Upstream branch resolved by source-pin maintenance |
| `AI_BASE_URL` / `AI_MODEL` | Approved internal inference endpoint and model |
| `FALCON_REGION` | FCS tenant region |

The Jenkinsfiles accept stage toggles and image selection as build parameters.
All secret values are bound only inside their owning stage through credential ID
settings:

| Credential ID setting | Bound value |
|---|---|
| `ARTIFACTORY_READ_CREDENTIAL_ID` | Read-only Artifactory token |
| `ARTIFACTORY_INTAKE_WRITE_CREDENTIAL_ID` | Intake-only write token |
| `ARTIFACTORY_WRITE_CREDENTIAL_ID` | Quarantine importer token |
| `ARTIFACTORY_SIGN_CREDENTIAL_ID` | Short-lived referrer-write token |
| `ARTIFACTORY_RELEASE_CREDENTIAL_ID` | Release-copy token |
| `COSIGN_INTAKE_KEY_CREDENTIAL_ID` / `COSIGN_INTAKE_PASSWORD_CREDENTIAL_ID` | Intake key and password |
| `COSIGN_INTAKE_PUBLIC_KEY_CREDENTIAL_ID` | Intake verification key |
| `COSIGN_KEY_CREDENTIAL_ID` / `COSIGN_PASSWORD_CREDENTIAL_ID` | Environment signing key and password |
| `COSIGN_PUBLIC_KEY_CREDENTIAL_ID` | Promotion verification key |
| `FALCON_CLIENT_ID_CREDENTIAL_ID` / `FALCON_CLIENT_SECRET_CREDENTIAL_ID` | FCS runtime client |
| `AI_API_KEY_CREDENTIAL_ID` | Inference credential |
| `SCM_MIRROR_CREDENTIAL_ID` / `SCM_REMEDIATION_CREDENTIAL_ID` | Mirror and branch-publisher credentials |

Use workload identity from each Kubernetes ServiceAccount to mint short-lived
Artifactory credentials. Scope credential providers and Jenkins folders so an
untrusted change-request job cannot resolve protected credential IDs. Run
release stages from a separately protected job whose pipeline definition is
loaded from the default branch; repository guards alone cannot protect a
Jenkinsfile modified by an untrusted change request. The Gov `input` step must
use folder-level RBAC backed by the configured U.S.-person group.

## Scanner evidence configuration

Grype uses `GRYPE_DB_CACHE_DIR` and `FACTORY_KEV_PATH` (defaults under
`/opt/security-data`). Both database and KEV feed must satisfy the catalog's
freshness limit. An optional `FACTORY_GRYPE_BASELINE` needs an adjacent `.sig`
and trusted `FACTORY_BASELINE_PUBLIC_KEY`; see the
[baseline contract](reviews/harness-remediation-plan.md#remediation-progress-2026-09-15).

FCS requires `FACTORY_FCS_REPORT_SCHEMA`, a local reviewed JSON Schema for its
pinned CLI report. Bundle schema references locally. The schema setting is part of Jenkins backend selection. An unset setting uses
Grype; a selected FCS backend with an unreadable or invalid schema fails closed
and does not switch scanners after selection.

## CrowdStrike FCS assessment

CrowdStrike FCS CLI 4.x is the preferred assessment when enabled and configured.
Otherwise Jenkins automatically schedules Syft/Grype for the release gate.
See [selection settings and offline prerequisites](reviews/harness-remediation-plan.md#immediate-change-fcs-preference-with-syftgrype-fallback). It runs
on the protected Kubernetes pod template named by
`FACTORY_K8S_FCS_POD_TEMPLATE` against the exact candidate loaded from
`image.oci.tar` into rootless Podman. The CLI uses the
image assessment policy configured in the environment's Falcon console: exit
code zero passes, while any nonzero exit, malformed report, missing report, or
invalid FCS SBOM fails closed in the OPA gate.

FCS produces its native JSON assessment and a CycloneDX JSON SBOM. CrowdStrike's
public FCS image-scan interface does not support SPDX output; the existing Syft
job remains responsible for `sbom.spdx.json`. Grype, Trivy, OSV, and ClamAV
continue to publish informational evidence (the first three in the normalized
finding document and ClamAV in its native text report) in the optional SCAN stage. A separate Grype assessment becomes authoritative
when FCS is disabled or unconfigured. Compliance, product tests, SBOM validity, approvals, and evidence
signatures remain independently blocking.

The FCS job uses a dedicated runner image built by
`toolchain/Containerfile.factory-fcs-runner`. Stage the entitlement-protected,
Falcon-API-downloaded executable at `dist/fcs/fcs` only for that ignored build
context; do not commit the executable. The bootstrap process must verify the
download API's SHA-256 before building and signing the runner image.

Use separate API clients and image assessment policies for commercial, Gov1,
and Gov2. The client requires the CrowdStrike container CLI/image scopes and
must be available only to the FCS runner. The runner needs outbound access to
the selected Falcon region but no Artifactory write, signing, exception, or
promotion credential.

Configure each runtime client with `Cloud Security Tools Download: READ`,
`Falcon Container CLI: READ & WRITE`, and `Falcon Container Image: READ &
WRITE`. Scope `FALCON_CLIENT_ID`, `FALCON_CLIENT_SECRET`, and `FALCON_REGION`
to the matching protected Jenkins release job and Kubernetes ServiceAccount.

For a connected local assessment after building an image:

```bash
export FALCON_CLIENT_ID=...
export FALCON_CLIENT_SECRET=...
export FALCON_REGION=us-1
make local-fcs IMAGE=jira-lts
```

The assessment, FCS CycloneDX SBOM, logs, and fail-closed status document are
written under `work/<image>/evidence/scans/fcs/`.

## Signing with Artifactory

The release pipeline uses Cosign key-pair signing. Artifactory stores the image,
signature and in-toto attestations as digest-linked registry artifacts; it does
not hold or operate the private key. Artifactory 7.90.1 or newer is required for
OCI 1.1 Referrers API support.

Create a different encrypted Cosign key pair for each `FACTORY_RELEASE_ENV`:

```bash
cosign generate-key-pair --output-key-prefix cosign-commercial
```

Configure these Jenkins credentials for each protected signing environment:

- Store the generated `.key` in the file credential selected by
  `COSIGN_KEY_CREDENTIAL_ID`.
- Store its password in the credential selected by
  `COSIGN_PASSWORD_CREDENTIAL_ID`.
- Store the `.pub` file in the credential selected by
  `COSIGN_PUBLIC_KEY_CREDENTIAL_ID`.
- Use the signing pod's workload identity to obtain the short-lived token
  selected by `ARTIFACTORY_SIGN_CREDENTIAL_ID`.

The signing identity should be able to read candidate manifests and create
signature and attestation attachment tags in quarantine. It should not be able to
overwrite candidate manifests or write to release repositories. The promotion
identity separately verifies the signature, copies the subject and complete
referrer graph and Cosign attachment tags, checks that the digest did not change, and verifies the copied
signature.

Before enabling production promotion, confirm that Artifactory returns the
Cosign artifacts for a signed candidate:

```bash
oras discover "${ARTIFACTORY_REGISTRY}/${FACTORY_QUARANTINE_REPOSITORY}/${FACTORY_IMAGE_PATH}@${IMAGE_DIGEST}"
```

## Pipeline stage toggles

Each pipeline stage is controlled by a Jenkins boolean parameter. Only catalog
validation is enabled by default. The Jenkinsfile rejects combinations that
omit a required predecessor; for example, the policy gate requires build,
SBOM, compliance, and test stages, plus an automatically selected assessment.

| Variable | Default | Stage controlled |
|---|---|---|
| `FACTORY_ENABLE_VALIDATE` | `true` | Schema and context validation |
| `FACTORY_ENABLE_PREPARE` | `false` | Resource-lock resolution and build context assembly |
| `FACTORY_ENABLE_BUILD` | `false` | Rootless BuildKit OCI build |
| `FACTORY_ENABLE_SBOM` | `false` | Syft SBOM generation |
| `FACTORY_ENABLE_SCAN` | `false` | Grype/Trivy/OSV/ClamAV informational scans |
| `FACTORY_ENABLE_HELMPER` | `false` | Helmper-style chart inventory evidence |
| `FACTORY_ENABLE_COPA` | `false` | Copacetic-style patch planning evidence |
| `FACTORY_ENABLE_FCS` | `false` | Prefer FCS when configured; otherwise schedule Grype |
| `FACTORY_ENABLE_COMPLIANCE` | `false` | OpenSCAP compliance scan |
| `FACTORY_ENABLE_TEST` | `false` | Product integration tests |
| `FACTORY_ENABLE_GATE` | `false` | OPA policy gate |
| `FACTORY_ENABLE_REMEDIATE` | `false` | AI read-only remediation summary |
| `FACTORY_ENABLE_REMEDIATION_BRANCH` | `false` | Protected publication of an agent-proposed branch |
| `FACTORY_ENABLE_IMPORT` | `false` | Protected quarantine import |
| `FACTORY_ENABLE_ATTEST` | `false` | Cosign signing and attestation |
| `FACTORY_ENABLE_HUMMINGBIRD` | `false` | Hummingbird-style reproducibility summary |
| `FACTORY_ENABLE_PROMOTE` | `false` | Pull-based release promotion |

Optional concept-stage commands are provided through environment settings:

| Variable | Default | Purpose |
|---|---|---|
| `FACTORY_HELMPER_COMMAND` | unset | Command run by `scripts/helmper_inventory.sh` when Helmper stage is enabled |
| `FACTORY_COPA_COMMAND` | unset | Command run by `scripts/copacetic_patch_plan.sh` when Copacetic stage is enabled |
| `FACTORY_HUMMINGBIRD_COMMAND` | unset | Optional extra verification command run by `scripts/hummingbird_verify.sh` |

## Source-pin management

Two repository tools keep catalog source revisions synchronized with an
operator-selected upstream branch.

**vendir** (`vendir/config.yml`) declares the five Repo One Git sources and
their pinned commit references. Run `vendir sync` to update the checked-out
content under `vendor/repo1/`.

`scripts/update_source_pins.sh` queries each catalog's upstream URL with
`git ls-remote` and updates both `source.revision` and the matching
`vendir/config.yml` reference. Run it from a connected Jenkins intake job,
review the resulting diff, and publish it through the organization's
SCM-controlled branch workflow:

```bash
FACTORY_UPSTREAM_BRANCH="${FACTORY_UPSTREAM_BRANCH:?}" make update-pins
```

## Toolchain pinning

`tools/versions.lock.yaml` records the pinned version and upstream project URL
for every tool embedded in the factory runner images (BuildKit, RootlessKit, Skopeo, Umoci,
ORAS, Cosign, Syft, Grype, Trivy, OSV Scanner, OPA, OpenSCAP,
ComplianceAsCode, and the FCS CLI). Update this file when bumping a tool version
and rebuild and re-sign both toolchain images.

Bootstrap must checksum-verify the pinned platform-specific BuildKit and
RootlessKit release bundles, then stage their executables in `dist/tools/`.
Include `buildctl`, `buildkitd`, `buildkit-runc` (the runtime bundled with
BuildKit), and `rootlesskit`; the factory runner build fails if they are absent.
Keep the bundled runtime and client/daemon versions together. Podman and its
setuid ID-mapping helpers remain installed for downstream tests and scanners.

## Legacy policy settings

`policies/exceptions/approved.json` is retained for compatibility but is not read
by the active Rego rules. Catalog `policy.block` and database-age values describe
legacy scanner intent; they do not enforce Falcon tenant policy or grant an
exception. Govern vulnerability exceptions in the authoritative assessment
system. Never assume editing the empty JSON file authorizes a release.

## Cosign storage and promotion compatibility

The pinned Cosign 2.x commands use digest-derived attachment tags. The earlier
referrer-only description is insufficient: both ORAS recursive referrers and
Cosign attachments must be copied. See [architecture](architecture.md#storage-compatibility).
The scripts create a temporary Docker-compatible auth file shared by Cosign,
ORAS and Skopeo, authenticate before verification, and delete it on exit.
