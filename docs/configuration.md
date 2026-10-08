# Configuration reference

[Project overview](../README.md) · [Operations](operations.md) · [Agents](agents.md)

## Quick setup checklist

1. **Cluster prerequisites** (versions in `tools/versions.lock.yaml`)
   - Kubernetes ≥ 1.30 with unprivileged user namespaces on build nodes
   - Tekton Pipelines **v1.15 LTS**, Pipelines-as-Code **v0.51**, Tekton Chains **v0.28**
   - A default StorageClass for per-run `volumeClaimTemplate` workspaces
   - Nodes for compliance labelled `factory.dev/fips-node=true` and tainted
     `factory.dev/fips-node=true:NoSchedule`
2. **Publish runner images** and record their digests in the Repository CR params:
   `runner_image` (`toolchain/Containerfile.factory-runner`), `intake_runner_image`,
   `agent_image` (`toolchain/Containerfile.factory-agent`).
3. **Apply the factory objects**: `kubectl apply -k deploy/base` after editing
   `settings.yaml`, `repository.yaml`, and the CIDRs in `network-policies.yaml`.
4. **Create the Secrets** listed in `deploy/secrets.example.yaml` with your secret manager.
5. **Configure Chains** with `deploy/chains/chains-config.yaml` and a signing key
   (KMS recommended).
6. **Install the PaC GitHub App** (or GitLab/Bitbucket webhook) for the repository
   and protect `main`: required reviews, CODEOWNERS, required status checks.
7. Confirm contributors can run `make ci` locally.

## Pipelines-as-Code Repository (`deploy/base/repository.yaml`)

| Field | Value | Why |
|---|---|---|
| `settings.pipelinerun_provenance` | `default_branch` | PR runs use PipelineRun definitions from `main`, never from the PR |
| `settings.policy.pull_request` / `ok_to_test` | maintainer team (+ bot) | who may trigger PR runs |
| `concurrency_limit` | `3` | bounds concurrent runs (PVC + up to 8 CPUs each) |
| `params` | `runner_image`, `intake_runner_image`, `agent_image`, `git_provider` | digest-pinned images and provider, templated into PipelineRuns |
| `incoming` | `webhook-url`, Secret `factory-pac-incoming`, target `main` | schedules and base-release fan-out |

## Settings (`deploy/base/settings.yaml`, ConfigMap `factory-settings`)

Injected into every factory step with `envFrom`; replaces the Jenkins global settings.

| Key | Purpose |
|---|---|
| `INTERNAL_GIT_BASE_URL` | internal SCM namespace with source mirrors |
| `ARTIFACTORY_URL` / `ARTIFACTORY_REGISTRY` | Artifactory API base and OCI registry host |
| `FACTORY_SOURCE_REPOSITORY` | generic repo for locks, intake files, release pointers |
| `UPSTREAM_OCI_REPOSITORY` | digest-pinned upstream bases |
| `FACTORY_{BASE,APPLICATION}_QUARANTINE_REPOSITORY` | protected candidate repositories |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | release and canary repositories |
| `FACTORY_DEFAULT_BRANCH` | branch the generated triggers and broker target |
| `FACTORY_GOV_APPROVER_PATTERN` | regex the merging approver must match for gov1/gov2 |
| `FACTORY_POINTER_ENVIRONMENT` | environment whose base releases update the release pointer (`commercial`) |
| `FACTORY_UPSTREAM_BRANCH` | Repo One branch followed by intake and upstream-sync |
| `FACTORY_PAC_CONTROLLER_URL` / `FACTORY_PAC_REPOSITORY` | PaC incoming endpoint and Repository CR name |
| `FACTORY_GITHUB_API_URL` / `FACTORY_GITLAB_API_URL` | provider APIs for agent comments and change requests |
| `SCM_BOT_AUTHOR_NAME` / `SCM_BOT_AUTHOR_EMAIL` | commit identity for agent proposals and release requests |
| `ANTHROPIC_BASE_URL` | Anthropic-format LLM gateway for Claude Code |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` / `_OPUS_MODEL` | optional gateway model names for the aliases |
| `FACTORY_AGENT_TIMEOUT` | wall-clock cap per agent run |

## Secrets

Names and keys are fixed by the Tasks (see `deploy/secrets.example.yaml`). Each is
mounted into exactly one step.

| Secret | Keys | Pipeline task |
|---|---|---|
| `factory-artifactory-read` | `token` | prepare, build, resolve, evidence-context |
| `factory-intake-cosign-public-key` | `cosign.pub` | prepare |
| `factory-artifactory-quarantine` | `token` | quarantine |
| `factory-artifactory-sign` | `token` | attest |
| `factory-artifactory-release` | `token` | promote |
| `factory-artifactory-pointer` | `token` | promote (pointer step) |
| `factory-cosign-{commercial,gov1,gov2}` | `cosign.key`, `password`, `cosign.pub` | attest, promote |
| `factory-artifactory-intake` | `token` | intake |
| `factory-intake-cosign` | `cosign.key`, `password`, `cosign.pub` | intake |
| `factory-scm-mirror` | `username`, `token` | intake |
| `factory-scm-bot` | `username`, `token` | agent publish step (default branch only) |
| `factory-ai-gateway` | `token` | agent step |
| `factory-pac-incoming` | `secret` | Repository incoming, schedules, dependents |

Create one Cosign key pair per release environment:

```bash
cosign generate-key-pair --output-key-prefix cosign-commercial
```

## Stage toggles

Toggles are PipelineRun params rendered by `factory/tekton.py`; change the
renderer and run `make tekton-render` rather than editing the generated files.

| Param | PR | push | schedule | base-release |
|---|---|---|---|---|
| `publish` | false | true | false | true |
| `enable-agents` (triage) | true | true | true | true |
| `enable-remediation` | false | true | true | true |
| `enable-release-request` | false | true | false | true |
| `enable-copa` / `enable-helmper` / `enable-hummingbird` | false | false | false | false |

Optional stage commands (`FACTORY_COPA_COMMAND`, `FACTORY_HELMPER_COMMAND`,
`FACTORY_HUMMINGBIRD_COMMAND`) can be added to `factory-settings`.

## BuildKit and Podman

The build and test run steps execute an ephemeral rootless `buildkitd` or
rootless Podman inside the step container: no shared daemon, host socket, or
privileged sidecar. They need `allowPrivilegeEscalation: true` and Unconfined
seccomp/AppArmor for setuid `newuidmap`/`newgidmap`; every other step uses
`RuntimeDefault` seccomp with all capabilities dropped. Hence the namespace's
Pod Security level is `privileged` with `restricted` warnings and audit.

`FACTORY_BUILD_NETWORK` accepts `default` (production), `none`, or `host`
(explicit only).

## Tekton Chains

`deploy/chains/chains-config.yaml` sets SLSA v1 provenance for TaskRuns and
PipelineRuns, OCI storage, and no public transparency log. Use a KMS signer in
production. Chains signs the digests in `IMAGE_URL`/`IMAGE_DIGEST` results
(quarantine and promotion) with its own key; the factory's environment keys
still produce the release-gating attestations.

## Source pins and Renovate

- `vendir/config.yml` pins Repo One sources; `source.revision` and the matching
  vendir ref must move together (enforced for agent changes).
- `scripts/update_source_pins.sh` / `make update-pins` updates pins manually; the
  `upstream-sync` agent does the same with overlay rebasing and opens a PR.
- `renovate.json` updates `tools/versions.lock.yaml` (including Tekton, PaC,
  Chains, tkn, and Claude Code); updates require human review.

## Vulnerability exceptions

`policies/exceptions/approved.json` affects vulnerability-threshold denials only,
never evidence identity, freshness, compliance, tests, or signatures. Humans add
exceptions; the `exception-steward` agent may only propose removals.

## Cosign storage and promotion compatibility

Cosign 2.x uses digest-derived attachment tags. Promotion copies both ORAS
recursive referrers (including the evidence bundle) and Cosign attachment tags.
