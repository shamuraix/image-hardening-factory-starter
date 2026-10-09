# Local Tekton / Kubernetes / registry harness

Disposable kind cluster that runs the real factory Tekton Tasks on real pods
with a snapshot of this repository as input. See [docs/local-kubernetes-testing.md](../../../docs/local-kubernetes-testing.md)
for scope, prerequisites, and runtime notes.

```bash
tests/integration/kind/up.sh                                   # kind cluster, images, Tekton, factory objects
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review run
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review status
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review log
tests/integration/kind/down.sh
```

The cluster is created with [kind](https://kind.sigs.k8s.io/) on Podman
(default) or Docker (`KIND_EXPERIMENTAL_PROVIDER=docker`). It never uses the
Lima BuildKit VM that local image builds use.

| File | Purpose |
|---|---|
| `up.sh` / `deploy.sh` / `down.sh` | create the kind cluster and runner image; deploy Tekton and the factory objects; delete the cluster |
| `harness-pipeline.yaml` | `harness-source` Task and `harness-smoke` Pipeline |
| `tekton.py` | create the smoke PipelineRun and assert per-task outcomes |
| `services.yaml` | namespace and TLS fixture registry |
| `Containerfile.runner`, `download-tools.*`, `configure-uidmap.py` | harness runner image (Debian, with node tools at the versions in `tools/versions.lock.yaml`) |
| `probe-crun.py`, `node-diagnostics.sh` | rootful mode only: install and probe a crun RuntimeClass; collect node diagnostics |

Registry credentials (`oidc/harness`) are public test-only values.
