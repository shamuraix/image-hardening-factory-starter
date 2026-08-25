# Image Hardening Factory

GitLab CI reference implementation for rebuilding selected Iron Bank source
repositories using internal Git and Artifactory, with no Registry1 or Iron Bank
platform dependency in the build or release path.

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
3. Rootless build runners have access only to internal Git, immutable
   Artifactory repositories, and the GitLab coordinator.
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
factory pipeline --catalog catalog/images --all --output generated-child.yml
python3 -m unittest discover -s tests/unit -p 'test_*.py' -v
```

For local pipeline rendering without installing the package:

```bash
PYTHONPATH=. python3 -m factory.cli validate --catalog catalog/images
PYTHONPATH=. python3 -m factory.cli pipeline \
  --catalog catalog/images --changed ubi9-minimal \
  --output generated-child.yml
```

Common Makefile targets:

| Target | Description |
|---|---|
| `make validate` | Run `factory validate` against the catalog |
| `make pipeline` | Render the full child pipeline to `generated-child.yml` |
| `make test` | Run unit tests |
| `make lint` | Run `ruff` linter and format check |
| `make local-build IMAGE=<name> ...` | Build an image locally |
| `make local-test IMAGE=<name> ...` | Build and run baseline tests locally |
| `make local-fcs IMAGE=<name> ...` | Build and run a local FCS assessment |
| `make package` | Create a `git archive` tarball of the repository |

## CLI subcommands

The `factory` entry point exposes five subcommands:

| Subcommand | Description |
|---|---|
| `factory validate --catalog <dir>` | Validate all catalog YAML files against the schema and print the image list |
| `factory pipeline --catalog <dir> (--all \| --changed <name...>) --output <file>` | Render a dependency-aware child pipeline YAML |
| `factory intake --image <catalog.yaml> --source-dir <dir> --cache-dir <dir> --output <file> [--upload-url <url>]` | Resolve and checksum-verify a hardening manifest; optionally upload locked files to Artifactory |
| `factory gate-input --image <name> --image-digest <digest> --sbom <file> --findings <file> --compliance <file> --tests <file> --database-status <file> --fcs-status <file> --output <file>` | Assemble the OPA gate input document from evidence artifacts |
| `factory normalize-findings --grype <file> --trivy <file> [--osv <file>] [--kev <file>] [--baseline <file>] --output <file>` | Normalize Grype, Trivy, and OSV scanner results into a unified finding document, annotated with KEV and new/baseline flags |

## Local image builds

The local development workflow uses rootless Podman and Buildah with a temporary
loopback OCI registry. By default, RPMs are read through an internal Artifactory
pull-through cache; an existing local RPM snapshot can be served from a loopback
HTTP server instead. The workflow applies the catalog overlay, downloads and
checksum-verifies manifest resources, generates a clearly marked development
resource lock, and publishes the result to both an OCI archive and the local
registry. Catalog base images are built automatically before an application
image.

Prerequisites are Python 3.11+, Git, Curl, Podman, Buildah, Skopeo, `yq`, and
`jq`. Umoci is also required for malware and compliance scans. Podman and
Buildah must run rootless. The default connected path requires `ARTIFACTORY_URL`
and `ARTIFACTORY_READ_TOKEN`. For snapshot-based testing,
`LOCAL_RPM_REPO_DIR` must point to a complete RPM repository containing
`repodata/repomd.xml`. Signature checking remains enabled by default, so the
repository must also contain valid RPM and repository signatures trusted by
the source image.

```bash
export ARTIFACTORY_URL=https://artifactory.example.com
export ARTIFACTORY_READ_TOKEN=replace-with-read-token

make local-build IMAGE=ubi9-minimal
```

The default Artifactory remote-repository key is `ext-redhat-ubi-remote`. Override it
when the internal repository uses a different key. The default assumes that
remote repository points to `https://cdn-ubi.redhat.com`, so Artifactory retains
the upstream `content/public/ubi` path:

```bash
make local-build \
  IMAGE=ubi9-minimal \
  LOCAL_RPM_CACHE_REPOSITORY=ubi-rpm-remote
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

For the simplest connected development build, set the two Artifactory
credentials and run the target directly:

```bash
make local-build IMAGE=ubi9-minimal
```

The workflow selects UBI 9 or UBI 10 from the catalog dependency and requests
BaseOS and AppStream only from the authenticated internal cache. It downloads
and hashes both `repomd.xml` documents and records a composite metadata digest
in the local development lock. The generated repo file enables both channels
and is bind-mounted over `/etc/yum.repos.d/factory.repo` during the Buildah
build. The source Dockerfile's default-disabled repository configuration is
therefore overridden for local development only. Direct access to Red Hat's CDN
is not supported by `local_build.sh`; `LOCAL_USE_UPSTREAM_UBI_REPOS=true` now
fails with migration guidance. Because pull-through metadata can change, these
development locks remain ineligible for quarantine import or promotion.

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

## Unprivileged Kubernetes runners

All CI job templates are compatible with the GitLab Kubernetes executor with
`privileged = false`. The runner pods do not need host paths, container-engine
sockets, host devices, or added Linux capabilities. The factory runner executes
as UID 10001, uses subordinate IDs for rootless Buildah and Podman, uses VFS
container storage instead of `/dev/fuse`, and disables nested runtime cgroups
because the outer Kubernetes pod enforces resource limits.

Runner nodes must allow unprivileged user namespaces, and the job filesystem
must provide writable ephemeral space under `/tmp` and `/home/factory`. The
container runtime's seccomp profile must permit the user-namespace operations
used by rootless Buildah and Podman. The specialized runner tags in
`components/jobs.yml` select network, credential, FIPS-node, and protection
boundaries; they do not imply privileged execution. ClamAV and OpenSCAP inspect
an ownership-preserving Umoci unpack created and read inside Podman's rootless
user namespace rather than mounting container storage. FCS uses a job-local
rootless Podman API socket; no host socket is mounted.

## Required GitLab variables

| Variable | Scope | Purpose |
|---|---|---|
| `FACTORY_TOOLCHAIN_REGISTRY` | all CI jobs | Registry containing the signed factory runner images |
| `INTERNAL_GIT_BASE_URL` | build/intake | Internal GitLab group containing source mirrors |
| `ARTIFACTORY_URL` | all | Artifactory base URL |
| `ARTIFACTORY_REGISTRY` | build/sign/promote | Artifactory OCI registry hostname |
| `UPSTREAM_OCI_REPOSITORY` | intake/build | Artifactory OCI repository containing digest-pinned upstream bases |
| `ARTIFACTORY_OIDC_AUDIENCE` | protected jobs | Audience configured for the GitLab OIDC integration in Artifactory |
| `ARTIFACTORY_READ_TOKEN` | build/scan | Read-only token; masked and protected |
| `ARTIFACTORY_WRITE_TOKEN` | importer only | Quarantine write token |
| `ARTIFACTORY_RELEASE_TOKEN` | promotion only | Release repository write token |
| `ARTIFACTORY_SIGN_TOKEN` | sign only | Short-lived OIDC-exchanged token allowed to add referrers to quarantine images |
| `COSIGN_KEY_PATH` | sign only | Protected file variable containing the environment-specific encrypted Cosign private key |
| `COSIGN_PASSWORD` | sign | Protected, masked, environment-scoped private-key password |
| `COSIGN_PUBLIC_KEY` | promotion only | Protected file variable containing the trusted release-signing public key |
| `COSIGN_INTAKE_KEY_REF` | connected intake | Reference to the intake signing key |
| `COSIGN_INTAKE_PUBLIC_KEY` | build/intake | Public key used to verify signed snapshots and resource locks |
| `FACTORY_RELEASE_ENV` | sign/promote | `commercial`, `gov1`, or `gov2` |
| `RPM_SNAPSHOT_UBI9_ID` | build | Immutable UBI 9 snapshot identifier |
| `RPM_SNAPSHOT_UBI10_ID` | build | Immutable UBI 10 snapshot identifier |
| `AI_BASE_URL` | AI jobs | Internal OpenAI-compatible inference endpoint |
| `AI_MODEL` | AI jobs | Approved internal model identifier |
| `GITLAB_MIRROR_TOKEN` | connected intake | Token restricted to creating or updating source mirrors |
| `GITLAB_REMEDIATION_BROKER_TOKEN` | remediation broker | Token restricted to creating remediation branches and merge requests |
| `CISA_KEV_URL` | security-data intake | Approved CISA KEV source or internal proxy URL |
| `EPSS_URL` | security-data intake | Approved EPSS source or internal proxy URL |
| `COMPLIANCE_AS_CODE_DATASTREAM_DIR` | security-data intake | Directory containing reviewed SCAP datastreams |
| `FALCON_CLIENT_ID` | FCS scanner only | Falcon API client with Cloud Security Tools Download and container image scopes |
| `FALCON_CLIENT_SECRET` | FCS scanner only | Protected and masked Falcon API client secret |
| `FALCON_REGION` | FCS scanner only | `us-1`, `us-2`, `eu-1`, `us-gov-1`, or `us-gov-2` |
| `FCS_CLI_VERSION` | FCS runner | Pinned CLI/runner tag; defaults to `4.0.0` |
| `FACTORY_CHANGED_IMAGES` | root pipeline | Comma-separated list of catalog image names to build; omit to build all |
| `UPDATECLI_GITLAB_TOKEN` | updatecli schedule | Token with MR create permission used by `updatecli/updatecli.d/repo1-source-pins.yaml` to open source-pin update MRs |

Use GitLab OIDC ID tokens to obtain short-lived Artifactory tokens. Store the
encrypted Cosign private key as a protected, environment-scoped GitLab file
variable; never upload it to Artifactory or store an unencrypted key in the
repository.

## CrowdStrike FCS assessment

CrowdStrike FCS CLI 4.x is the authoritative image-security assessment. It runs
on the protected `factory-fcs-connected` Kubernetes runner against the exact
candidate loaded from `image.oci.tar` into rootless Podman. The CLI uses the
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
to the matching protected GitLab environment (`fcs/commercial`, `fcs/gov1`, or
`fcs/gov2`).

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

Configure these GitLab variables for each protected `signing/<environment>`
environment:

- Add the generated `.key` as a protected file variable named
  `COSIGN_KEY_PATH`.
- Add its password as the protected and masked `COSIGN_PASSWORD` variable.
- Add the `.pub` file as the protected file variable `COSIGN_PUBLIC_KEY` for
  verification and promotion jobs.
- Configure `ARTIFACTORY_OIDC_AUDIENCE` to match the Artifactory OIDC
  integration.
- Exchange the job's `ARTIFACTORY_ID_TOKEN` for a short-lived
  `ARTIFACTORY_SIGN_TOKEN` before `scripts/sign_and_attest.sh` runs.

The signing identity should be able to read candidate manifests and create
signature and attestation referrers in quarantine. It should not be able to
overwrite candidate manifests or write to release repositories. The promotion
identity separately verifies the signature, copies the subject and complete
referrer graph, checks that the digest did not change, and verifies the copied
signature.

Before enabling production promotion, confirm that Artifactory returns the
Cosign artifacts for a signed candidate:

```bash
oras discover "${ARTIFACTORY_REGISTRY}/${QUARANTINE_REPOSITORY}/${IMAGE_PATH}@${IMAGE_DIGEST}"
```

## Pipeline stage toggles

Each pipeline stage can be disabled by setting the corresponding
`FACTORY_ENABLE_*` variable to `false`, `0`, or `off`. All stages default to
enabled (`true`) except for `FCS`, `COMPLIANCE`, `REMEDIATE`, `IMPORT`,
`ATTEST`, and `PROMOTE`, which require protected runners or credentials and
default to `false`.

| Variable | Default | Stage controlled |
|---|---|---|
| `FACTORY_ENABLE_VALIDATE` | `true` | Schema and context validation |
| `FACTORY_ENABLE_PREPARE` | `true` | Resource-lock resolution and build context assembly |
| `FACTORY_ENABLE_BUILD` | `true` | Rootless Buildah OCI build |
| `FACTORY_ENABLE_SBOM` | `true` | Syft SBOM generation |
| `FACTORY_ENABLE_SCAN` | `true` | Grype/Trivy/OSV/ClamAV informational scans |
| `FACTORY_ENABLE_FCS` | `false` | CrowdStrike FCS authoritative assessment |
| `FACTORY_ENABLE_COMPLIANCE` | `false` | OpenSCAP compliance scan |
| `FACTORY_ENABLE_TEST` | `true` | Product integration tests |
| `FACTORY_ENABLE_GATE` | `true` | OPA policy gate |
| `FACTORY_ENABLE_REMEDIATE` | `false` | AI read-only remediation summary and patch-MR broker |
| `FACTORY_ENABLE_IMPORT` | `false` | Protected quarantine import |
| `FACTORY_ENABLE_ATTEST` | `false` | Cosign signing and attestation |
| `FACTORY_ENABLE_PROMOTE` | `false` | Pull-based release promotion |

## Source-pin management

Two tools keep catalog source revisions synchronized with upstream Repo One
development branches.

**vendir** (`vendir/config.yml`) declares the five Repo One Git sources and
their pinned commit references. Run `vendir sync` to update the checked-out
content under `vendor/repo1/`.

**updatecli** (`updatecli/updatecli.d/repo1-source-pins.yaml`) queries each
repository's current `development` branch tip with `git ls-remote` and opens a
GitLab merge request updating both the catalog `source.revision` fields and the
matching `vendir/config.yml` `ref` values. Run it on a schedule from a
connected runner:

```bash
updatecli apply --config updatecli/updatecli.d/repo1-source-pins.yaml
```

The `UPDATECLI_GITLAB_TOKEN` variable must be in scope when the pipeline runs.

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

The root pipeline renders a dependency-aware child pipeline. A UBI 9 change
selects UBI 9 plus the three Atlassian descendants. A UBI 10 change selects
only its canary pipeline until an Atlassian catalog entry explicitly changes
its base dependency.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the ten implementation
milestones and [docs/operations.md](docs/operations.md) for environment setup.
