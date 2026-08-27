# Image Hardening Factory

Jenkins reference implementation for rebuilding selected Iron Bank source
repositories using internal Git and Artifactory. Jenkins provisions every job
as an unprivileged Kubernetes pod; the build and release path has no Registry1
or Iron Bank platform dependency.

The initial catalog contains:

- Red Hat UBI 9 Minimal 9.8
- Red Hat UBI 10 Minimal 10.2 (canary only)
- Atlassian Bitbucket Data Center LTS 10.2.6
- Atlassian Confluence Data Center LTS 10.2.15
- Atlassian Jira Data Center LTS 11.3.10

## Trust model

1. The connected intake runner is the only runner allowed to reach approved
   upstream endpoints.
2. It mirrors Git commits, downloads checksum-pinned resources, imports OCI
   bases by digest, and publishes an immutable resource lock.
3. Rootless build pods have access only to internal Git, immutable Artifactory
   repositories, and the Jenkins agent endpoint.
4. Build jobs emit OCI layouts and do not hold release credentials or use
   privileged pods.
5. A protected FCS runner performs the authoritative image-security assessment
   against the exact local OCI candidate; other vulnerability scanners are
   informational.
6. A protected importer writes passing images to quarantine.
7. A separate protected job signs the digest and its attestations with an
   environment-local Cosign key. Signatures and attestations are stored in
   Artifactory as OCI referrers.
8. Promotion copies the exact digest and its referrer closure. It never
   rebuilds an image.
9. AI jobs can summarize evidence or create a patch proposal. They cannot
   change policy, VEX, exceptions, signing, publication, or approval state.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
factory validate --catalog catalog/images
factory plan --catalog catalog/images --all --output generated-jenkins-plan.json
python3 -m unittest discover -s tests/unit -p 'test_*.py' -v
```

For local pipeline rendering without installing the package:

```bash
PYTHONPATH=. python3 -m factory.cli validate --catalog catalog/images
PYTHONPATH=. python3 -m factory.cli plan \
  --catalog catalog/images --changed ubi9-minimal \
  --output generated-jenkins-plan.json
```

Common Makefile targets:

| Target | Description |
|---|---|
| `make validate` | Run `factory validate` against the catalog |
| `make plan` | Render the dependency-aware Jenkins execution plan |
| `make test` | Run unit tests |
| `make lint` | Run `ruff` linter and format check |
| `make local-build IMAGE=<name> ...` | Build an image locally |
| `make local-test IMAGE=<name> ...` | Build and run baseline tests locally |
| `make local-fcs IMAGE=<name> ...` | Build and run a local FCS assessment |
| `make update-pins` | Refresh catalog and vendir revisions from the configured upstream branch |
| `make package` | Create a `git archive` tarball of the repository |

## CLI subcommands

The `factory` entry point exposes five subcommands:

| Subcommand | Description |
|---|---|
| `factory validate --catalog <dir>` | Validate all catalog YAML files against the schema and print the image list |
| `factory plan --catalog <dir> (--all \| --changed <name...>) --output <file>` | Render a dependency-aware Jenkins execution plan as JSON |
| `factory intake --image <catalog.yaml> --source-dir <dir> --cache-dir <dir> --output <file> [--upload-url <url>]` | Resolve and checksum-verify a hardening manifest; optionally upload locked files to Artifactory |
| `factory gate-input --image <name> --image-digest <digest> --sbom <file> --findings <file> --compliance <file> --tests <file> --database-status <file> --fcs-status <file> --output <file>` | Assemble the OPA gate input document from evidence artifacts |
| `factory normalize-findings --grype <file> --trivy <file> [--osv <file>] [--kev <file>] [--baseline <file>] --output <file>` | Normalize Grype, Trivy, and OSV scanner results into a unified finding document, annotated with KEV and new/baseline flags |

## Local image builds

The local development workflow uses rootless Podman and Buildah with a temporary
loopback OCI registry. Three RPM source modes are available:

| Mode | Flag | Credentials required |
|---|---|---|
| **Direct public CDN** | `LOCAL_USE_UPSTREAM_UBI_REPOS=true` | None — UBI is publicly accessible |
| **Artifactory pull-through cache** | `ARTIFACTORY_URL`, `ARTIFACTORY_READ_TOKEN`, `LOCAL_RPM_CACHE_REPOSITORY` | Artifactory token |
| **Complete local snapshot** | `LOCAL_RPM_REPO_DIR=/path/to/snapshot` | None |

The direct CDN mode builds against `cdn-ubi.redhat.com` without Artifactory.
It is convenient for initial setup and zero-credential development, but upstream
content may change between builds, making these builds non-reproducible.  The
resulting lock is marked `localDevelopment: true` and cannot enter import,
signing, or promotion.

The Artifactory pull-through cache mode fetches RPMs via the internal Artifactory
remote repository.  The complete local snapshot mode serves an existing snapshot
from a loopback HTTP server.  Both modes also produce development-only locks.

The workflow applies the catalog overlay, downloads and checksum-verifies
manifest resources, generates a clearly marked development resource lock, and
publishes the result to both an OCI archive and the local registry.  Catalog
base images are built automatically before an application image.

Prerequisites are Python 3.11+, Git, Curl, Podman, Buildah, Skopeo, `yq`, and
`jq`. Umoci is also required for malware and compliance scans. Podman and
Buildah must run rootless.  For snapshot-based testing,
`LOCAL_RPM_REPO_DIR` must point to a complete RPM repository containing
`repodata/repomd.xml`. Signature checking remains enabled by default, so the
repository must also contain valid RPM and repository signatures trusted by
the source image.

```bash
make local-build IMAGE=ubi9-minimal
```

Set `LOCAL_RPM_CACHE_REPOSITORY` to the Artifactory remote-repository key before
using the connected workflow. The repository must proxy
`https://cdn-ubi.redhat.com` while retaining the upstream
`content/public/ubi` path:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_RPM_CACHE_REPOSITORY="${LOCAL_RPM_CACHE_REPOSITORY:?}"
```

To build an Atlassian image and its UBI dependency:

```bash
make local-build IMAGE=jira-lts
```

To build from a complete local snapshot instead of Artifactory:

```bash
make local-build IMAGE=ubi9-minimal \
  LOCAL_RPM_REPO_DIR=/absolute/path/to/ubi9-snapshot
```

By default, sources are cloned from each catalog entry's upstream URL. For
offline development, arrange pinned local mirrors as `<root>/<image>` and set:

```bash
export LOCAL_SOURCE_ROOT=/absolute/path/to/source-mirrors
```

The outputs are written to `work/<image>/image.oci.tar`,
`work/<image>/image-metadata.json`, and `work/<image>/resource-lock.json`. The
lock contains `"localDevelopment": true`; it is not signed and must never be
used for quarantine import or release. Run the image's baseline test profile
after building with:

```bash
make local-test \
  IMAGE=ubi9-minimal
```

Useful overrides include `LOCAL_REGISTRY` (loopback only, default
`127.0.0.1:5000`), `LOCAL_RPM_PORT` (default `18080`), and
`LOCAL_KEEP_REGISTRY=true`. The internal cache defaults to the URL
`${ARTIFACTORY_URL}/artifactory/${LOCAL_RPM_CACHE_REPOSITORY}/content/public/ubi`.
Set `LOCAL_RPM_CACHE_UBI_ROOT_URL` when the Artifactory remote repository maps
the upstream path at a different root. Setting `LOCAL_RPM_GPGCHECK=0` or
`LOCAL_RPM_REPO_GPGCHECK=0` is available only for disposable development data
and weakens parity with the production build. `LOCAL_RPM_SSLVERIFY=0` also
disables repository TLS verification and should be used only as a last-resort
diagnostic override.

On an SELinux-enforcing host such as the Fedora CoreOS guest used by Podman
Machine, the generated repository file is privately relabeled for the build
container. Newer Skopeo releases may also refuse to copy upstream transport
signatures into local registries or OCI archives that cannot store them; the
local workflow explicitly removes those transport signatures while retaining
digest verification and retries transient registry failures.

If local HTTPS access requires an additional corporate CA, pass a single PEM
certificate with `LOCAL_CA_CERT`. For UBI 9 local builds, the certificate is
copied into the development context and activated before the first RPM access:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_CA_CERT=/absolute/path/to/corporate-ca.crt
```

This adds the CA to the resulting development image and therefore expands its
trust store. Review that addition before manually placing the image in a shared
quarantine repository. Do not pass a bundle of unrelated trust roots when the
specific issuing CA is available.

If only the host trust bundle is available, set `LOCAL_CA_BUNDLE` instead. The
local workflow passes the bundle to Curl for repository metadata downloads and,
when `LOCAL_CA_CERT` is unset, installs it in the UBI 9 development image before
the first RPM access:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_CA_BUNDLE=/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
```

When both variables are set, `LOCAL_CA_BUNDLE` configures host Curl and
`LOCAL_CA_CERT` is the certificate installed in the image.

For the fastest zero-credential development build, set `LOCAL_USE_UPSTREAM_UBI_REPOS=true`
to pull RPMs directly from the public UBI CDN:

```bash
make local-build IMAGE=ubi9-minimal LOCAL_USE_UPSTREAM_UBI_REPOS=true
```

This mode fetches BaseOS and AppStream from `cdn-ubi.redhat.com`, requires no
`ARTIFACTORY_URL`, `ARTIFACTORY_READ_TOKEN`, or `LOCAL_RPM_CACHE_REPOSITORY`,
and keeps RPM GPG checking and TLS verification enabled.  Both channel
`repomd.xml` documents are fetched and hashed into a deterministic composite
metadata digest; the CDN origin is recorded as `developmentSource` in the lock.
Because upstream content can change between builds, these direct CDN builds are
convenient but non-reproducible.  The lock remains marked
`"localDevelopment": true` and cannot be imported, signed, or promoted.

For the simplest Artifactory-backed connected development build, set the two
credentials and run the target directly:

```bash
make local-build IMAGE=ubi9-minimal
```

The workflow selects UBI 9 or UBI 10 from the catalog dependency and requests
BaseOS and AppStream only from the authenticated internal cache. It downloads
and hashes both `repomd.xml` documents and records a composite metadata digest
in the local development lock. The generated repo file enables both channels
and its temporary directory is bind-mounted over `/etc/yum.repos.d` during the
Buildah build. This masks repository files inherited from the upstream UBI base
image while leaving the directory writable for `librhsm` initialization. RPM
operations therefore cannot fall back to Red Hat's public CDN or other
repository files inherited from the base image.  Because pull-through metadata
can change, these local development locks remain ineligible for quarantine
import or promotion.

To evaluate an application against the UBI 10 canary without changing its
production catalog dependency, set the local base override:

```bash
make local-build \
  IMAGE=jira-lts \
  LOCAL_BASE_IMAGE_OVERRIDE=ubi10-minimal

make local-test \
  IMAGE=jira-lts \
  LOCAL_BASE_IMAGE_OVERRIDE=ubi10-minimal
```

The override is accepted only for application images and must name a catalog
base image. The workflow builds the selected base first, uses its exact local
OCI archive, selects the matching UBI RPM repositories, and records both the
catalog base and effective base under `developmentBaseOverride` in the local
lock. It does not alter `catalog/images/jira-lts.yaml`; production continues to
depend on UBI 9.

The Atlassian overlays receive `BASE_MAJOR` from the effective base. Their
legacy forced RPM removals remain active for UBI 9, but are skipped for UBI 10
because its Java 21 package requires libraries such as `cups-libs`. RPM
verification ignores timestamp-only changes caused by reproducible layer
timestamps, combines the application allowlist with the base allowlist, and
adds the reviewed UBI 10 hardening deviations from
`tests/profiles/base/rpm-verify.ubi10.allow`.

## Jenkins on unprivileged Kubernetes agents

`Jenkinsfile` runs the image factory and `Jenkinsfile.intake` runs connected
intake. Both use the Jenkins Kubernetes plugin and add a `factory` container to
an administrator-managed pod template. Every template must set
`privileged: false`; it must not mount host paths, container-engine sockets,
host devices, or add Linux capabilities. The runner executes as UID 10001,
uses subordinate IDs for rootless Buildah and Podman, stores containers with
VFS, and lets the outer pod enforce cgroup limits.

Runner nodes must allow unprivileged user namespaces. `/tmp` and
`/home/factory` must be writable, and the runtime seccomp profile must permit
the user-namespace operations used by rootless Buildah and Podman. ClamAV and
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
| `FACTORY_K8S_BUILDAH_POD_TEMPLATE` | Rootless, internal-only build |
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

## CrowdStrike FCS assessment

CrowdStrike FCS CLI 4.x is the authoritative image-security assessment. It runs
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
finding document and ClamAV in its native text report) but no longer make release
decisions. Compliance, product tests, SBOM validity, approvals, and evidence
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
signature and in-toto attestations as one OCI subject/referrer graph; it does
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
signature and attestation referrers in quarantine. It should not be able to
overwrite candidate manifests or write to release repositories. The promotion
identity separately verifies the signature, copies the subject and complete
referrer graph, checks that the digest did not change, and verifies the copied
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
SBOM, FCS, compliance, and test stages.

| Variable | Default | Stage controlled |
|---|---|---|
| `FACTORY_ENABLE_VALIDATE` | `true` | Schema and context validation |
| `FACTORY_ENABLE_PREPARE` | `false` | Resource-lock resolution and build context assembly |
| `FACTORY_ENABLE_BUILD` | `false` | Rootless Buildah OCI build |
| `FACTORY_ENABLE_SBOM` | `false` | Syft SBOM generation |
| `FACTORY_ENABLE_SCAN` | `false` | Grype/Trivy/OSV/ClamAV informational scans |
| `FACTORY_ENABLE_FCS` | `false` | CrowdStrike FCS authoritative assessment |
| `FACTORY_ENABLE_COMPLIANCE` | `false` | OpenSCAP compliance scan |
| `FACTORY_ENABLE_TEST` | `false` | Product integration tests |
| `FACTORY_ENABLE_GATE` | `false` | OPA policy gate |
| `FACTORY_ENABLE_REMEDIATE` | `false` | AI read-only remediation summary |
| `FACTORY_ENABLE_REMEDIATION_BRANCH` | `false` | Protected publication of an agent-proposed branch |
| `FACTORY_ENABLE_IMPORT` | `false` | Protected quarantine import |
| `FACTORY_ENABLE_ATTEST` | `false` | Cosign signing and attestation |
| `FACTORY_ENABLE_PROMOTE` | `false` | Pull-based release promotion |

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
for every tool embedded in the factory runner images (Buildah, Skopeo, Umoci,
ORAS, Cosign, Syft, Grype, Trivy, OSV Scanner, OPA, OpenSCAP,
ComplianceAsCode, and the FCS CLI). Update this file when bumping a tool version
and rebuild and re-sign both toolchain images.

## Exception management

`policies/exceptions/approved.json` holds the OPA-visible approved exception
set. The current schema is:

```json
{
  "factory": {
    "exceptions": {
      "approved": {}
    }
  }
}
```

Add per-finding exception records under the `approved` object. The OPA bundle
includes this file; changes require a policy-controlled review and rebuild.

## Repository operation

The root Jenkins pipeline renders a dependency-aware JSON execution plan. A
UBI 9 change selects UBI 9 plus the three Atlassian descendants. A UBI 10
change selects only its canary path until an Atlassian catalog entry explicitly
changes its base dependency. Dependency waves execute with Jenkins `parallel`;
each application waits until its selected base pipeline has completed.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the ten implementation
milestones and [docs/operations.md](docs/operations.md) for environment setup.
