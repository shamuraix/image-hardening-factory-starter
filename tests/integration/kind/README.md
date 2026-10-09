# Local Tekton / Kubernetes / registry harness

Disposable cluster that runs the real factory Tekton Tasks on real pods with
synthetic inputs. See [docs/local-kubernetes-testing.md](../../../docs/local-kubernetes-testing.md)
for scope, prerequisites, and runtime notes.

```bash
limactl start --name factory-k3s template://k3s
export FACTORY_HARNESS_CLUSTER=factory-k3s
export FACTORY_HARNESS_STATE=.local-factory/tekton-k3s
export FACTORY_HARNESS_REGISTRY_PORT=15446
tests/integration/kind/up.sh                                   # cluster, images, Tekton, factory objects
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" run
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" status
python3 tests/integration/kind/tekton.py --state "$FACTORY_HARNESS_STATE" log
FACTORY_HARNESS_STATE="$FACTORY_HARNESS_STATE" tests/integration/kind/down.sh
```

| File | Purpose |
|---|---|
| `up.sh` / `deploy.sh` / `down.sh` | create, deploy into, and delete the harness cluster |
| `harness-pipeline.yaml` | `harness-source` Task and `harness-smoke` Pipeline |
| `tekton.py` | create the smoke PipelineRun and assert per-task outcomes |
| `services.yaml` | namespace and TLS fixture registry |
| `Containerfile.runner`, `download-tools.*`, `configure-uidmap.py` | harness runner image |
| `probe-crun.py`, `node-diagnostics.sh` | rootless runtime probes and diagnostics |

Registry credentials (`oidc/harness`) are public test-only values.
