# Local Tekton / Kubernetes harness

Disposable kind cluster that runs the real factory Tekton Tasks — validate,
rootless build, SBOM, rootless tests, and seal checks — on real pods with the
real runner image, against a snapshot of this repository. See
[docs/local-kubernetes-testing.md](../../../docs/local-kubernetes-testing.md)
for scope, host requirements, and troubleshooting.

```bash
make harness-up                       # kind cluster, runner image, Tekton, factory objects
make harness-run IMAGE=ubi9-minimal   # build, SBOM, test; asserts per-task outcomes
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review status
python3 tests/integration/kind/tekton.py --state .local-factory/kind-review log
make harness-down
```

| File | Purpose |
|---|---|
| `up.sh` / `deploy.sh` / `down.sh` | create the kind cluster and runner image; deploy Tekton and the factory objects; delete the cluster |
| `harness-pipeline.yaml` | `harness-source` Task (source snapshot stand-in for checkout) and the `harness-build` Pipeline |
| `harness-prepare.sh` | stand-in for the prepare stage: Repo One clone, overlays, public UBI base, unsigned `localDevelopment` lock |
| `tekton.py` | create the PipelineRun with production pod settings and assert per-task outcomes |
| `probe-crun.py`, `node-diagnostics.sh` | rootful mode only: install and probe a crun RuntimeClass; collect node diagnostics |

The runner image is `toolchain/Containerfile.factory-runner` built on the public
`registry.access.redhat.com/ubi9/ubi-minimal`; its tools come from
`toolchain/build-dist.sh`.
