# Local Kubernetes testing (Tekton harness)

[Local development](local-development.md) · [Harness README](../tests/integration/kind/README.md)

## What the harness proves

A disposable Lima k3s (or kind) cluster with Tekton Pipelines, the factory
ServiceAccounts/settings, all factory Tasks and Pipelines, and a TLS-authenticated
fixture registry. `tests/integration/kind/tekton.py run` executes the
`harness-smoke` Pipeline:

| Task | Expectation | Proves |
|---|---|---|
| `checkout` (`harness-source`) | succeeds | source snapshot committed into the run workspace |
| `validate` (`factory-stage`) | succeeds, emits a 64-hex `seal` result | real verify → run → seal on a factory pod as UID 10001 |
| `consume` (`factory-stage`) | succeeds | a downstream task verifies the upstream seal before reading |
| `tamper-detected` (`factory-stage`) | **fails** | a wrong seal digest is rejected in-cluster |

It does **not** run the rootless BuildKit/Podman stages, PaC, Chains, or agents;
those need the production-like node configuration below and real credentials.
Passing the harness does not establish production FIPS compliance, network
isolation, or application qualification.

## Run it

Requirements: Linux or macOS, Lima (`limactl`), kubectl, Skopeo, Git, Python 3.11+,
jq, yq, curl, OpenSSL. Allow access to GitHub releases (Tekton release manifest,
pinned tools), Debian mirrors, and Docker Hub. Start with 6 CPUs, 12 GiB RAM, 30 GiB disk.

```bash
limactl start --name factory-k3s template://k3s
export FACTORY_HARNESS_CLUSTER=factory-k3s
export FACTORY_HARNESS_STATE=.local-factory/tekton-k3s
export FACTORY_HARNESS_REGISTRY_PORT=15446
tests/integration/kind/up.sh
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" run
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" log
```

The Tekton version comes from `tools/versions.lock.yaml`; override the manifest
with `TEKTON_PIPELINE_RELEASE_URL` for a mirrored copy.

To refresh the source snapshot or Tekton definitions without rebuilding images,
rerun `FACTORY_HARNESS_STATE=... KUBECONFIG=.../kubeconfig tests/integration/kind/deploy.sh`.
The snapshot must fit the 1 MiB ConfigMap limit.

## Running the rootless build stages locally

The production Tasks need what the earlier harness established for nested
rootless containers:

- kubelet `userNamespaces.idsPerPod: 262144` so the runner's
  `factory:100000:65536` subordinate range fits (`up.sh` configures kind this way);
- `hostUsers: false` pods, `procMount: Unmasked` on the run step, and a crun
  RuntimeClass on local clusters where runc fails sandbox sysfs mounting with user
  namespaces (`probe-crun.py` provisions and probes it in rootful mode);
- Debian mapping helpers with explicit SETUID/SETGID file capabilities and no
  default Podman ping-group sysctl (the harness runner image handles both).

Apply these through `taskRunSpecs[].podTemplate` (`runtimeClassName`) and a
harness-only copy of the build Task before attempting the full image pipeline
in the harness. Production clusters with native user-namespace support need none
of the RuntimeClass workarounds.

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
FACTORY_HARNESS_STATE=.local-factory/tekton-k3s tests/integration/kind/down.sh
limactl stop factory-k3s
```

Only the recorded harness cluster is deleted; state stays on disk and is ignored by git.
