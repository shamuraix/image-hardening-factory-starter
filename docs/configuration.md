# Configuration reference

[Project overview](../README.md) · [Operations](operations.md) · [Agents](agents.md) · [Architecture](architecture.md)

Terms: **PaC** is [Pipelines-as-Code](https://pipelinesascode.com/docs/), which
starts Tekton pipelines from Git events. A **Repository CR** is the Kubernetes
object that connects a Git repository to PaC.

## Quick setup checklist

1. **Cluster prerequisites** (versions in `tools/versions.lock.yaml`)
   - Kubernetes 1.33 or later. The build and test stages run in a pod
     [user namespace](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/)
     (`hostUsers: false`) with an unmasked `/proc` on the run step
     (`procMount: Unmasked`); the `UserNamespacesSupport` and `ProcMountType`
     feature gates are on by default from 1.33
     ([feature gates](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/)).
     On 1.30–1.32 enable both gates by hand. The ValidatingAdmissionPolicy in
     `deploy/base/admission-policy.yaml` needs 1.30 or later. Build nodes need a
     runtime that supports user namespaces (containerd 2.0+ or CRI-O 1.25+) and
     kubelet `userNamespaces.idsPerPod` of at least 262144, so the runner's
     `100000:65536` subordinate range fits.
   - [Tekton Pipelines](https://tekton.dev/docs/pipelines/) v1.15,
     Pipelines-as-Code v0.51, [Tekton Chains](https://github.com/tektoncd/chains/blob/v0.28.0/docs/config.md) v0.28
   - A default StorageClass for the per-run `volumeClaimTemplate` workspaces
   - Nodes for the compliance stage labelled `factory.dev/fips-node=true` and
     tainted `factory.dev/fips-node=true:NoSchedule`
2. **Build, sign, and publish the three runner images** and record their
   digests in the Repository CR params (see [Runner images](#runner-images)):
   - `runner_image` — `toolchain/Containerfile.factory-runner`
   - `intake_runner_image` — `toolchain/Containerfile.factory-intake-runner`
   - `agent_image` — `toolchain/Containerfile.factory-agent`
3. **Apply the factory objects**: edit `deploy/base/settings.yaml`,
   `deploy/base/repository.yaml`, and the CIDRs in `deploy/base/network-policies.yaml`,
   then run `kubectl apply -k deploy/base`.
4. **Create the Secrets** listed in `deploy/secrets.example.yaml` with your
   secret manager.
5. **Configure Chains** with `deploy/chains/chains-config.yaml` and a signing key
   (KMS recommended).
6. **Connect GitLab.** Create a project (or group) access token with the `api`
   scope and a webhook secret, store them in Secret `factory-gitlab-webhook`,
   and add a project webhook to the PaC controller URL for **Merge request,
   Push, Comments, and Tag push** events — `tkn pac create repo` does this, or
   do it under Settings > Webhooks
   ([PaC GitLab setup](https://pipelinesascode.com/docs/install/gitlab/)).
   Protect `main`: merge requests only, required approvals, and "require
   approval from code owners" so the `CODEOWNERS` file governs `releases/` and
   the trust-boundary paths (a GitLab Premium feature; without it, use a
   protected-branch rule that limits who may merge). Merge requests from
   members of the project, including inherited group membership, start runs
   automatically; anyone else needs `/ok-to-test` from a member. Make the
   factory bot a project member so agent-proposed merge requests are validated.
   PaC's `settings.policy` block is not used: it applies to GitHub and Forgejo
   only ([PaC docs](https://pipelinesascode.com/docs/advanced/policy-authorization/)).
   Terms: GitLab calls them merge requests; PaC and the generated PipelineRuns
   use the event name `pull_request` for both GitLab and GitHub.
7. Confirm contributors can run `make ci` locally.

## Pipelines-as-Code Repository (`deploy/base/repository.yaml`)

| Field | Value | Why |
|---|---|---|
| `url` | the GitLab project URL | which project's webhooks this namespace serves |
| `git_provider.type` / `url` / `secret` / `webhook_secret` | `gitlab`, the instance URL, Secret `factory-gitlab-webhook` keys `provider.token` and `webhook.secret` | API access and webhook validation for a self-hosted instance ([PaC GitLab setup](https://pipelinesascode.com/docs/install/gitlab/)) |
| `settings.pipelinerun_provenance` | `default_branch` | merge-request runs use PipelineRun definitions from `main`, never from the merge request ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/)) |
| `concurrency_limit` | `3` | at most three PipelineRuns at once; each holds a volume and up to 8 CPUs ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/concurrency/)) |
| `params` | `runner_image`, `intake_runner_image`, `agent_image`, `git_provider`, `enable_agents` | digest-pinned images, the provider, and the cluster-wide agent switch, filled into the generated PipelineRuns |
| `incoming` | `webhook-url`, Secret `factory-pac-incoming`, target `main` | lets CronJobs and the release pipeline start runs ([PaC docs](https://pipelinesascode.com/docs/advanced/incoming-webhooks/)) |

## Settings (`deploy/base/settings.yaml`, ConfigMap `factory-settings`)

Every factory step receives these as environment variables (`envFrom`).

| Key | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` | internal SCM namespace that holds the source mirrors |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Artifactory API base URL and OCI registry host |
| `ARTIFACTORY_USERNAME` | registry login name paired with the `factory-artifactory-*` tokens (the user the tokens were issued to, for example the bot account); REST calls send the token as a bearer and need no name |
| `FACTORY_SOURCE_REPOSITORY` | generic repository for locks, intake files, release pointers, and the security-data bundle |
| `UPSTREAM_OCI_REPOSITORY` | digest-pinned upstream base images |
| `FACTORY_{BASE,APPLICATION}_QUARANTINE_REPOSITORY` | protected candidate repositories; the catalog `publication.quarantineRepository` field expands these names |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | release and canary repositories; expanded from the catalog `publication.releaseRepository` field |
| `FACTORY_CATALOG_DIR` | catalog directory (`catalog/images`) |
| `FACTORY_UBI_MIRROR_URL` | internal mirror of the public UBI RPM content (`https://cdn-ubi.redhat.com/content/public/ubi/dist`, same layout). Builds read `ubi<major>/<major>/<arch>/{baseos,appstream}/os` under it with Secret `factory-rpm-mirror` |
| `FACTORY_DEFAULT_BRANCH` | branch that scheduled runs and agent change requests target |
| `FACTORY_GOV_APPROVER_PATTERN` | regular expression the merging approver must match for gov1/gov2 |
| `FACTORY_POINTER_ENVIRONMENT` | environment whose base releases update the release pointer (`commercial`) |
| `CISA_KEV_URL` | CISA KEV (Known Exploited Vulnerabilities) JSON feed, linked from the [KEV catalog page](https://www.cisa.gov/known-exploited-vulnerabilities-catalog); used when building the security-data bundle |
| `COMPLIANCE_AS_CODE_DATASTREAM_DIR` | directory in the intake runner image that holds the [ComplianceAsCode](https://github.com/ComplianceAsCode/content) SCAP datastreams `ssg-rhel9-ds.xml` and `ssg-rhel10-ds.xml` |
| `FACTORY_UPSTREAM_BRANCH` | Repo One branch followed by intake and upstream-sync |
| `FACTORY_PAC_CONTROLLER_URL` / `FACTORY_PAC_REPOSITORY` | PaC incoming endpoint and Repository CR name |
| `FACTORY_GITLAB_API_URL` | GitLab API for agent comments and merge requests; defaults to `https://<git host>/api/v4` (`FACTORY_GITHUB_API_URL` applies only when `git_provider` is `github`) |
| `SCM_BOT_AUTHOR_NAME` / `SCM_BOT_AUTHOR_EMAIL` | commit identity for agent proposals and release requests |
| `ANTHROPIC_BASE_URL` | Anthropic-format [LLM gateway](https://code.claude.com/docs/en/llm-gateway) for Claude Code |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` / `_OPUS_MODEL` | optional gateway model names for the `sonnet`/`opus` aliases ([model configuration](https://code.claude.com/docs/en/model-config)) |
| `FACTORY_AGENT_TIMEOUT` | wall-clock limit per agent run |

## Artifactory layout

The scripts address Artifactory in exactly two ways, so any set of repositories
works as long as these two hold:

- **OCI**: `${ARTIFACTORY_REGISTRY}/${repository}/${path}:tag` — the
  repository-path access method, where `${repository}` may itself contain a
  path (`<repository key>/<prefix>`). The subdomain method
  (`<repository key>.<host>`) is not supported.
- **Files**: `${ARTIFACTORY_URL}/artifactory/${FACTORY_SOURCE_REPOSITORY}/<file>`
  — a generic repository, again with an optional prefix.

The factory's logical repositories are therefore path prefixes, and the trust
boundaries between them are enforced with Artifactory permission targets
(include patterns on the prefix) and one access token per target. The
`deploy/overlays/dev` overlay is a worked example on one docker and one
generic repository (`artifactory.cicd.dc`); the base manifests use neutral
placeholder names.

| Factory setting | Dev value (prefix) | Holds | Token (Secret) and scope |
|---|---|---|---|
| runner images (Repository CR params) | `…docker-dev-local/factory/` | the three runner images, by digest | pushed by an operator; read by the kubelet |
| `UPSTREAM_OCI_REPOSITORY` | `…docker-dev-local/upstream` | Iron Bank bases imported by intake | `factory-artifactory-intake` write; `-read` read |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` | `…docker-dev-local/quarantine/bases` | gated base candidates and their evidence referrers | `factory-artifactory-quarantine` write |
| `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | `…docker-dev-local/quarantine/apps` | gated application candidates | same |
| `FACTORY_RELEASE_REPOSITORY` | `…docker-dev-local/release` | promoted images, cosign signatures and attestations | `factory-artifactory-release` write; `-sign` for signatures |
| `FACTORY_CANARY_REPOSITORY` | `…docker-dev-local/canary` | UBI 10 canary | release |
| `FACTORY_SOURCE_REPOSITORY` | `…generic-dev-local/factory` | `locks/` (signed resource locks), intake resources, `releases/<image>/current.json` (pointers), `security-data/` (signed scanner bundle) | `-intake` writes locks, resources, and the bundle; `-pointer` writes pointers; `-read` reads |
| `FACTORY_UBI_MIRROR_URL` | `https://<host>/artifactory/ext-redhat-ubi-remote/content/public/ubi/dist` | remote repository in front of the UBI CDN, same layout | `factory-rpm-mirror` (bot account + token) read |

Permission targets for the dev overlay (repository, include pattern, who):

| Target | Repository | Include pattern | Permission | Used by |
|---|---|---|---|---|
| factory-read | docker, generic, ext-redhat-ubi-remote | `**` | read | `factory-artifactory-read`, `factory-rpm-mirror` |
| factory-intake | docker | `upstream/**` | read, deploy | `factory-artifactory-intake` |
| factory-intake | generic | `factory/locks/**`, `factory/resources/**`, `factory/security-data/**` | read, deploy | `factory-artifactory-intake` |
| factory-quarantine | docker | `quarantine/**` | read, deploy | `factory-artifactory-quarantine` |
| factory-sign | docker | `quarantine/**`, `release/**` | read, deploy (tags `sha256-*.sig`, `.att`) | `factory-artifactory-sign` |
| factory-release | docker | `release/**`, `canary/**` | read, deploy | `factory-artifactory-release` |
| factory-pointer | generic | `factory/releases/**` | read, deploy | `factory-artifactory-pointer` |

No token can remove artifacts, and no token's include pattern crosses from
quarantine to release except the signature token, which writes only signature
and attestation tags next to an existing digest.

What the given repositories cannot provide:

- **RPM content** does not come from the local rpm repository: a *local*
  repository serves only what is uploaded to it, and the build expects the UBI
  CDN layout (`ubi9/9/x86_64/baseos/os/…`) under `FACTORY_UBI_MIRROR_URL`. The
  dev instance already has a *remote* repository, `ext-redhat-ubi-remote`, in
  front of `https://cdn-ubi.redhat.com`, so the overlay points at
  `…/artifactory/ext-redhat-ubi-remote/content/public/ubi/dist` and the
  catalog's `build.rpm.source: private-mirror` applies. `factory-rpm-mirror`
  holds the bot account and its token (Artifactory accepts a token as the
  basic-auth password). Without such a remote repository, set
  `FACTORY_RPM_SOURCE_MODE: public-upstream` to install from the CDN directly;
  `image-metadata.json` records `rpmSource` either way.
- **Environment separation.** One `dev` repository per type is fine for
  development. The `commercial`/`gov1`/`gov2` release environments and the
  quarantine-to-release boundary are designed as separate repositories with
  separate write scopes; use prefixes only where a single misconfigured
  permission target is an acceptable blast radius.
- **OCI referrers.** `publish_evidence_bundle.sh` attaches evidence with
  `oras attach`. Artifactory versions without the OCI 1.1 referrers API are
  handled by oras's fallback to the referrers tag schema; signatures and
  attestations are ordinary tags.
- **Helm and PyPI repositories** are not used: `deploy/` is Kustomize, and
  `make toolchain` resolves Python dependencies on a connected host and bakes
  them into the runner image (a PyPI repository is only a `pip --index-url`
  for that host).

## Secrets

Secret names and keys are fixed by the Tasks (see `deploy/secrets.example.yaml`).
Each is mounted only into the step that needs it.

| Secret | Keys | Pipeline task |
|---|---|---|
| `factory-artifactory-read` | `token` | prepare, build, resolve, evidence-context |
| `factory-rpm-mirror` | `username`, `password` | build (read-only RPM mirror credential) |
| `factory-intake-cosign-public-key` | `cosign.pub` | prepare (resource lock and security-data bundle) |
| `factory-artifactory-quarantine` | `token` | quarantine |
| `factory-artifactory-sign` | `token` | attest |
| `factory-artifactory-release` | `token` | promote |
| `factory-artifactory-pointer` | `token` | promote (pointer step) |
| `factory-cosign-{commercial,gov1,gov2}` | `cosign.key`, `password`, `cosign.pub` | attest, promote |
| `factory-artifactory-intake` | `token` | intake; security-data (publish step) |
| `factory-intake-cosign` | `cosign.key`, `password`, `cosign.pub` | intake; security-data (build step) |
| `factory-scm-mirror` | `username`, `token` | intake |
| `factory-scm-bot` | `username`, `token` | agent publish step (default branch only) |
| `factory-ai-gateway` | `token` | agent step |
| `factory-pac-incoming` | `secret` | Repository incoming webhook, schedules, dependents |
| `factory-gitlab-webhook` | `provider.token`, `webhook.secret` | Pipelines-as-Code GitLab provider (read by the PaC controller, not by any Task) |

Create one cosign key pair per release environment
([cosign key generation](https://docs.sigstore.dev/cosign/key_management/signing_with_self-managed_keys/)):

```bash
cosign generate-key-pair --output-key-prefix cosign-commercial
```

## Pipeline parameters

Each generated PipelineRun sets these `factory-image-build` parameters. They are
rendered by `factory/tekton.py`; change the renderer and run `make tekton-render`
rather than editing the generated files.

| Param | PR | push | schedule | base-release |
|---|---|---|---|---|
| `publish` | false | true | false | true |
| `enable-agents` (triage, remediation, readiness) | `{{ enable_agents }}` from the Repository CR (default `"true"`) | same | same | same |
| `enable-remediation` | false | true | true | true |
| `enable-release-request` | false | true | false | true |

## Runner images

All three images are built from `toolchain/`, offline, from a `dist/`
directory that `make toolchain` assembles on a connected host:

1. `toolchain/download-tools.py` fetches every pinned binary from GitHub
   releases (cosign, oras, BuildKit, RootlessKit, yq, OPA, Syft, Grype, Trivy,
   OSV-Scanner, umoci), the ClamAV package, the shadow source archive (see
   [User-namespace helpers](#user-namespace-helpers)), and the ComplianceAsCode
   SCAP datastreams, checking each against the SHA-256 digest GitHub records
   for the asset, and refusing any asset without one. With `--with-claude` it also
   fetches the Claude Code binary and checks it against the release manifest.
   Versions come only from `tools/versions.lock.yaml`.
2. `pip wheel` packages this repository and its dependencies for Python 3.12 on
   the runner.

Then:

| Image | Containerfile | Base | Adds |
|---|---|---|---|
| `runner_image` | `Containerfile.factory-runner` | `BASE_REF`: the internal hardened UBI 9 minimal (the harness uses `registry.access.redhat.com/ubi9/ubi-minimal`) | UBI packages (Python 3.12, rootless Podman, Skopeo, OpenSCAP, git, jq, curl), the pinned tools, ClamAV, the factory package, the `factory` user with subordinate IDs, `newuidmap`/`newgidmap` compiled with libcap in a throwaway build stage (needs `gcc`, `make`, `libcap-devel`, `glibc-devel`, `libxcrypt-devel`, `xz` from the UBI AppStream/BaseOS mirror) |
| `intake_runner_image` | `Containerfile.factory-intake-runner` | the runner image | SCAP datastreams at `COMPLIANCE_AS_CODE_DATASTREAM_DIR`, `freshclam.conf` |
| `agent_image` | `Containerfile.factory-agent` | the runner image | the pinned Claude Code binary |

Because every tool is in the runner, no stage downloads anything at run time,
and the harness builds the identical runner image for local use.

`FACTORY_CA_BUNDLE` (optional, for `make toolchain` and as a setting): a PEM
bundle of extra CA certificates. The runner image trusts it, and
`scripts/build_image.sh` mounts the runner's merged trust store into every RUN
step of the image being built at `/run/factory-ca-bundle.crt` (a BuildKit
secret, not a layer) and points the mounted repository configuration at it
(`sslcacert`, `scripts/write_repo_config.sh`), so `dnf`/`microdnf` verify the
proxy or mirror against it. The image's own trust store is left alone: Iron
Bank Dockerfiles run `update-ca-trust`, which must be able to rewrite
`/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem`, and the extra root never
ends up in the built image. Other TLS clients inside RUN steps are not covered,
which matches Iron Bank's rule that Dockerfiles fetch nothing at build time;
declared resources arrive through the hardening manifest instead. Needed when
builds reach the network through a TLS-inspecting proxy or a mirror with an
internal CA; unnecessary when the internal hardened base already trusts them.

## BuildKit and Podman

The build and test run steps start a short-lived rootless `buildkitd` or
rootless Podman inside the step container. There is no shared daemon, host
socket, or privileged sidecar. These two steps need
`allowPrivilegeEscalation: true`, Unconfined seccomp/AppArmor, and
`capabilities: {drop: [ALL], add: [SETUID, SETGID]}` because
`newuidmap`/`newgidmap` are setuid programs (next section). Every other step
uses `RuntimeDefault` seccomp with all capabilities dropped. For that reason the
namespace's [Pod Security](https://kubernetes.io/docs/concepts/security/pod-security-admission/)
level is `privileged`, with `restricted` warnings and audit.

### User-namespace helpers

Rootless BuildKit and Podman map the `factory` user's subordinate range
(`100000:65536`) into a child user namespace by running `newuidmap` and
`newgidmap`, which write `/proc/<pid>/uid_map` and `gid_map`. Three kernel
rules decide whether that works inside a pod:

- Inside a pod user namespace (`hostUsers: false`) the kernel ignores file
  capabilities written outside that namespace, so the helpers must be setuid
  root; the runner image sets that bit.
- A setuid program receives only the capabilities in the caller's *bounding
  set* ([capabilities(7)](https://man7.org/linux/man-pages/man7/capabilities.7.html),
  "Capabilities and execution of programs by root"). With `drop: [ALL]` alone
  the helper is root with no capabilities and fails with
  `open of uid_map failed: Permission denied`. The run steps therefore keep
  `CAP_SETUID` and `CAP_SETGID` in the bounding set. The step process itself
  runs as uid 10001 with no effective capabilities; they only materialise
  inside the two helpers.
- Writing a map needs `CAP_SETUID`/`CAP_SETGID` in the parent namespace, and
  the opener must be the namespace owner or hold `CAP_SYS_ADMIN` over it
  (`kernel/user_namespace.c`, `map_write`). The UBI `shadow-utils` helpers are
  built without libcap, stay uid 0, and so would need `CAP_SYS_ADMIN` and
  `CAP_DAC_OVERRIDE` as well. The runner instead compiles the helpers from the
  pinned [shadow](https://github.com/shadow-maint/shadow) release with
  `libcap-devel` present: that build drops back to the caller's uid before
  opening the map and keeps only the one capability it needs
  (`lib/idmapping.c`, `write_mapping`). The image build fails if the installed
  helpers are not that build, and `scripts/runtime_preflight.sh` checks both
  the bounding set and the helpers before every build.

`FACTORY_BUILD_NETWORK` accepts `default` (production), `none`, or `host`
(explicit only).

Containers the product tests start get no network namespace of their own: a
step container has no `/dev/net/tun`, so rootless Podman's `pasta` and
`slirp4netns` cannot create one. `toolchain/containers.conf` sets
`netns = "none"` as the default, one-shot checks pass `--network none`, and the
Atlassian integration run passes `--network host`, which inside a pod is the
pod's own namespace (never the node's) and lets the runner poll the product on
loopback at its fixed port. A unit test keeps every `podman run` explicit.

## Tekton Chains

`deploy/chains/chains-config.yaml` sets the `slsa/v2alpha3` provenance format
for TaskRuns and PipelineRuns, OCI storage, and no public transparency log.
In Chains v0.28, `slsa/v2alpha3` produces SLSA v1.0 provenance from Tekton `v1`
objects; the similarly named `slsa/v1` is an alias of `in-toto` and produces the
older SLSA v0.2 format
([Chains configuration](https://github.com/tektoncd/chains/blob/v0.28.0/docs/config.md)).
Use a KMS signer in production. Chains signs the digests in the
`IMAGE_URL`/`IMAGE_DIGEST` results of the quarantine and promotion tasks with
its own key; the factory's environment keys still produce the attestations that
gate release.

## Source pins and Renovate

- `vendir/config.yml` pins Repo One sources. `source.revision` and the matching
  vendir ref must move together (enforced for agent changes).
- `scripts/update_source_pins.sh` (`make update-pins`) updates pins by hand. The
  `upstream-sync` agent does the same with overlay rebasing and opens a PR.
- `renovate.json` updates `tools/versions.lock.yaml` (including Tekton, PaC,
  Chains, tkn, and Claude Code). Every update needs human review.

## Vulnerability exceptions

`policies/exceptions/approved.json` affects vulnerability-threshold denials
only. It never affects evidence identity, data freshness, compliance, tests, or
signatures. Humans add exceptions; the `exception-steward` agent may only
propose removals.

## Cosign storage and promotion

Cosign 2.x stores signatures and attestations under tags derived from the
image digest. Promotion copies both OCI referrers with `oras cp --recursive`
(including the evidence bundle) and these tags with `cosign copy`.
