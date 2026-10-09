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
| `tamper-detected` (`factory-stage`, in `finally`) | yes | a wrong seal digest is rejected in-cluster: this task must **fail**, and `tekton.py` checks that its `verify` step is what failed |

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
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review status  # re-check the last run
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review log     # all step logs
make harness-down
```

`tekton.py` prints the PipelineRun's own reason and message, each task's
status, and the last lines of the first failed task's `run` step. A task
shown as `None` never got a TaskRun: the PipelineRun stopped scheduling before
it was reached, and the reason line says why. Pass the same `--state` directory
you gave `FACTORY_HARNESS_STATE`, if you set one.

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
   placeholder `factory-artifactory-read` and `factory-rpm-mirror` Secrets
   (the build Task binds them by `secretKeyRef`, so the pod cannot start
   without them; nothing on the harness path reads the values), Tasks,
   Pipelines, and the harness objects.
5. In rootful mode only, `probe-crun.py` installs and probes a crun
   RuntimeClass and records its name in the state directory; `tekton.py` then
   sets `runtimeClassName` on the build and test pods.

`make harness-run` first redeploys the source snapshot, Tasks, and Pipelines
(so the scripts you just edited or pulled are what runs), then creates a
PipelineRun with the same `taskRunSpecs` the generated PipelineRuns use
(`hostUsers: false` for build and test) and asserts each task's outcome,
printing the failed step's log when one fails. The source snapshot must fit
the 1 MiB ConfigMap limit.

## Using a cluster you already run (Rancher Desktop)

The harness can target an existing cluster instead of creating one with kind.
Rancher Desktop works when:

- Kubernetes **1.33 or later** is selected (the user-namespace and `procMount`
  feature gates are on by default from 1.33). k3s 1.33 ships containerd 2.x
  and runc 1.2+, which support pod user namespaces
  ([k3s release notes](https://docs.k3s.io/release-notes/v1.33.X)).
- The container engine is **containerd**, not dockerd. With dockerd, k3s uses
  cri-dockerd, which cannot run `hostUsers: false` pods.
- The kubelet allows 262144 IDs per pod. k3s reads kubelet drop-ins from
  `/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/`
  ([k3s configuration](https://docs.k3s.io/installation/configuration)); write
  one from a Rancher Desktop provisioning script, which runs before k3s starts
  ([provisioning scripts](https://docs.rancherdesktop.io/how-to-guides/provisioning-scripts)):

  ```yaml
  # macOS/Linux: ~/Library/Application Support/rancher-desktop/lima/_config/override.yaml
  provision:
    - mode: system
      script: |
        mkdir -p /var/lib/rancher/k3s/agent/etc/kubelet.conf.d
        cat >/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/10-factory-userns.conf <<'EOF'
        apiVersion: kubelet.config.k8s.io/v1beta1
        kind: KubeletConfiguration
        userNamespaces:
          idsPerPod: 262144
        EOF
  ```

  On Windows, put the same shell in `%LOCALAPPDATA%\rancher-desktop\provisioning\factory-userns.start`
  (Unix line endings). Restart Kubernetes afterwards; `up.sh` checks the value.
- The VM has at least 6 CPUs, 12 GiB RAM, and 40 GiB disk.

Then:

```bash
export FACTORY_HARNESS_KUBECONFIG=~/.kube/config          # Rancher Desktop's context
export FACTORY_HARNESS_STATE=.local-factory/rancher-desktop
make harness-up        # builds the runner, loads it with nerdctl, installs Tekton
make harness-run IMAGE=ubi9-minimal
```

`up.sh` builds the runner image straight into the cluster's containerd with
`nerdctl --namespace k8s.io build` (Rancher Desktop ships `nerdctl`), so no
second container engine is needed; set `FACTORY_HARNESS_BUILD_COMMAND` if yours
differs.
`make harness-down` refuses to touch a cluster it did not create; remove the
`factory-harness` and `tekton-pipelines` namespaces yourself. The same cluster
can then run the full pipeline (see
[operations.md](operations.md#running-the-full-pipeline-on-a-local-cluster)).

## Behind a TLS-inspecting proxy (Zscaler and similar)

The runner image is built on the public UBI base, which does not trust your
proxy's root certificate, so `microdnf` fails with *unable to get local issuer
certificate*. Export the root once and hand it to the harness:

```bash
# macOS: the proxy root is in the system keychain
security find-certificate -a -c "Zscaler Root CA" -p /Library/Keychains/System.keychain >~/zscaler-root.pem
openssl x509 -in ~/zscaler-root.pem -noout -subject      # sanity check
export FACTORY_CA_BUNDLE=~/zscaler-root.pem
make harness-up
```

What that does: `toolchain/build-dist.sh` copies the PEM into `dist/ca-trust/`,
the runner Containerfile adds it to the system trust store before its first
network access, and `deploy.sh` sets `FACTORY_CA_BUNDLE` in the harness
settings so `build_image.sh` mounts the runner's merged bundle (public roots
plus yours) into the RUN steps of the image being built — as a BuildKit
secret, never as a layer. Nothing is written into the built image.

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
  [feature gates](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/));
- `newuidmap: open of uid_map failed: Permission denied` from RootlessKit —
  the run step's capability bounding set lacks `SETUID`/`SETGID`, or the
  runner has the UBI `shadow-utils` helpers instead of the libcap-aware build
  (docs/configuration.md, "User-namespace helpers"). The preflight now names
  which; rebuild the runner with `make harness-up` after pulling.

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
