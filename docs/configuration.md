# Configuration reference

[Project overview](../README.md) · [Scope](scope.md) · [Operations](operations.md) · [Agents](agents.md) · [Architecture](architecture.md)

Terms: **PaC** is [Pipelines-as-Code](https://pipelinesascode.com/docs/), which
starts Tekton pipelines from Git events. A **Repository CR** is the Kubernetes
object that connects a Git repository to PaC.

## Quick setup checklist

1. **Cluster prerequisites** (versions in `tools/versions.lock.yaml`)
   - Kubernetes 1.28 or later with containerd 1.7 or later. No pod user
     namespaces, `procMount`, or `appArmorProfile` field are used
     ([BuildKit](#buildkit), [scope.md](scope.md)).
   - ValidatingAdmissionPolicy enabled for `deploy/base/admission-policy.yaml`.
     On 1.28 it is beta and off by default: start kube-apiserver with
     `--feature-gates=ValidatingAdmissionPolicy=true` and
     `--runtime-config=admissionregistration.k8s.io/v1beta1=true`. The manifest
     uses `v1beta1`; on 1.30 or later change both objects to
     `admissionregistration.k8s.io/v1`. Applying it to a cluster that does not
     serve the API fails, so the deploy stops rather than running without it.
   - Build nodes that allow unprivileged user namespaces, ideally a dedicated
     tainted pool for build pods.
   - [Tekton Pipelines](https://tekton.dev/docs/pipelines/) v1.15,
     Pipelines-as-Code v0.51, [Tekton Chains](https://github.com/tektoncd/chains/blob/v0.28.0/docs/config.md) v0.28
   - A default StorageClass for the per-run `volumeClaimTemplate` workspaces
2. **Build, sign, and publish the three runner images** and record their
   digests in the environment manifest (see [Runner images](#runner-images)):
   - `RUNNER_IMAGE` (Repository param `runner_image`) — `toolchain/Containerfile.factory-runner`
   - `INTAKE_RUNNER_IMAGE` (`intake_runner_image`) — `toolchain/Containerfile.factory-intake-runner`
   - `AGENT_IMAGE` (`agent_image`) — `toolchain/Containerfile.factory-agent`
3. **Describe the environment**: copy `deploy/environments/example` to
   `deploy/environments/<env>` and fill in `manifest/environment.yaml`
   ([Environment manifest](#environment-manifest)). Then apply the factory
   objects with `kubectl apply -k deploy/environments/<env>`.
4. **Create the Secrets** listed in `deploy/secrets.example.yaml` with your
   secret manager.
5. **Configure Chains** with `kubectl apply -k deploy/environments/<env>/chains`
   and a signing key (KMS recommended).
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

## Environment manifest

Everything that differs between installations — server names, the git
provider and project, source-mirror group, Artifactory host and repositories,
the UBI mirror, the PaC controller URL, the LLM gateway, runner image digests,
release approvers, egress CIDRs, and the Chains builder ID — lives in one file
per environment, `deploy/environments/<env>/manifest/environment.yaml`.
`deploy/base`, `deploy/chains`, the Tasks, and the scripts hold no environment
values; fields that need one say `from-environment`.

```
deploy/
  base/                         environment-neutral factory objects
  chains/                       environment-neutral Chains config
  components/
    environment/                copies manifest keys into the objects that use them
    local-cluster/              single-node profile: no NetworkPolicies, agents off
  environments/
    example/                    placeholders, production-shaped
      manifest/environment.yaml   the environment manifest (documents every key)
      kustomization.yaml          factory objects: kubectl apply -k deploy/environments/example
      chains/                     Chains config:   kubectl apply -k deploy/environments/example/chains
    dev/                        dev GitLab and Artifactory on a single-node cluster
```

The manifest is a ConfigMap named `factory-environment` marked
`config.kubernetes.io/local-config`, so Kustomize reads it but never applies
it. `deploy/components/environment` maps each key to its targets with
Kustomize `replacements`:

| Manifest keys | Filled into |
|---|---|
| settings keys (`ARTIFACTORY_*`, `FACTORY_*_REPOSITORY`, `INTERNAL_GIT_BASE_URL`, `FACTORY_UBI_MIRROR_URL`, `FACTORY_PAC_CONTROLLER_URL`, `FACTORY_GITLAB_API_URL`, `SCM_BOT_AUTHOR_*`, `ANTHROPIC_BASE_URL`, `CISA_KEV_URL`, `FACTORY_GOV_APPROVER_PATTERN`) | ConfigMap `factory-settings`, under the same name |
| `GIT_PROVIDER`, `GIT_PROVIDER_URL`, `GIT_REPOSITORY_URL` | Repository CR `spec.git_provider.type`, `.url`, `spec.url`, and the `git_provider` param |
| `RUNNER_IMAGE`, `INTAKE_RUNNER_IMAGE`, `AGENT_IMAGE` | Repository CR image params; `RUNNER_IMAGE` also runs the schedule CronJobs |
| `*_CIDR` | the egress NetworkPolicies |
| `CHAINS_BUILDER_ID` | `chains-config` `builder.id` |

To add an environment, copy `deploy/environments/example` and edit only its
manifest; add `../../components/local-cluster` to its `components` for a
single-node cluster. Secrets never go in the manifest
(`deploy/secrets.example.yaml`). `tests/unit/test_deploy_overlays.py` fails if
a manifest key is missing or unused, if a build leaves a `from-environment`
placeholder, or if a value unique to one environment appears in any tracked
file outside that environment's directory.

## Pipelines-as-Code Repository (`deploy/base/repository.yaml`)

| Field | Value | Why |
|---|---|---|
| `url` | the GitLab project URL (`GIT_REPOSITORY_URL`) | which project's webhooks this namespace serves |
| `git_provider.type` / `url` / `secret` / `webhook_secret` | `GIT_PROVIDER`, `GIT_PROVIDER_URL`, Secret `factory-gitlab-webhook` keys `provider.token` and `webhook.secret` | API access and webhook validation for a self-hosted instance ([PaC GitLab setup](https://pipelinesascode.com/docs/install/gitlab/)) |
| `settings.pipelinerun_provenance` | `default_branch` | merge-request runs use PipelineRun definitions from `main`, never from the merge request ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/)) |
| `concurrency_limit` | `3` | at most three PipelineRuns at once; each holds a volume and up to 8 CPUs ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/concurrency/)) |
| `params` | `runner_image`, `intake_runner_image`, `agent_image`, `git_provider`, `enable_agents` | digest-pinned images, the provider, and the cluster-wide agent switch, filled into the generated PipelineRuns |
| `incoming` | `webhook-url`, Secret `factory-pac-incoming`, target `main` | lets CronJobs and the release pipeline start runs ([PaC docs](https://pipelinesascode.com/docs/advanced/incoming-webhooks/)) |

## Settings (ConfigMap `factory-settings`)

Every factory step receives these as environment variables (`envFrom`). Keys
marked *env* come from the environment manifest; the rest are shared defaults
in `deploy/base/settings.yaml`.

| Key | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` (*env*) | internal SCM namespace that holds the source mirrors |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` (*env*) | Artifactory API base URL and OCI registry host |
| `ARTIFACTORY_USERNAME` (*env*) | registry login name paired with the `factory-artifactory-*` tokens (the user the tokens were issued to, for example the bot account); REST calls send the token as a bearer and need no name |
| `FACTORY_SOURCE_REPOSITORY` (*env*) | generic repository for locks, intake files, release pointers, and the security-data bundle |
| `UPSTREAM_OCI_REPOSITORY` (*env*) | digest-pinned upstream base images |
| `FACTORY_{BASE,APPLICATION}_QUARANTINE_REPOSITORY` (*env*) | protected candidate repositories; the catalog `publication.quarantineRepository` field expands these names |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` (*env*) | release and canary repositories; expanded from the catalog `publication.releaseRepository` field |
| `FACTORY_CATALOG_DIR` | catalog directory (`catalog/images`) |
| `FACTORY_UBI_MIRROR_URL` (*env*) | internal mirror of the public UBI RPM content (`https://cdn-ubi.redhat.com/content/public/ubi/dist`, same layout). Builds read `ubi<major>/<major>/<arch>/{baseos,appstream}/os` under it with Secret `factory-rpm-mirror` |
| `FACTORY_DEFAULT_BRANCH` | branch that scheduled runs and agent change requests target |
| `FACTORY_GOV_APPROVER_PATTERN` (*env*) | regular expression the merging approver must match for gov1/gov2 |
| `FACTORY_POINTER_ENVIRONMENT` | environment whose base releases update the release pointer (`commercial`) |
| `CISA_KEV_URL` (*env*) | CISA KEV (Known Exploited Vulnerabilities) JSON feed, linked from the [KEV catalog page](https://www.cisa.gov/known-exploited-vulnerabilities-catalog); used when building the security-data bundle |
| `FACTORY_UPSTREAM_BRANCH` | Repo One branch followed by intake and upstream-sync |
| `FACTORY_PAC_CONTROLLER_URL` (*env*) / `FACTORY_PAC_REPOSITORY` | PaC incoming endpoint and Repository CR name |
| `FACTORY_GITLAB_API_URL` (*env*) | GitLab API for agent comments and merge requests; defaults to `https://<git host>/api/v4` (`FACTORY_GITHUB_API_URL` applies only when `git_provider` is `github`) |
| `SCM_BOT_AUTHOR_NAME` / `SCM_BOT_AUTHOR_EMAIL` (*env*) | commit identity for agent proposals and release requests |
| `ANTHROPIC_BASE_URL` (*env*) | Anthropic-format [LLM gateway](https://code.claude.com/docs/en/llm-gateway) for Claude Code |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` / `_OPUS_MODEL` | optional gateway model names for the `sonnet`/`opus` aliases ([model configuration](https://code.claude.com/docs/en/model-config)); to use them, add the key to the environment manifests and a matching entry to `deploy/components/environment` |
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
(include patterns on the prefix) and one access token per target. The dev
environment (`deploy/environments/dev/manifest/environment.yaml`) is a worked
example on one docker and one generic repository; the example environment uses
neutral placeholder names. The prefixes below are the dev layout.

| Factory setting | Dev prefix | Holds | Token (Secret) and scope |
|---|---|---|---|
| runner images (`*_IMAGE` manifest keys) | `<docker repository>/factory/` | the three runner images, by digest | pushed by an operator; read by the kubelet |
| `UPSTREAM_OCI_REPOSITORY` (*env*) | `<docker repository>/upstream` | Iron Bank bases imported by intake | `factory-artifactory-intake` write; `-read` read |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` | `<docker repository>/quarantine/bases` | gated base candidates and their evidence referrers | `factory-artifactory-quarantine` write |
| `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | `<docker repository>/quarantine/apps` | gated application candidates | same |
| `FACTORY_RELEASE_REPOSITORY` | `<docker repository>/release` | promoted images, cosign signatures and attestations | `factory-artifactory-release` write; `-sign` for signatures |
| `FACTORY_CANARY_REPOSITORY` | `<docker repository>/canary` | canary-track images (none today) | release |
| `FACTORY_SOURCE_REPOSITORY` (*env*) | `<generic repository>/factory` | `locks/` (signed resource locks), intake resources, `releases/<image>/current.json` (pointers), `security-data/` (signed scanner bundle) | `-intake` writes locks, resources, and the bundle; `-pointer` writes pointers; `-read` reads |
| `FACTORY_UBI_MIRROR_URL` (*env*) | `https://<host>/artifactory/<UBI remote repository>/content/public/ubi/dist` | remote repository in front of the UBI CDN, same layout | `factory-rpm-mirror` (bot account + token) read |

Permission targets for the dev environment (repository, include pattern, who):

| Target | Repository | Include pattern | Permission | Used by |
|---|---|---|---|---|
| factory-read | docker, generic, UBI remote | `**` | read | `factory-artifactory-read`, `factory-rpm-mirror` |
| factory-intake | docker | `upstream/**` | read, deploy | `factory-artifactory-intake` |
| factory-intake | generic | `factory/locks/**`, `factory/resources/**`, `factory/security-data/**` | read, deploy | `factory-artifactory-intake` |
| factory-quarantine | docker | `quarantine/**` | read, deploy | `factory-artifactory-quarantine` |
| factory-sign | docker | `quarantine/**`, `release/**` | read, deploy (tags `sha256-*.sig`, `.att`) | `factory-artifactory-sign` |
| factory-release | docker | `release/**`, `canary/**` | read, deploy | `factory-artifactory-release` |
| factory-pointer | generic | `factory/releases/**` | read, deploy | `factory-artifactory-pointer` |

No token can remove artifacts, and no token's include pattern crosses from
quarantine to release except the signature token, which writes only signature
and attestation tags next to an existing digest.

If the instance grants `anonymous` read (the dev instance does), the read
rows above are redundant and only the deploy grants need creating; the
scripts still present the read tokens, which is harmless. One exception is
required: exclude `quarantine/**` and `upstream/**` from the anonymous read
permission on the docker repository. Those prefixes hold candidates that are
built but not gated, and unmodified upstream bases that have not been through
the pipeline; the design assumes only the factory's tokens can pull them, so
that nobody consumes an unreleased image because its tag happened to exist.
`release/**`, `canary/**`, and the generic repository (signed locks, release
pointers, the signed security-data bundle) can stay anonymously readable.

What the given repositories cannot provide:

- **RPM content** does not come from the local rpm repository: a *local*
  repository serves only what is uploaded to it, and the build expects the UBI
  CDN layout (`ubi9/9/x86_64/baseos/os/…`) under `FACTORY_UBI_MIRROR_URL`. The
  dev instance already has a *remote* repository in front of
  `https://cdn-ubi.redhat.com`, so the dev manifest points
  `FACTORY_UBI_MIRROR_URL` at `…/artifactory/<remote>/content/public/ubi/dist`
  and the catalog's `build.rpm.source: private-mirror` applies. `factory-rpm-mirror`
  holds the bot account and its token (Artifactory accepts a token as the
  basic-auth password). Without such a remote repository, set
  `FACTORY_RPM_SOURCE_MODE: public-upstream` (in `factory-settings`) to install from the CDN directly;
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
   OSV-Scanner) and the shadow source archive (see
   [User-namespace helpers](#user-namespace-helpers)), checking each against the SHA-256 digest GitHub records
   for the asset, and refusing any asset without one. With `--with-claude` it also
   fetches the Claude Code binary and checks it against the release manifest.
   Versions come only from `tools/versions.lock.yaml`.
2. `pip wheel` packages this repository and its dependencies for Python 3.12 on
   the runner.

Then:

| Image | Containerfile | Base | Adds |
|---|---|---|---|
| `runner_image` | `Containerfile.factory-runner` | `BASE_REF`: the internal hardened UBI 9 minimal (or `registry.access.redhat.com/ubi9/ubi-minimal` for a connected development build) | UBI packages (Python 3.12, Skopeo, git, jq, curl, libcap), the pinned tools, the factory package, the `factory` user with subordinate IDs, `newuidmap`/`newgidmap` compiled with libcap in a throwaway build stage (needs `gcc`, `make`, `libcap-devel`, `glibc-devel`, `libxcrypt-devel`, `xz` from the UBI AppStream/BaseOS mirror) and installed with file capabilities. No container engine. |
| `intake_runner_image` | `Containerfile.factory-intake-runner` | the runner image | nothing beyond a tool check; a separate digest for the connected trust class |
| `agent_image` | `Containerfile.factory-agent` | the runner image | the pinned Claude Code binary |

Because every tool is in the runner, no stage downloads anything at run time.

Behind a TLS-inspecting proxy (Zscaler and similar), export the proxy root as
PEM and set `FACTORY_CA_BUNDLE` before `make toolchain`, for example on macOS:

```bash
security find-certificate -a -c "Zscaler Root CA" -p /Library/Keychains/System.keychain >~/zscaler-root.pem
openssl x509 -in ~/zscaler-root.pem -noout -subject      # sanity check
export FACTORY_CA_BUNDLE=~/zscaler-root.pem
make toolchain
```

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

## BuildKit

The build run step starts a short-lived rootless `buildkitd` inside the step
container (`scripts/run_buildkit.sh`) with `--oci-worker-no-process-sandbox`.
There is no shared daemon, host socket, privileged sidecar, or other container
engine; nothing in the pipeline runs the candidate image ([scope.md](scope.md)).

The run step needs, and is the only container that has:

- `allowPrivilegeEscalation: true` and
  `capabilities: {drop: [ALL], add: [SETUID, SETGID]}`, for the mapping helpers
  (next section);
- `seccompProfile: Unconfined`, because the runtime default profile blocks the
  `unshare` and `mount` calls RootlessKit makes. A `Localhost` profile that
  allows them can replace it;
- AppArmor `unconfined`, set by the annotation
  `container.apparmor.security.beta.kubernetes.io/step-run` that
  `factory/tekton.py` renders onto the build TaskRun
  (`taskRunSpecs[].metadata.annotations`; Tekton copies TaskRun annotations to
  the pod). The `securityContext.appArmorProfile` field only exists from
  Kubernetes 1.30; on 1.30 or later the annotation still works and the field
  can replace it.

It does not use pod user namespaces (`hostUsers: false`) or
`procMount: Unmasked`: both are unavailable on Kubernetes 1.28 with
containerd 1.7, and the no-process-sandbox mode does not need them. Every other
step uses `RuntimeDefault` seccomp with all capabilities dropped. For that
reason the namespace's
[Pod Security](https://kubernetes.io/docs/concepts/security/pod-security-admission/)
level is `privileged`, with `restricted` warnings and audit.

Nodes must allow unprivileged user namespaces
(`/proc/sys/user/max_user_namespaces` above zero; on Ubuntu 23.10 and later,
`kernel.apparmor_restrict_unprivileged_userns=0` or an AppArmor profile that
permits them). `scripts/runtime_preflight.sh` records these facts in
`evidence/runtime/runtime.txt` before every build.

### User-namespace helpers

RootlessKit maps the `factory` user's subordinate range (`100000:65536`) into a
child user namespace by running `newuidmap` and `newgidmap`, which write
`/proc/<pid>/uid_map` and `gid_map`.

- The helpers carry file capabilities, as RHEL ships them: `newuidmap` has
  `cap_setuid=ep` and `newgidmap` has `cap_setgid=ep`. Neither is setuid, so
  nothing in the image runs with euid 0.
- A file capability is granted only if it is in the caller's *bounding set* and
  `no_new_privs` is unset
  ([capabilities(7)](https://man7.org/linux/man-pages/man7/capabilities.7.html)).
  The run step therefore keeps `CAP_SETUID` and `CAP_SETGID` in the bounding
  set and allows privilege escalation. The step process itself runs as uid
  10001 with no effective capabilities; they only materialise inside the two
  helpers.
- The runner compiles the helpers from the pinned
  [shadow](https://github.com/shadow-maint/shadow) release with `libcap-devel`
  present: that build raises only the one capability it needs while writing
  the map and drops the rest (`lib/idmapping.c`, `write_mapping`).
- The runner image must be built by a builder that preserves
  `security.capability` extended attributes in layers (BuildKit does).
  `scripts/runtime_preflight.sh` checks the bounding set, `no_new_privs`, and
  the helpers' file capabilities before every build and names whichever is
  missing.

Because pods share the host user namespace, the build pod's UID and its
subordinate range are real host UIDs on the node. Run build pods on a
dedicated, tainted node pool ([scope.md](scope.md#security-trade-off-accepted-with-this-scope)).

`FACTORY_BUILD_NETWORK` accepts `default` (production), `none`, or `host`
(explicit only).

## Tekton Chains

`deploy/chains/chains-config.yaml` (applied per environment with
`deploy/environments/<env>/chains`, which fills `builder.id` from
`CHAINS_BUILDER_ID`) sets the `slsa/v2alpha3` provenance format
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
only. It never affects evidence identity, data freshness, or signatures. Humans add exceptions; the `exception-steward` agent may only
propose removals.

## Cosign storage and promotion

Cosign 2.x stores signatures and attestations under tags derived from the
image digest. Promotion copies both OCI referrers with `oras cp --recursive`
(including the evidence bundle) and these tags with `cosign copy`.
