# Operations guide

## Artifactory setup

Infrastructure resource names are deployment settings, not repository defaults.
Create repositories for each setting and enable immutability on release
repositories:

| Setting | Type | Writer |
|---|---|---|
| `FACTORY_SOURCE_REPOSITORY` | Generic | Connected intake only |
| `UPSTREAM_OCI_REPOSITORY` | OCI | Connected intake only |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` | OCI | Protected importer |
| `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | OCI | Protected importer |
| `FACTORY_RELEASE_REPOSITORY` | OCI | Protected promotion broker |
| `FACTORY_CANARY_REPOSITORY` | OCI | Protected canary broker |
| `LOCAL_RPM_CACHE_REPOSITORY` | RPM remote | Local development read-through only |
| `FACTORY_RPM_SNAPSHOT_UBI9_REPOSITORY` | RPM | Repository snapshot job |
| `FACTORY_RPM_SNAPSHOT_UBI10_REPOSITORY` | RPM | Repository snapshot job |

Do not point production builds at a remote repository whose RPM metadata changes
in place. Run `scripts/snapshot_rpm_repo.sh`, set the resulting immutable ID as
the matching `RPM_SNAPSHOT_*_ID`, and include the `repomd.xml` SHA-256 in the
resource lock. Local development may use the configured pull-through cache, but
the resulting lock is marked development-only and cannot enter import or
promotion.

## Jenkins Kubernetes trust classes

Each Jenkins pod-template setting must resolve to a separately administered
Kubernetes trust class:

| Setting | Network | Credential scope | Placement |
|---|---|---|---|
| `FACTORY_K8S_INTAKE_POD_TEMPLATE` | Approved upstream + internal | Intake-only writes | Connected pool |
| `FACTORY_K8S_OFFLINE_POD_TEMPLATE` | Internal only | Artifact read | Offline pool |
| `FACTORY_K8S_BUILDAH_POD_TEMPLATE` | Internal mirrors only | Artifact read | Rootless build pool |
| `FACTORY_K8S_FIPS_POD_TEMPLATE` | Internal only | Artifact read | FIPS-node pool |
| `FACTORY_K8S_TEST_POD_TEMPLATE` | Internal only | Test registry read | Rootless test pool |
| `FACTORY_K8S_FCS_POD_TEMPLATE` | Falcon API + candidate | Falcon assessment only | Connected protected pool |
| `FACTORY_K8S_AI_POD_TEMPLATE` | AI endpoint + evidence read | No SCM/registry write | Untrusted pool |
| `FACTORY_K8S_REMEDIATION_POD_TEMPLATE` | Internal SCM | Branch push only | Protected broker pool |
| `FACTORY_K8S_IMPORT_POD_TEMPLATE` | Quarantine | Quarantine write | Protected importer pool |
| `FACTORY_K8S_SIGNING_POD_TEMPLATE` | Artifactory only | Referrer write | Protected signing pool |
| `FACTORY_K8S_PROMOTION_POD_TEMPLATE` | Source/target Artifactory | Release copy | Protected promotion pool |

Give every class its own Kubernetes ServiceAccount and least-privilege
NetworkPolicy. Pods must run unprivileged without added capabilities, host
paths, host devices, or container-engine sockets. Nodes must allow unprivileged
user namespaces; `/tmp` and `/home/factory` must be writable. The runtime
seccomp profile must permit rootless Buildah and Podman user-namespace
operations. FIPS jobs require FIPS-enabled nodes.

The factory image runs as UID 10001 with subordinate IDs and VFS storage.
ClamAV and OpenSCAP consume an ownership-preserving Umoci unpack inside
Podman's user namespace. FCS creates a rootless, job-local Podman socket.

Configure an object-storage-backed Jenkins Artifact Manager. The pipeline uses
stashes to transfer OCI archives between ephemeral pods; controller-local stash
storage is not suitable for these files.

## Jenkins authorization boundary

Use separate Jenkins jobs for untrusted change-request validation and protected
release execution:

1. The change-request job loads the proposed Jenkinsfile but cannot resolve any
   importer, signing, promotion, mirror, or remediation-broker credential.
2. The release job loads its pipeline definition from the protected default
   branch and runs only trusted revisions.
3. Folder-level RBAC restricts the Gov approval `input` step to the group named
   by `FACTORY_GOV_APPROVERS`.
4. `FACTORY_GOV_APPROVER_PATTERN` independently validates the authenticated
   submitter ID before Gov signing or promotion.
5. Lockable Resources prefixes from `FACTORY_IMPORT_LOCK_PREFIX` and
   `FACTORY_PROMOTION_LOCK_PREFIX` serialize writes for the same image.

Do not rely on branch checks inside a Jenkinsfile supplied by an untrusted
change request. Credential visibility, trusted pipeline loading, SCM branch
protection, and Kubernetes admission policy are primary controls.

Use Kubernetes workload identity and the Artifactory identity provider to issue
short-lived stage credentials. Scope each Jenkins credential ID setting to the
job and pod class that owns it. Do not replace short-lived tokens with
controller-wide static secrets.

## Bootstrap sequence

1. Build the intake and factory runner images from verified source and binaries.
2. Generate SBOMs and sign the runner images; configure their digest-qualified
   references as Jenkins settings.
3. Create internal source mirrors and the Artifactory repositories selected by
   the resource-name settings.
4. Create the Kubernetes ServiceAccounts, namespaces, NetworkPolicies, and
   Jenkins pod templates for every trust class.
5. Configure Jenkins RBAC, credential providers, Lockable Resources, and the
   external Artifact Manager.
6. Register `Jenkinsfile.intake` as a connected scheduled job and run intake.
7. Verify resource-lock and security-data signatures.
8. Register `Jenkinsfile` as a multibranch validation job and as a separately
   protected release job.
9. Build the UBI 9 lineage, stopping after quarantine until evidence is
   independently reviewed.
10. Approve signing and digest-preserving promotion.

Build `toolchain/Containerfile.factory-fcs-runner` from the signed factory
runner image by passing its digest as `FACTORY_RUNNER_REF` and supplying the
pinned `FCS_CLI_VERSION`. Download and checksum-verify the entitlement-protected
CLI only in the bootstrap process. The runtime FCS client belongs only to the
FCS pod template.

## Artifactory-compatible signing

Artifactory 7.90.1 or newer is required for OCI 1.1 Referrers API support.
Create a separate encrypted Cosign key pair for each release environment.
Bind the private key and password only through `COSIGN_KEY_CREDENTIAL_ID` and
`COSIGN_PASSWORD_CREDENTIAL_ID`. Bind only the public key selected by
`COSIGN_PUBLIC_KEY_CREDENTIAL_ID` in promotion jobs.

The signing workload identity obtains the short-lived credential selected by
`ARTIFACTORY_SIGN_CREDENTIAL_ID`. It may create signature and attestation
attachment tags for existing quarantine subjects but cannot replace candidate
manifests or write release repositories. Confirm both referrer discovery and Cosign verification after copying before enabling promotion.

## Intake settings

`Jenkinsfile.intake` requires:

| Setting | Purpose |
|---|---|
| `FACTORY_RPM_SNAPSHOT_UBI9_REPOSITORY` / `FACTORY_RPM_SNAPSHOT_UBI10_REPOSITORY` | Snapshot destinations |
| `FACTORY_SOURCE_REPOSITORY` | Locks, snapshot metadata, and locked resources |
| `INTERNAL_GIT_BASE_URL` | Internal source-mirror namespace |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Generic API and OCI endpoints |
| `UPSTREAM_OCI_REPOSITORY` | Digest-pinned upstream OCI destination |

The following settings are **optional**; bundled defaults are used when absent:

| Setting | Default | Purpose |
|---|---|---|
| `UBI9_SOURCE_REPO_FILE` | `config/rpm/ubi9.repo` | UBI 9 source `.repo` file |
| `UBI9_SOURCE_REPO_IDS` | `ubi-9-baseos-rpms ubi-9-appstream-rpms` | Space-separated UBI 9 repository IDs |
| `UBI10_SOURCE_REPO_FILE` | `config/rpm/ubi10.repo` | UBI 10 source `.repo` file |
| `UBI10_SOURCE_REPO_IDS` | `ubi-10-for-x86_64-baseos-rpms ubi-10-for-x86_64-appstream-rpms` | Space-separated UBI 10 repository IDs |

The bundled `.repo` files point to the public `cdn-ubi.redhat.com` endpoints and
require no Red Hat subscription credentials.  Override these settings to use a
deployment-specific mirror or an authenticated proxy.

The intake job also uses credential IDs documented in the main README. Configure
its schedule in Jenkins job configuration; no environment-specific cron or job
resource name is embedded in the repository.

### RPM snapshot size

`scripts/snapshot_rpm_repo.sh` reports the downloaded snapshot's logical byte
size and file count to stderr and records both values in `snapshot.json`
(`snapshotBytes`, `snapshotFiles`).  Actual size varies as Red Hat updates UBI
content.  Estimate current storage requirements before initial deployment:

```bash
dnf reposync \
  --config config/rpm/ubi9.repo \
  --repoid ubi-9-baseos-rpms \
  --repoid ubi-9-appstream-rpms \
  --download-metadata \
  --download-path /tmp/ubi9-measure \
  --arch x86_64 --arch noarch --newest-only --delete
du -sh /tmp/ubi9-measure
```

Retaining multiple snapshots multiplies storage usage.  If Artifactory
checksum-deduplication is enabled, unchanged RPMs share physical storage, but
each snapshot path still counts towards logical repository size.

## Build repository configuration

`scripts/write_repo_config.sh` selects an RPM repository configuration:

| Variable | Purpose |
|---|---|
| `FACTORY_RPM_UPSTREAM_UBI_BASE` | Development-only: direct public CDN base URL (no credentials) |
| `FACTORY_UBI_REPO_PREFIX` | Internal UBI cache URL through the architecture segment |
| `FACTORY_RPM_REPO_USERNAME` / `FACTORY_RPM_REPO_PASSWORD` | Temporary read-only repository credentials |
| `FACTORY_RPM_BASE_URL` | Explicit immutable snapshot URL, including loopback local development |
| `FACTORY_RPM_GPGCHECK` | RPM package signature check |
| `FACTORY_RPM_REPO_GPGCHECK` | Repository metadata signature check |
| `FACTORY_RPM_SSLVERIFY` | Repository TLS verification |

## Automated source-pin maintenance

Set `FACTORY_UPSTREAM_BRANCH` and run `scripts/update_source_pins.sh` from a
connected Jenkins job. The script resolves each catalog's `source.upstream`,
updates the catalog revision and matching `vendir/config.yml` reference, and
leaves the resulting change for normal review. Publish that diff through an
SCM-specific protected branch workflow; the source-pin process must never
auto-merge.

## Day 2 operating cadence

| Frequency/event | Action | Evidence to retain |
|---|---|---|
| Each intake | Check approved origins, checksums, root RPM metadata and snapshot signatures | Source revision, snapshot ID, signed resource lock |
| Each release | Review FCS tenant policy, compliance and product results for the candidate digest | Gate input/result, assessment reports, signed predicates |
| Security-data refresh | Rebuild and sign scanner data/tool images; check actual versions and data age | Data inventory and runner digest |
| Source update | Refresh pins on a branch, reconcile versions and recheck patches | Reviewed diff and clean build results |
| Key rotation | Distribute new public trust, test verification, then switch signer | Old/new key IDs and verification record |
| Periodic restore drill | Recover an archived candidate and verify every required predicate | Restore and destination verification logs |

### Snapshot and resource lock contract

Resource locks are stored under
`locks/<image>/<source-revision>/<rpm-snapshot-id>/resource-lock.json` and a
sibling `resource-lock.sig`. Re-run intake when migrating from the earlier
revision-only path. Never rewrite an existing signed lock to select a new RPM
snapshot. Deny overwrite/delete for normal intake credentials and apply retention
only after verifying that no retained release depends on the content.

The snapshot job re-indexes combined RPM channels with `createrepo_c`. It signs
`snapshot.json`, not `repomd.xml.asc`. Production builds authenticate the root
`repodata/repomd.xml` hash against that signed lock; `repo_gpgcheck=0` is therefore
intentional for factory snapshots. RPM package signature checking remains on.
The Artifactory snapshot must remain immutable between the metadata check and
package resolution. Provide the vendor RPM trust keys in the base image.

### Failure runbooks

| Symptom | Check and recovery |
|---|---|
| Base unavailable with `--pull=never` | Verify internal digest and read credentials; the build stage must preload it into the fresh pod |
| Intake upload denied | Check intake token and repository path; never use a release token as a shortcut |
| Snapshot hash mismatch | Stop and investigate replacement/corruption; issue a new snapshot and lock after review |
| OpenSCAP has no evaluated rules | Check ARF output, profile applicability and tailoring; empty evidence must not pass |
| FCS unavailable or denies | Check matching region/client and tenant policy; retain report, rebuild or resolve the finding |
| Gate denied | Inspect `evidence/gate-result.json`; informational scanner thresholds do not change FCS policy |
| Signature absent after copy | Check Cosign attachment tags, ORAS referrers and destination permissions; do not promote without evidence |
| Canary reaches wrong repository | Verify `FACTORY_CANARY_REPOSITORY`; destination comes from the catalog, not a global release override |

### Rollback

Select a previously approved image by digest and verify its signature and required
attestations using the retained environment public key. Re-evaluate its security
eligibility with current policy/data before redeployment. Change the downstream
GitOps desired digest through its approval workflow; this repository does not
operate Flux or roll back application databases. Coordinate database compatibility
and restore requirements with the product owner. Do not rebuild an old source
and describe the new digest as the original release.

### Activation gates still owned by the environment

Run a complete UBI 9 build/import/sign/copy in a disposable repository, including
denied/malformed evidence cases. Confirm archived artifacts survive failed jobs,
that destination signatures and all required predicates verify, and that canary
writes cannot reach release storage. Complete licensed database/search/clustering
and graceful-shutdown tests before treating the product baseline profiles as
production qualification. FCS API egress is required even when build inputs are
air-gapped; no offline FCS substitute is implemented here.

Release tags are `<product-version>-<full-sha256>` so a rebuilt product version can coexist with the previous immutable release. Consumers should use the verified digest.
