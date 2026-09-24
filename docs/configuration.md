# Configuration reference

[Project overview](../README.md) · [Operations](operations.md)

## Quick setup checklist (new operators)

Use this section when you are standing up the pipeline for the first time.

1. **Prepare Jenkins Kubernetes pod templates**
   - Configure trust-class templates and map them to `FACTORY_K8S_*_POD_TEMPLATE` settings.
   - Use unprivileged/rootless-compatible templates (no host mounts, no engine socket mounts, no privileged mode).

2. **Publish runner images and pin by digest**
   - Set `FACTORY_RUNNER_IMAGE` and `FACTORY_INTAKE_RUNNER_IMAGE` to signed digest-qualified references.

3. **Set required repository and routing settings**
   - `INTERNAL_GIT_BASE_URL`
   - `ARTIFACTORY_URL`, `ARTIFACTORY_REGISTRY`
   - `FACTORY_SOURCE_REPOSITORY`, `UPSTREAM_OCI_REPOSITORY`
   - quarantine/release repositories (`FACTORY_BASE_QUARANTINE_REPOSITORY`, `FACTORY_APPLICATION_QUARANTINE_REPOSITORY`, `FACTORY_RELEASE_REPOSITORY`, `FACTORY_CANARY_REPOSITORY`)

4. **Bind Jenkins credential ID settings**
   - Configure read/write/sign/release Artifactory credential IDs.
   - Configure Cosign key/password/public-key credential IDs.
   - Configure SCM mirror/remediation credential IDs if remediation flows are enabled.

5. **Run minimal pipeline stages first**
   - Start with `VALIDATE`, `PREPARE`, `BUILD`, `SBOM`, `SCAN`, `COMPLIANCE`, `TEST`, `GATE`.
   - Enable `IMPORT`/`ATTEST`/`PROMOTE` only after validation and access controls are confirmed.

6. **Verify local contributor workflow**
   - Confirm contributors can run `make validate`, `make test`, `make lint`, and `make plan`.

---

## Advanced configuration reference

### Jenkins on unprivileged Kubernetes agents

`Jenkinsfile` runs the image factory and `Jenkinsfile.intake` runs connected
intake. Both use the Jenkins Kubernetes plugin and add a `factory` container to
an administrator-managed pod template.

- Set `privileged: false`.
- Do not mount host paths, container-engine sockets, or host devices.
- Do not add Linux capabilities just to bypass rootless constraints.

The runner executes as UID 10001, uses subordinate IDs for rootless BuildKit
and Podman, and relies on the outer pod for cgroup enforcement. BuildKit uses
its native snapshotter; Podman VFS remains for scanner/product-test storage.

Nodes must allow unprivileged user namespaces. `/tmp` and `/home/factory` must
be writable.

Pod template trust classes are selected with these settings:

| Setting | Trust class |
|---|---|
| `FACTORY_K8S_INTAKE_POD_TEMPLATE` | Approved upstream and intake-only writes |
| `FACTORY_K8S_OFFLINE_POD_TEMPLATE` | Internal read-only analysis |
| `FACTORY_K8S_BUILDKIT_POD_TEMPLATE` | Rootless, internal-only build |
| `FACTORY_K8S_FIPS_POD_TEMPLATE` | FIPS-node compliance |
| `FACTORY_K8S_TEST_POD_TEMPLATE` | Rootless product tests |
| `FACTORY_K8S_AI_POD_TEMPLATE` | AI endpoint and read-only evidence |
| `FACTORY_K8S_REMEDIATION_POD_TEMPLATE` | SCM branch publication only |
| `FACTORY_K8S_IMPORT_POD_TEMPLATE` | Quarantine writes only |
| `FACTORY_K8S_SIGNING_POD_TEMPLATE` | Signature/referrer writes only |
| `FACTORY_K8S_PROMOTION_POD_TEMPLATE` | Verified release copy only |

Set runner images as signed digest-qualified references:

- `FACTORY_RUNNER_IMAGE`
- `FACTORY_INTAKE_RUNNER_IMAGE`

Use object-storage-backed Jenkins artifact storage for large OCI evidence.

### BuildKit pod template details

Register [the BuildKit pod YAML](../toolchain/jenkins-buildkit-pod.yaml) as the
template named by `FACTORY_K8S_BUILDKIT_POD_TEMPLATE`, then replace image
placeholder and service account with deployment-specific values.

The build runs an ephemeral `buildkitd` **inside the factory container**.
No shared daemon, no host socket, no privileged sidecar.

`FACTORY_BUILD_NETWORK` accepts:

- `default` (production)
- `none`
- `host` (explicit only)

Build inputs are digest-verified OCI layouts. A native source policy blocks
remote image/HTTP/Git sources during solve.

### Jenkins settings and credentials

#### Core settings

| Setting | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` | Internal SCM namespace containing source mirrors |
| `SCM_REPOSITORY_URL` | Factory repository push URL used by remediation broker |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Artifactory API base URL and OCI registry host |
| `FACTORY_SOURCE_REPOSITORY` | Repository for locks and intake content |
| `UPSTREAM_OCI_REPOSITORY` | OCI repository for digest-pinned upstream bases |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` / `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | Protected candidate repositories |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | Release and canary repositories |
| `FACTORY_DEFAULT_BRANCH` | Only branch allowed to import/sign/promote |
| `FACTORY_IMPORT_LOCK_PREFIX` / `FACTORY_PROMOTION_LOCK_PREFIX` | Lockable Resources prefixes |
| `FACTORY_GOV_APPROVERS` / `FACTORY_GOV_APPROVER_PATTERN` | Gov approval guardrails |
| `SCM_REMEDIATION_AUTHOR_NAME` / `SCM_REMEDIATION_AUTHOR_EMAIL` | Bot identity for remediation commits |
| `FACTORY_UPSTREAM_BRANCH` | Upstream branch used by source-pin maintenance |
| `AI_BASE_URL` / `AI_MODEL` | Approved inference endpoint and model |

#### Credential ID settings

| Credential ID setting | Bound value |
|---|---|
| `ARTIFACTORY_READ_CREDENTIAL_ID` | Read-only Artifactory token |
| `ARTIFACTORY_INTAKE_WRITE_CREDENTIAL_ID` | Intake-only write token |
| `ARTIFACTORY_WRITE_CREDENTIAL_ID` | Quarantine importer token |
| `ARTIFACTORY_SIGN_CREDENTIAL_ID` | Referrer-write token |
| `ARTIFACTORY_RELEASE_CREDENTIAL_ID` | Release-copy token |
| `COSIGN_INTAKE_KEY_CREDENTIAL_ID` / `COSIGN_INTAKE_PASSWORD_CREDENTIAL_ID` | Intake signing key and password |
| `COSIGN_INTAKE_PUBLIC_KEY_CREDENTIAL_ID` | Intake verification key |
| `COSIGN_KEY_CREDENTIAL_ID` / `COSIGN_PASSWORD_CREDENTIAL_ID` | Environment signing key and password |
| `COSIGN_PUBLIC_KEY_CREDENTIAL_ID` | Promotion verification key |
| `AI_API_KEY_CREDENTIAL_ID` | Inference credential |
| `SCM_MIRROR_CREDENTIAL_ID` / `SCM_REMEDIATION_CREDENTIAL_ID` | Mirror and branch-publisher credentials |

### Scanner evidence configuration (delegated scanners)

The authoritative assessment backend is `delegated-scanners`, using normalized
evidence from Grype, Trivy, Syft metadata, and OSV Scanner outputs.

- Grype database and KEV freshness are enforced by policy through the prepared
  evidence payload.
- Assessment status is produced in
  `work/<image>/evidence/scans/delegated/status.json`.
- Policy may warn (instead of deny) for fixable high/critical findings outside
  the application archive depending on normalized location context.

### Signing with Artifactory

The release pipeline uses Cosign key-pair signing. Artifactory stores subject,
signature, and in-toto attestations as digest-linked registry artifacts. Keep
private keys in Jenkins file credentials, never in the repository.

Create separate key pairs per `FACTORY_RELEASE_ENV`:

```bash
cosign generate-key-pair --output-key-prefix cosign-commercial
```

Before production promotion, verify discoverability of referrers:

```bash
oras discover "${ARTIFACTORY_REGISTRY}/${FACTORY_APPLICATION_QUARANTINE_REPOSITORY}/${FACTORY_IMAGE_PATH}@${IMAGE_DIGEST}"
```

### Pipeline stage toggles

Each stage is controlled by a Jenkins boolean parameter.

| Variable | Default | Stage controlled |
|---|---|---|
| `FACTORY_ENABLE_VALIDATE` | `true` | Schema and context validation |
| `FACTORY_ENABLE_PREPARE` | `false` | Resource-lock resolution and context assembly |
| `FACTORY_ENABLE_BUILD` | `false` | Rootless BuildKit OCI build |
| `FACTORY_ENABLE_SBOM` | `false` | Syft SBOM generation |
| `FACTORY_ENABLE_SCAN` | `false` | Grype/Trivy/OSV/ClamAV evidence collection |
| `FACTORY_ENABLE_ASSESSMENT` | `false` | Delegated scanner assessment artifact stage |
| `FACTORY_ENABLE_HELMPER` | `false` | Helmper-style chart inventory evidence |
| `FACTORY_ENABLE_COPA` | `false` | Copacetic-style patch planning evidence |
| `FACTORY_ENABLE_COMPLIANCE` | `false` | OpenSCAP compliance scan |
| `FACTORY_ENABLE_TEST` | `false` | Product integration tests |
| `FACTORY_ENABLE_GATE` | `false` | OPA policy gate |
| `FACTORY_ENABLE_REMEDIATE` | `false` | AI read-only remediation summary |
| `FACTORY_ENABLE_REMEDIATION_BRANCH` | `false` | Protected publication of remediation branch |
| `FACTORY_ENABLE_IMPORT` | `false` | Protected quarantine import |
| `FACTORY_ENABLE_ATTEST` | `false` | Cosign signing and attestation |
| `FACTORY_ENABLE_HUMMINGBIRD` | `false` | Reproducibility summary |
| `FACTORY_ENABLE_PROMOTE` | `false` | Pull-based release promotion |

Optional concept-stage commands:

| Variable | Default | Purpose |
|---|---|---|
| `FACTORY_HELMPER_COMMAND` | unset | Command run by `scripts/helmper_inventory.sh` |
| `FACTORY_COPA_COMMAND` | unset | Command run by `scripts/copacetic_patch_plan.sh` |
| `FACTORY_HUMMINGBIRD_COMMAND` | unset | Optional extra verification command in `scripts/hummingbird_verify.sh` |

### Source-pin management

- `vendir/config.yml` pins Repo One Git sources.
- `vendir sync` refreshes checked-out content under `vendor/repo1/`.
- `scripts/update_source_pins.sh` updates `source.revision` and matching
  `vendir/config.yml` entries.

Run from a connected intake environment:

```bash
FACTORY_UPSTREAM_BRANCH="${FACTORY_UPSTREAM_BRANCH:?}" make update-pins
```

### Toolchain pinning

`tools/versions.lock.yaml` records pinned tools and source URLs for runner
images. Update pins deliberately, rebuild toolchain images, and re-sign them.

### Vulnerability exclusions

`policies/exceptions/approved.json` holds approved vulnerability exclusions.
These affect vulnerability-threshold denials only; they do not bypass evidence
identity/freshness, compliance, tests, or signature checks.

### Cosign storage and promotion compatibility

Cosign 2.x uses digest-derived attachment tags. Promotion must copy both ORAS
recursive referrers and Cosign attachment tags.
