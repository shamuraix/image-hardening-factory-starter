# Local harness

[Project overview](../README.md) · [Scope](scope.md) · [Rancher Desktop](local-rancher-desktop.md) · [Operations](operations.md)

The harness builds one catalog image on a cluster you already run, with the
production Tasks and pod settings, and no GitLab, Artifactory, or credentials.
Use it to check an overlay, a product upgrade, or a change to the build
scripts before opening a merge request. It is not part of `make ci`.

It runs the same configuration as production, on purpose: an existing
Kubernetes **1.28+** cluster with containerd, the production `factory-stage`
and `factory-rootless-build` Tasks with their production commands and seal
chain, and the build pod's AppArmor annotation exactly as `factory/tekton.py`
renders it. Nothing excluded by [scope.md](scope.md) is used: no pod user
namespaces, `procMount`, container engine in a pod, or test stage. The earlier
kind harness was removed because it tested a different configuration; this one
does not have a configuration of its own.

## What it runs

| Task | Production Task? | What it does |
|---|---|---|
| `checkout` (`harness-source`) | stand-in | commits a snapshot of your checkout (tracked and untracked, not ignored files) into the run's workspace |
| `validate` | yes | catalog validation |
| `base-prepare`, `base-build` | yes (prepare is the stand-in below) | application images only: build the catalog base (`build.base.image`) in the same run |
| `prepare` (`harness-prepare.sh`) | Task yes, script harness-only | clone the pinned Iron Bank revision from Repo One, apply the overlays, validate the context; base images pull the public UBI image matching the catalog version; application images verify the base build's seal, use that archive, and download each `hardening_manifest.yaml` resource over HTTPS and check it against the manifest's sha256/sha512 |
| `context-check` | yes | template and helper checks on the prepared context |
| `build` | yes | `runtime_preflight.sh`, then rootless BuildKit |
| `sbom` | yes | Syft CycloneDX and SPDX SBOMs |
| `image-check` | yes | image config and filesystem checks on the OCI archive |
| `consume` | yes | a later task verifies an earlier seal before reading |
| `tamper-detected` | yes | a wrong seal digest is rejected (this task must **fail**, in its verify step) |

Not run: `scan`, `assessment`, `gate`, `quarantine`, signing, promotion, intake,
Pipelines-as-Code, Chains, agents. They need the signed security-data bundle,
Artifactory, and credentials; use [local-rancher-desktop.md](local-rancher-desktop.md)
for those. The resource lock `prepare` writes is unsigned and marked
`localDevelopment`, which `import_image.sh` and `sign_and_attest.sh` refuse, so
a harness candidate can never be imported, signed, or promoted.

## Requirements

- A Kubernetes 1.28+ cluster with the **containerd** runtime and a default
  StorageClass, whose node allows unprivileged user namespaces (the build's
  RootlessKit creates one). Rancher Desktop set up as in
  [local-rancher-desktop.md](local-rancher-desktop.md), steps 1 and 3, is the
  documented one; `up.sh` checks the version and runtime.
- `kubectl`, `git`, `python3` with this repository installed
  (`pip install -e '.[dev]'`), and `nerdctl` (Rancher Desktop ships it) to
  build the runner into the node's containerd.
- Egress from the cluster to `repo1.dso.mil`, `registry.access.redhat.com`,
  `cdn-ubi.redhat.com`, and for application images to every host in their
  `hardening_manifest.yaml` (Atlassian downloads and Marketplace, GitHub,
  kernel.org). Behind a TLS-inspecting proxy, set `FACTORY_CA_BUNDLE` to its
  root certificate before `make harness-up`; the runner and the image builds
  trust it, and the built image does not.
- For application images, room for the run: the workspace requests 40 GiB
  and the build step up to 8 GiB of memory.

## Run it

```bash
export FACTORY_HARNESS_KUBECONFIG=~/.kube/config     # with Rancher Desktop's context selected
export FACTORY_CA_BUNDLE=~/zscaler-root.pem          # only behind a TLS-inspecting proxy
make harness-up                                      # runner image, Tekton, harness objects
make harness-run IMAGE=ubi10-minimal                 # a base image
make harness-run IMAGE=jira-lts                      # builds ubi10-minimal, then Jira on it
make harness-status                                  # re-check the last run
make harness-down                                    # delete the factory-harness namespace
```

`harness-up` copies the kubeconfig into `.local-factory/harness/` (git-ignored)
and records its context, so later runs never follow your shell to another
cluster. It builds `toolchain/Containerfile.factory-runner` for the node's
architecture into the node's containerd, installs Tekton Pipelines at the pinned
version, and runs `deploy.sh`.

`harness-run` runs `deploy.sh` first (source snapshot, Tasks, Pipelines,
settings), so a run always executes your checkout, then waits and prints each
task's outcome. When a task fails it prints the last 60 lines of its first
failed step. Evidence stays on the run's volume until the PipelineRun is
deleted:

```bash
tests/integration/local/workspace-exec.sh harness-jira-lts-abcde \
  ls -la /workspace/source/work/jira-lts/evidence
```

Settings are `deploy/base/settings.yaml` plus two harness overrides:
`FACTORY_RPM_SOURCE_MODE=public-upstream` (RPMs from Red Hat's public UBI CDN)
and, with `FACTORY_CA_BUNDLE`, the runner's merged trust store for build RUN
steps. The build Task's two Secrets get placeholder values; nothing on the
harness path reads them.

## Apple silicon

The catalog builds `linux/amd64`. On an arm64 node the runner is arm64 and
BuildKit runs the image's RUN steps through binfmt emulation (Rancher Desktop:
VZ with Rosetta). That proves the pipeline mechanics, not amd64 behaviour, and
the Atlassian builds are much slower. A crash of BuildKit's own `check` probe
under Rosetta is expected and harmless; `env.sh` keeps it from writing a core
file into the workspace.
