# Local cluster harness (kind)

[Project overview](../README.md) · [Harness README](../tests/integration/kind/README.md) · [Configuration](configuration.md)

The harness is the only local build loop. It runs the factory's own Tekton
Tasks on a disposable [kind](https://kind.sigs.k8s.io/) cluster (Kubernetes in
containers, run by Podman or Docker on your machine) with the real runner image,
so what you test locally is what the pipeline runs.

## What it proves

`make harness-run` executes the `harness-build` Pipeline for one catalog base
image (`ubi9-minimal` by default, `IMAGE=ubi10-minimal` also works):

| Task | Real factory Task? | Proves |
|---|---|---|
| `checkout` (`harness-source`) | harness stand-in | the reviewed source snapshot is committed into the run workspace |
| `validate` (`factory-stage`) | yes | catalog validation and the verify → run → seal steps on a factory pod as UID 10001 |
| `prepare` (`factory-stage` running `harness-prepare.sh`) | Task yes, script harness-only | clone the pinned Iron Bank revision from Repo One, apply overlays, validate the context, pull the public UBI base |
| `build` (`factory-rootless-build`) | yes | `runtime_preflight.sh`, then a rootless BuildKit build in a pod user namespace with the production security context |
| `sbom` (`factory-stage`) | yes | Syft CycloneDX and SPDX SBOMs plus `sbom.identity.json` |
| `test` (`factory-rootless-test`) | yes | the base test profile and RPM integrity checks in rootless Podman |
| `consume` (`factory-stage`) | yes | a downstream task verifies an upstream seal before reading |
| `tamper-detected` (`factory-stage`) | yes | a wrong seal digest is rejected in-cluster (this task must **fail**) |

Applying every Task and Pipeline also proves the Tekton webhook accepts them.

What it does not do: scan (no security-data bundle), gate, quarantine, sign,
promote, Pipelines-as-Code, Tekton Chains, or the agents. Those need
Artifactory, the intake key, credentials, and an LLM gateway. The lock written
by `harness-prepare.sh` is unsigned and marked `localDevelopment`, which the
import and signing scripts refuse, so a harness build can never leave the
cluster. Passing the harness does not establish production FIPS compliance,
network isolation, or application qualification.

## Host requirements

- Linux or macOS with Podman (default) or Docker, given at least 6 CPUs,
  12 GiB RAM, and 40 GiB disk
- `kind` at the version pinned in `tools/versions.lock.yaml` (`up.sh` checks),
  `kubectl`, `git`
- Python 3.11+ with the repository installed: `pip install -e '.[dev]'`
- Network access to GitHub releases (Tekton manifest, pinned tools), PyPI,
  `registry.access.redhat.com`, `cdn-ubi.redhat.com`, and `repo1.dso.mil`

Nothing else is installed on the host. `skopeo`, `buildctl`, `syft`, and the
rest live only in the runner image.

## Run it

```bash
make harness-up                   # cluster, runner image, Tekton, factory objects
make harness-run IMAGE=ubi9-minimal
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review log
make harness-down
```

`make harness-up` does, in order:

1. Checks the kind version and (rootless Podman) that your user has enough
   subordinate IDs for user-namespaced pods (111 × 262144); otherwise it tells
   you to use `FACTORY_HARNESS_ROOTFUL=true` or Docker
   (`KIND_EXPERIMENTAL_PROVIDER=docker`).
2. Creates the cluster with kubelet `userNamespaces.idsPerPod: 262144`, so the
   runner's `factory:100000:65536` subordinate range fits
   ([Kubernetes user namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/)).
3. Runs `toolchain/build-dist.sh` (pinned tools from GitHub releases, verified
   against the digests GitHub records, and a wheel of this repository), then
   builds `toolchain/Containerfile.factory-runner` on
   `registry.access.redhat.com/ubi9/ubi-minimal:<catalog version>` and loads it
   into the cluster.
4. Runs `deploy.sh`: installs the pinned Tekton Pipelines release, applies the
   factory ServiceAccounts, settings (with `FACTORY_RPM_SOURCE_MODE:
   public-upstream`, so builds install RPMs from Red Hat's public UBI CDN),
   Tasks, Pipelines, and the harness objects.
5. In rootful mode only, `probe-crun.py` installs and probes a crun
   RuntimeClass and records its name in the state directory; `tekton.py` then
   sets `runtimeClassName` on the build and test pods.

`make harness-run` creates a PipelineRun with the same `taskRunSpecs` the
generated PipelineRuns use (`hostUsers: false` for build and test) and asserts
each task's outcome. Rerun `deploy.sh` alone after editing Tasks, Pipelines,
or scripts (`FACTORY_HARNESS_STATE=... KUBECONFIG=$FACTORY_HARNESS_STATE/kubeconfig tests/integration/kind/deploy.sh`);
the source snapshot must fit the 1 MiB ConfigMap limit.

## If the build stage fails

`runtime_preflight.sh` runs first and names what is missing. The usual causes
on a local cluster:

- the node runtime cannot create user namespaces for pods (needs containerd
  2.0+ or CRI-O; kind's node images ship containerd 2.x) — `node-diagnostics.sh`
  collects the runtime and kernel facts;
- runc cannot mount sysfs in a user-namespaced sandbox — use
  `FACTORY_HARNESS_ROOTFUL=true` so `probe-crun.py` provisions crun
  ([Podman discussion](https://github.com/containers/podman/discussions/19931),
  [Podman issue](https://github.com/containers/podman/issues/13194));
- the `ProcMountType` or `UserNamespacesSupport` feature gate is off (both are
  on by default from Kubernetes 1.33;
  [feature gates](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/)).

```bash
kubectl --kubeconfig "$FACTORY_HARNESS_STATE/kubeconfig" -n factory-harness get events --sort-by=.lastTimestamp
kubectl --kubeconfig "$FACTORY_HARNESS_STATE/kubeconfig" -n factory-harness get taskruns
sudo -v && tests/integration/kind/node-diagnostics.sh
```

## Teardown

```bash
make harness-down
```

Only the recorded harness cluster is deleted; state stays on disk and is ignored
by git. State directories may hold the cluster kubeconfig; never commit them.
