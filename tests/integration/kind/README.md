# Local Jenkins / Kubernetes / registry harness

## Quick harness usage

This disposable harness validates repository workflows in real Kubernetes pods
using synthetic fixtures.

### Start

```bash
limactl start --name factory-k3s template://k3s
export FACTORY_HARNESS_CLUSTER=factory-k3s
export FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s
export FACTORY_HARNESS_JENKINS_PORT=18083
export FACTORY_HARNESS_REGISTRY_PORT=15446
tests/integration/kind/up.sh
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s run
```

### Check and collect

```bash
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s status
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s log
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s collect
```

### Stop

```bash
FACTORY_HARNESS_STATE=.local-factory/proc-fixed-k3s tests/integration/kind/down.sh
limactl stop factory-k3s
```

---

## Advanced harness reference

### Scope

The harness validates script orchestration, build/evidence handoff, import,
signing, and promotion mechanics with synthetic fixtures. It is **not** a
production certification path.

### Runtime and host prerequisites

- Linux or macOS host, Lima (`limactl`), kubectl/Skopeo/curl/OpenSSL/Python/jq
- cgroup v2 and pod user-namespace support
- rootful cluster mode for the default tested topology

### Important differences from production

- Uses disposable synthetic images/evidence
- Uses test-only credentials and ephemeral storage
- Does not prove production repository IAM, policy, or network posture
- Does not replace full environment acceptance testing

### Refreshing source bundle in running harness

```bash
git ls-files --cached --others --exclude-standard -z |
  tar --null -T - -czf .local-factory/proc-fixed-k3s/source.tar.gz
kubectl --kubeconfig .local-factory/proc-fixed-k3s/kubeconfig -n factory-harness \
  create configmap factory-source \
  --from-file=source.tar.gz=.local-factory/proc-fixed-k3s/source.tar.gz \
  --dry-run=client -o yaml |
  kubectl --kubeconfig .local-factory/proc-fixed-k3s/kubeconfig apply \
    --server-side --field-manager=factory-harness --force-conflicts -f -
python3 tests/integration/kind/jenkins.py --state .local-factory/proc-fixed-k3s refresh
```

### Additional checks

```bash
python3 tests/integration/kind/jenkins-stage-checks.py --state .local-factory/proc-fixed-k3s
```
