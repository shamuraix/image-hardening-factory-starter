# Local Kubernetes testing (Tekton harness)

[Local development](local-development.md) · [Harness README](../tests/integration/kind/README.md)

## What the harness proves

`tests/integration/kind/up.sh` creates a disposable
[kind](https://kind.sigs.k8s.io/) cluster (Kubernetes in containers, run by
Podman or Docker on your machine), installs the pinned Tekton Pipelines release,
applies the factory ServiceAccounts, settings, Tasks and Pipelines, builds a
harness runner image, and deploys a TLS-authenticated fixture registry.
`tests/integration/kind/tekton.py run` then executes the `harness-smoke`
Pipeline:

| Task | Expectation | Proves |
|---|---|---|
| `checkout` (`harness-source`) | succeeds | the reviewed source snapshot is committed into the run workspace |
| `validate` (`factory-stage`) | succeeds, emits a 64-hex `seal` result | the real verify → run → seal steps work on a factory pod as UID 10001 |
| `consume` (`factory-stage`) | succeeds | a downstream task verifies the upstream seal before reading |
| `tamper-detected` (`factory-stage`) | **fails** | a wrong seal digest is rejected in-cluster |

Applying every Task and Pipeline also proves the Tekton webhook accepts them.

It does **not** run the rootless BuildKit or Podman stages, Pipelines-as-Code,
Tekton Chains, or the agents; those need the node configuration below and real
credentials. Passing the harness does not establish production FIPS compliance,
network isolation, or application qualification.

## Run it

Requirements: Linux or macOS with Podman (default) or Docker, `kind` at the
version pinned in `tools/versions.lock.yaml` (`up.sh` checks), kubectl, Skopeo,
Git, Python 3.11+ with the repository installed (`pip install -e '.[dev]'`), jq,
yq, curl, OpenSSL. Allow access to GitHub releases (the Tekton release manifest
and pinned node tools), Debian mirrors, and Docker Hub. Give the container
runtime at least 6 CPUs, 12 GiB RAM, and 30 GiB disk.

```bash
export FACTORY_HARNESS_STATE=.local-factory/kind-review   # default
export FACTORY_HARNESS_REGISTRY_PORT=15443               # default
tests/integration/kind/up.sh
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" run
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" log
```

`KIND_EXPERIMENTAL_PROVIDER=docker` selects Docker. With rootless Podman,
`up.sh` first checks that the user has enough subordinate IDs for
user-namespaced pods (111 × 262144); if not, it tells you to use
`FACTORY_HARNESS_ROOTFUL=true` (rootful Podman through `sudo`) or Docker.

The Tekton version comes from `tools/versions.lock.yaml`; override the manifest
with `TEKTON_PIPELINE_RELEASE_URL` for a mirrored copy.

To refresh the source snapshot or Tekton definitions without recreating the
cluster, rerun
`FACTORY_HARNESS_STATE=... KUBECONFIG=$FACTORY_HARNESS_STATE/kubeconfig tests/integration/kind/deploy.sh`.
The snapshot must fit the 1 MiB ConfigMap limit.

## Running the rootless build stages locally

The production build and test Tasks need a node that can run nested rootless
containers. The harness prepares some of this but does not yet run those
stages:

- kubelet `userNamespaces.idsPerPod: 262144`, so the runner's
  `factory:100000:65536` subordinate range fits (`up.sh` creates kind nodes
  this way; see [Kubernetes user namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/));
- `hostUsers: false` on the pod and `procMount: Unmasked` on the run step (the
  generated PipelineRuns set both for the build and test stages);
- on local clusters where runc cannot mount sysfs in a user-namespaced sandbox,
  a crun RuntimeClass. In rootful mode `probe-crun.py` installs crun on the
  node, probes it, and records the working RuntimeClass name in the state
  directory;
- Debian mapping helpers with explicit SETUID/SETGID file capabilities and no
  default Podman ping-group sysctl (the harness runner image handles both).

To exercise the build stage in the harness, add a harness pipeline that runs
`factory-rootless-build` with `runtimeClassName` from the recorded RuntimeClass
in `taskRunSpecs[].podTemplate`. Production clusters with native user-namespace
support need no RuntimeClass workaround.

## Runtime troubleshooting

```bash
kubectl --kubeconfig "$FACTORY_HARNESS_STATE/kubeconfig" -n factory-harness get events --sort-by=.lastTimestamp
kubectl --kubeconfig "$FACTORY_HARNESS_STATE/kubeconfig" -n factory-harness get taskruns
sudo -v && tests/integration/kind/node-diagnostics.sh
```

Background from the earlier investigation, still relevant to the build stages:

- Debian mapping helpers needed explicit SETUID/SETGID file capabilities
  ([Podman discussion](https://github.com/containers/podman/discussions/19931)).
- Nested Podman required unmasked proc and no default ping-group sysctl
  ([Kubernetes security context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/),
  [Podman issue](https://github.com/containers/podman/issues/13194)).
- runc failed sandbox sysfs mounting with user namespaces in local clusters; crun
  worked once its system D-Bus prerequisite was supplied.

## Teardown

```bash
FACTORY_HARNESS_STATE=.local-factory/kind-review tests/integration/kind/down.sh
```

Only the recorded harness cluster is deleted; state stays on disk and is ignored
by git. State directories may hold test credentials; never commit them.
