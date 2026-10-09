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
2. **Publish the three runner images** and record their digests in the
   Repository CR params:
   - `runner_image` — `toolchain/Containerfile.factory-runner`
   - `intake_runner_image` — no Containerfile in this repository; it must
     provide the tools listed in [Intake runner image](#intake-runner-image)
   - `agent_image` — `toolchain/Containerfile.factory-agent`
3. **Apply the factory objects**: edit `deploy/base/settings.yaml`,
   `deploy/base/repository.yaml`, and the CIDRs in `deploy/base/network-policies.yaml`,
   then run `kubectl apply -k deploy/base`.
4. **Create the Secrets** listed in `deploy/secrets.example.yaml` with your
   secret manager.
5. **Configure Chains** with `deploy/chains/chains-config.yaml` and a signing key
   (KMS recommended).
6. **Connect the Git provider** (PaC GitHub App, or a GitHub, GitLab, or
   Bitbucket webhook) and protect `main` with required reviews, CODEOWNERS, and
   required status checks. Two features the factory relies on are limited by
   provider: the per-image path filters use `.pathChanged()`, which PaC
   [supports on GitHub and GitLab only](https://pipelinesascode.com/docs/guides/event-matching/cel-expressions/),
   and the Repository CR `policy` setting is
   [supported on GitHub and Forgejo only](https://pipelinesascode.com/docs/advanced/policy-authorization/).
7. Confirm contributors can run `make ci` locally.

## Pipelines-as-Code Repository (`deploy/base/repository.yaml`)

| Field | Value | Why |
|---|---|---|
| `settings.pipelinerun_provenance` | `default_branch` | PR runs use PipelineRun definitions from `main`, never from the PR ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/)) |
| `settings.policy.pull_request` / `ok_to_test` | maintainer team (add the bot account) | who may trigger PR runs ([PaC docs](https://pipelinesascode.com/docs/advanced/policy-authorization/)) |
| `concurrency_limit` | `3` | at most three PipelineRuns at once; each holds a volume and up to 8 CPUs ([PaC docs](https://pipelinesascode.com/docs/guides/repository-crd/concurrency/)) |
| `params` | `runner_image`, `intake_runner_image`, `agent_image`, `git_provider` | digest-pinned images and the provider, filled into the generated PipelineRuns |
| `incoming` | `webhook-url`, Secret `factory-pac-incoming`, target `main` | lets CronJobs and the release pipeline start runs ([PaC docs](https://pipelinesascode.com/docs/advanced/incoming-webhooks/)) |

## Settings (`deploy/base/settings.yaml`, ConfigMap `factory-settings`)

Every factory step receives these as environment variables (`envFrom`).

| Key | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` | internal SCM namespace that holds the source mirrors |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Artifactory API base URL and OCI registry host |
| `FACTORY_SOURCE_REPOSITORY` | generic repository for locks, intake files, release pointers, and the security-data bundle |
| `UPSTREAM_OCI_REPOSITORY` | digest-pinned upstream base images |
| `FACTORY_{BASE,APPLICATION}_QUARANTINE_REPOSITORY` | protected candidate repositories; the catalog `publication.quarantineRepository` field expands these names |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | release and canary repositories; expanded from the catalog `publication.releaseRepository` field |
| `FACTORY_CATALOG_DIR` | catalog directory (`catalog/images`) |
| `FACTORY_DEFAULT_BRANCH` | branch that scheduled runs and agent change requests target |
| `FACTORY_GOV_APPROVER_PATTERN` | regular expression the merging approver must match for gov1/gov2 |
| `FACTORY_POINTER_ENVIRONMENT` | environment whose base releases update the release pointer (`commercial`) |
| `CISA_KEV_URL` | CISA KEV (Known Exploited Vulnerabilities) JSON feed, linked from the [KEV catalog page](https://www.cisa.gov/known-exploited-vulnerabilities-catalog); used when building the security-data bundle |
| `COMPLIANCE_AS_CODE_DATASTREAM_DIR` | directory in the intake runner image that holds the [ComplianceAsCode](https://github.com/ComplianceAsCode/content) SCAP datastreams `ssg-rhel9-ds.xml` and `ssg-rhel10-ds.xml` |
| `FACTORY_UPSTREAM_BRANCH` | Repo One branch followed by intake and upstream-sync |
| `FACTORY_PAC_CONTROLLER_URL` / `FACTORY_PAC_REPOSITORY` | PaC incoming endpoint and Repository CR name |
| `FACTORY_GITHUB_API_URL` / `FACTORY_GITLAB_API_URL` | provider APIs for agent comments and change requests (GitLab defaults to `https://<git host>/api/v4`) |
| `SCM_BOT_AUTHOR_NAME` / `SCM_BOT_AUTHOR_EMAIL` | commit identity for agent proposals and release requests |
| `ANTHROPIC_BASE_URL` | Anthropic-format [LLM gateway](https://code.claude.com/docs/en/llm-gateway) for Claude Code |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` / `_OPUS_MODEL` | optional gateway model names for the `sonnet`/`opus` aliases ([model configuration](https://code.claude.com/docs/en/model-config)) |
| `FACTORY_AGENT_TIMEOUT` | wall-clock limit per agent run |

## Secrets

Secret names and keys are fixed by the Tasks (see `deploy/secrets.example.yaml`).
Each is mounted only into the step that needs it.

| Secret | Keys | Pipeline task |
|---|---|---|
| `factory-artifactory-read` | `token` | prepare, build, resolve, evidence-context |
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
| `enable-agents` (triage) | true | true | true | true |
| `enable-remediation` | false | true | true | true |
| `enable-release-request` | false | true | false | true |

## Intake runner image

The `intake_runner_image` runs the intake, security-data, and upstream-context
tasks. It must provide:

- `grype`, `trivy`, `osv-scanner`, and `freshclam` (to download scanner
  databases and ClamAV signatures)
- `clamscan` (intake scans every mirrored resource; the intake task refreshes
  its own signatures with `freshclam` first)
- `cosign`, `skopeo`, `curl`, `jq`, `yq`, `git`, and Python 3
- ComplianceAsCode SCAP content in `COMPLIANCE_AS_CODE_DATASTREAM_DIR`

The main `runner_image` no longer contains scanner data. Scan and compliance
stages use the bundle that the prepare stage downloads; see
[security-data/README.md](../security-data/README.md).

## BuildKit and Podman

The build and test run steps start a short-lived rootless `buildkitd` or
rootless Podman inside the step container. There is no shared daemon, host
socket, or privileged sidecar. These two steps need
`allowPrivilegeEscalation: true` and Unconfined seccomp/AppArmor because
`newuidmap`/`newgidmap` are setuid programs. Every other step uses
`RuntimeDefault` seccomp with all capabilities dropped. For that reason the
namespace's [Pod Security](https://kubernetes.io/docs/concepts/security/pod-security-admission/)
level is `privileged`, with `restricted` warnings and audit.

`FACTORY_BUILD_NETWORK` accepts `default` (production), `none`, or `host`
(explicit only).

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
