# Operations guide

## Artifactory setup

Create these local repositories and enable immutability on release repositories:

| Repository | Type | Writer |
|---|---|---|
| `FACTORY_SOURCE_REPOSITORY` (default `generic-source-local`) | Generic | Connected intake only |
| `oci-upstream-cache-local` | OCI | Connected intake only |
| `FACTORY_QUARANTINE_REPOSITORY` or catalog override (`oci-base-quarantine-local`) | OCI | Protected importer |
| `FACTORY_QUARANTINE_REPOSITORY` or catalog override (`oci-app-quarantine-local`) | OCI | Protected importer |
| `oci-release-local` | OCI | Protected promotion broker |
| `oci-canary-local` | OCI | Protected canary broker |
| `rpm-ubi-remote` | RPM remote (`https://cdn-ubi.redhat.com`) | Local development read-through only |
| `rpm-ubi9-snapshot-local` | RPM | Repository snapshot job |
| `rpm-ubi10-snapshot-local` | RPM | Repository snapshot job |
| `security-data-local` | Generic/OCI | Security-data intake job |
| `oci-toolchain-local` | OCI | Toolchain bootstrap job |

Do not point production build containers at an Artifactory remote repository
whose RPM metadata changes in place. Use `scripts/snapshot_rpm_repo.sh`, set the
resulting immutable ID as `RPM_SNAPSHOT_ID`, and include the `repomd.xml`
SHA-256 in the resource lock. The build records the snapshot ID in provenance.
The local workflow may use the internal pull-through cache, but marks the lock
as development-only and prevents that build from entering import or promotion.

## Runner classes

| Tag | Network | Credential scope | Kubernetes placement |
|---|---|---|---|
| `factory-connected-intake` | Approved upstream + internal | Intake-only writes | Connected pool |
| `factory-offline` | Internal only | Artifact read | Offline pool |
| `factory-buildah-ephemeral` | Internal mirrors only | Artifact read | Rootless build pool |
| `factory-fips-ephemeral` | Internal only | Artifact read | FIPS-node pool |
| `factory-test-ephemeral` | Internal only | Test registry read | Rootless test pool |
| `factory-fcs-connected` | Falcon API + local candidate | Falcon image-assessment credentials only | Connected protected pool |
| `factory-ai-untrusted` | AI endpoint + evidence read | No Git/registry write | Untrusted network pool |
| `factory-remediation-broker` | Internal GitLab | Create branch/MR only | Protected broker pool |
| `factory-protected-importer` | Artifactory quarantine | Quarantine write | Protected importer pool |
| `factory-protected-signing` | Artifactory only | Write OCI signature/attestation referrers | Protected signing pool |
| `factory-protected-promotion` | Source/target Artifactory | Release copy | Protected promotion pool |

Configure every pool with the GitLab Kubernetes executor and
`privileged = false`. Do not add Linux capabilities or mount host paths,
container-engine sockets, or host devices. The runner image uses UID 10001,
rootless Buildah and Podman, VFS storage, and job-local ephemeral paths. Nodes
must allow unprivileged user namespaces, and `/tmp` and `/home/factory` must be
writable. The container runtime's seccomp profile must permit the
user-namespace operations used by rootless Buildah and Podman. FIPS jobs
additionally require scheduling on FIPS-enabled nodes. Rootless nested
containers disable their own cgroups; the outer pod remains subject to its
Kubernetes resource limits.

Buildah uses rootless isolation. ClamAV and OpenSCAP consume an
ownership-preserving Umoci unpack inside Podman's rootless user namespace
instead of `podman mount`. The FCS socket is created inside the job filesystem
by rootless Podman and is never shared with the node.

## Bootstrap sequence

1. Build the intake and runner toolchain images from verified source/binaries.
   For the FCS runner, use a bootstrap-only Falcon API client with Cloud
   Security Tools Download `READ` to download the pinned Linux CLI, verify the
   SHA-256 returned by the Falcon download API, and stage only the verified
   executable as `dist/fcs/fcs`.
2. Generate SBOMs and sign the toolchain images before using them in CI.
3. Create GitLab source mirror projects and protected runner tags.
4. Run `.gitlab/intake.yml` from a connected environment.
5. Transfer and verify resource locks and security-data bundles.
6. Run the normal root pipeline with `FACTORY_CHANGED_IMAGES=ubi9-minimal`.
7. Approve the UBI 9 quarantine import and signature.
8. Review the three resulting Atlassian pipelines and integration evidence.
9. Promote by digest after the policy and approval attestations are present.

Build `toolchain/Containerfile.factory-fcs-runner` from the already signed
factory runner image, passing its digest as `FACTORY_RUNNER_REF` and the pinned
`FCS_CLI_VERSION`. Do not download the CLI in an ordinary image pipeline job:
that would make each scan depend on a mutable tool download and would give the
scanner job permission to replace its own executable. The runtime Falcon API
client uses the documented FCS image-scan scopes and is stored as protected,
masked variables scoped to `fcs/<environment>`.

## Artifactory-compatible signing

Artifactory 7.90.1 or newer is required for OCI 1.1 Referrers API support.
Create a separate Cosign key pair for each release environment. Configure the
encrypted private key as the protected GitLab file variable `COSIGN_KEY_PATH`,
its password as the protected masked variable `COSIGN_PASSWORD`, and distribute
only the public key to promotion and verification jobs as `COSIGN_PUBLIC_KEY`.

The signing job exchanges its GitLab OIDC identity for the short-lived
`ARTIFACTORY_SIGN_TOKEN`. That identity may write only signature and
attestation referrers for subjects already present in quarantine; it must not
upload or overwrite candidate image manifests. Confirm referrer discovery with
`oras discover` before enabling promotion.

## U.S.-person approval

Configure Gov1/Gov2 as protected GitLab environments with approval rules bound
to the U.S.-person group. The scripts also reject a manual signing or promotion
job when `GITLAB_USER_EMAIL` does not end in `.us`; this is defense in depth,
not a replacement for protected-environment authorization.

## Helm charts

The `helm/` content in the three Repo One source repositories is excluded from
this image pipeline. Build a separate OCI Helm pipeline from Atlassian's
supported chart, with digest-pinned images, Gateway API overlays, Kyverno/OPA
validation, and independent signing and promotion.

## Intake pipeline additional variables

The `.gitlab/intake.yml` pipeline and its supporting scripts accept a number of
variables beyond the core set listed in the main README:

| Variable | Script | Purpose |
|---|---|---|
| `UBI9_SOURCE_REPO_FILE` | `snapshot_rpm_repo.sh` | Path to the `.repo` file describing the UBI 9 source repository to snapshot |
| `UBI9_SOURCE_REPO_ID` | `snapshot_rpm_repo.sh` | Repository ID within the `.repo` file used by `dnf reposync` |
| `UBI10_SOURCE_REPO_FILE` | `snapshot_rpm_repo.sh` | Path to the `.repo` file describing the UBI 10 source repository to snapshot |
| `UBI10_SOURCE_REPO_ID` | `snapshot_rpm_repo.sh` | Repository ID within the `.repo` file used by `dnf reposync` |
| `FACTORY_RPM_SNAPSHOT_UBI9_REPOSITORY` | `snapshot_rpm_repo.sh` | Artifactory repository for UBI 9 RPM snapshots; defaults to `rpm-ubi9-snapshot-local` |
| `FACTORY_RPM_SNAPSHOT_UBI10_REPOSITORY` | `snapshot_rpm_repo.sh` | Artifactory repository for UBI 10 RPM snapshots; defaults to `rpm-ubi10-snapshot-local` |
| `FACTORY_SOURCE_REPOSITORY` | `publish_intake.sh` / `snapshot_rpm_repo.sh` | Artifactory generic repository for resource locks, snapshot metadata, and OCI imports; defaults to `generic-source-local` |
| `AI_REPOSITORY_CANDIDATES` | `ai_remediation.py` | Path to the pre-built repository-candidates JSON file; defaults to `/opt/security-data/repository-candidates.json` |

## Build pipeline repo-config variables

`scripts/write_repo_config.sh` selects an RPM repository configuration for the
build. Two optional variables enable use cases outside the default Artifactory
snapshot path:

| Variable | Purpose |
|---|---|
| `FACTORY_UBI_REPO_PREFIX` | Internal UBI cache URL through the architecture segment. Local builds derive this from Artifactory; direct Red Hat CDN URLs are not supported. |
| `FACTORY_RPM_REPO_USERNAME` / `FACTORY_RPM_REPO_PASSWORD` | Read-only credentials embedded in the temporary cache repository file. |
| `FACTORY_RPM_BASE_URL` | Override the Artifactory snapshot URL with an arbitrary base URL (for example, the loopback HTTP server used with `LOCAL_RPM_REPO_DIR`). Incompatible with `FACTORY_UBI_REPO_PREFIX`. |
| `FACTORY_RPM_GPGCHECK` | RPM package signature check (`1` or `0`); defaults to `1`. |
| `FACTORY_RPM_REPO_GPGCHECK` | Repository metadata signature check (`1` or `0`); defaults to `1`. |
| `FACTORY_RPM_SSLVERIFY` | TLS verification for the RPM repository (`1` or `0`); defaults to `1`. |

## Automated source-pin maintenance

`updatecli/updatecli.d/repo1-source-pins.yaml` describes an updatecli pipeline
that queries each Repo One repository's `development` branch tip and opens a
GitLab merge request updating both the catalog `source.revision` fields and the
matching `vendir/config.yml` `ref` values. Run it on a scheduled GitLab
pipeline from a connected intake runner:

```bash
updatecli apply --config updatecli/updatecli.d/repo1-source-pins.yaml
```

The `UPDATECLI_GITLAB_TOKEN` GitLab variable must be present and have MR-create
permission. Review and merge the resulting MR before triggering the intake
pipeline so that the new revision is mirrored and locked before the build uses
it.

`vendir/config.yml` provides a complementary `vendir sync` workflow for
developers who want to inspect the upstream source content locally without
running the full intake pipeline.
