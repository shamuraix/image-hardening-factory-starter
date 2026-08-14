# Operations guide

## Artifactory setup

Create these local repositories and enable immutability on release repositories:

| Repository | Type | Writer |
|---|---|---|
| `generic-source-local` | Generic | Connected intake only |
| `oci-upstream-cache-local` | OCI | Connected intake only |
| `oci-base-quarantine-local` | OCI | Protected importer |
| `oci-app-quarantine-local` | OCI | Protected importer |
| `oci-release-local` | OCI | Protected promotion broker |
| `oci-canary-local` | OCI | Protected canary broker |
| `rpm-ubi9-snapshot-local` | RPM | Repository snapshot job |
| `rpm-ubi10-snapshot-local` | RPM | Repository snapshot job |
| `security-data-local` | Generic/OCI | Security-data intake job |
| `oci-toolchain-local` | OCI | Toolchain bootstrap job |

Do not point build containers at an Artifactory remote repository whose RPM
metadata changes in place. Use `scripts/snapshot_rpm_repo.sh`, set the resulting
immutable ID as `RPM_SNAPSHOT_ID`, and include the `repomd.xml` SHA-256 in the
resource lock. The build records the snapshot ID in provenance.

## Runner classes

| Tag | Network | Credential scope | Lifecycle |
|---|---|---|---|
| `factory-connected-intake` | Approved upstream + internal | Intake-only writes | Ephemeral VM |
| `factory-offline` | Internal only | Artifact read | Ephemeral container/VM |
| `factory-buildah-ephemeral` | Internal mirrors only | Artifact read | Destroy after job |
| `factory-fips-ephemeral` | Internal only | Artifact read | FIPS host, destroy after job |
| `factory-test-ephemeral` | Internal only | Test registry read | Destroy after job |
| `factory-fcs-connected` | Falcon API + local candidate | Falcon image-assessment credentials only | Ephemeral, protected |
| `factory-ai-untrusted` | AI endpoint + evidence read | No Git/registry write | Destroy after job |
| `factory-remediation-broker` | Internal GitLab | Create branch/MR only | Protected |
| `factory-protected-importer` | Artifactory quarantine | Quarantine write | Protected |
| `factory-protected-signing` | Artifactory only | Write OCI signature/attestation referrers | Protected |
| `factory-protected-promotion` | Source/target Artifactory | Release copy | Protected |

Use rootful Buildah with chroot isolation on a single-use VM. Do not mount a
Docker socket and do not run privileged build pods on reusable Kubernetes
runners.

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
