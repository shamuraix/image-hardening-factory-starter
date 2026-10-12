# Testing the factory on Rancher Desktop

[Project overview](../README.md) · [Operations](operations.md) · [Configuration](configuration.md) · [Scope](scope.md)

This applies [Running the full pipeline on a local cluster](operations.md#running-the-full-pipeline-on-a-local-cluster)
to Rancher Desktop on a work laptop. Only the cluster is local: the runs use the
dev GitLab project and dev Artifactory repositories
(`deploy/environments/dev`), with the `local-cluster` component (no
NetworkPolicies, agents off). This is not a laptop harness: there is no
`make` target for it, and nothing here changes what production runs.

The Rancher Desktop settings below come from its documentation and have not
been run as a whole by this change. Steps marked **Check** confirm each one
before you go on.

## 0. Before you start

- VPN access to `gitlab.cicd.dc` and `artifactory.cicd.dc`, from both the
  laptop and the Rancher Desktop VM.
- The Zscaler root CA exported as a PEM file (for example `~/zscaler-root.pem`).
- An **amd64** laptop if you can. The catalog builds `linux/amd64` only, and on
  Apple Silicon the VM and its node are arm64. BuildKit there either fails or
  emulates, so build results don't stand for production.
- Production nodes run containerd 1.7.27 on Kubernetes 1.28. Match the
  Kubernetes minor version locally.

## 1. Configure Rancher Desktop

1. **Preferences > Kubernetes**: enable Kubernetes and pick a `v1.28.x` version.
2. **Preferences > Container Engine**: `containerd`.
3. **Preferences > Virtual Machine**: give it enough memory and CPU for the
   Jira/Confluence builds. Raise it if build pods are `OOMKilled`.
4. Enable ValidatingAdmissionPolicy at `v1beta1`. On 1.28 it is off by
   default, and `deploy/base/admission-policy.yaml` fails to apply without it.
   - **macOS** (and Linux): in
     `~/Library/Application Support/rancher-desktop/lima/_config/override.yaml`
     (Linux: the same `lima/_config/override.yaml` under Rancher Desktop's data
     directory) set:
     ```yaml
     env:
       K3S_EXEC: >-
         --kube-apiserver-arg=feature-gates=ValidatingAdmissionPolicy=true
         --kube-apiserver-arg=runtime-config=admissionregistration.k8s.io/v1beta1=true
     ```
   - **Windows**: add `%LOCALAPPDATA%\rancher-desktop\provisioning\k3s-vap.start`
     with Unix line endings. Provisioning scripts run before k3s starts, and k3s
     reads `/etc/rancher/k3s/config.yaml`:
     ```sh
     #!/bin/sh
     mkdir -p /etc/rancher/k3s
     cat > /etc/rancher/k3s/config.yaml <<'EOF'
     kube-apiserver-arg:
       - feature-gates=ValidatingAdmissionPolicy=true
       - runtime-config=admissionregistration.k8s.io/v1beta1=true
     EOF
     ```
   - Quit and restart Rancher Desktop. A factory reset deletes these files.
5. **Check**:
   ```bash
   kubectl version                       # server v1.28.x
   kubectl get nodes -o wide             # CONTAINER-RUNTIME column
   kubectl api-resources --api-group=admissionregistration.k8s.io | grep validatingadmissionpolic
   kubectl get storageclass              # a (default) class, local-path on k3s
   rdctl shell cat /proc/sys/user/max_user_namespaces   # must be > 0
   ```
   The build step sets AppArmor to `unconfined` through the pod annotation. The
   kubelet accepts that on a node without AppArmor, so the VM doesn't need it.
   `scripts/runtime_preflight.sh` records the node facts in
   `evidence/runtime/runtime.txt` on every build.

## 2. Build the runner images

The runner pods reach the internet (Iron Bank, scanner databases, the CISA KEV
feed) through Zscaler. Bake the Zscaler root into the runner images for this
environment:

```bash
FACTORY_CA_BUNDLE=~/zscaler-root.pem make toolchain
REG=artifactory.cicd.dc/techops-cicd-esd-hip-docker-dev-local/factory
nerdctl build -f toolchain/Containerfile.factory-runner \
  --build-arg BASE_REF=registry.access.redhat.com/ubi9/ubi-minimal \
  -t "$REG/factory-runner:dev" .
nerdctl push "$REG/factory-runner:dev"        # note the pushed digest
nerdctl build -f toolchain/Containerfile.factory-intake-runner \
  --build-arg FACTORY_RUNNER_REF="$REG/factory-runner@sha256:<digest>" \
  -t "$REG/factory-intake-runner:dev" .
nerdctl push "$REG/factory-intake-runner:dev"
```

The agent image is optional while agents are off. Sign each image by digest with
cosign, then put the digests in
`deploy/environments/dev/manifest/environment.yaml` (`RUNNER_IMAGE`,
`INTAKE_RUNNER_IMAGE`, `AGENT_IMAGE`). These images trust Zscaler, so keep them
out of any other environment's manifest.

**Check**: the runner build stops if skopeo or the CA setup don't match
(`toolchain/Containerfile.factory-runner`). A clean build is the check.

## 3. Install Tekton at the pinned versions

```bash
kubectl apply -f https://infra.tekton.dev/tekton-releases/pipeline/previous/v1.15.0/release.yaml
kubectl apply -f https://github.com/tektoncd/pipelines-as-code/releases/download/v0.51.0/release.k8s.yaml
kubectl apply -f https://infra.tekton.dev/tekton-releases/chains/previous/v0.28.0/release.yaml
kubectl apply -k deploy/environments/dev/chains
```

**Check**: `for ns in tekton-pipelines pipelines-as-code tekton-chains; do kubectl get pods -n $ns; done`
shows every pod `Running`. An `ImagePullBackOff` with an `x509` error means the VM
doesn't trust Zscaler for registry pulls. Fix that in Rancher Desktop's VM
trust store, not in the factory.

## 4. Point the in-cluster callers at the controller

`FACTORY_PAC_CONTROLLER_URL` is used only from inside the cluster: the
schedule CronJobs and the `factory-trigger-dependents` task call
`scripts/tekton/trigger_incoming.sh`. Locally, set it to the controller
Service:

```yaml
FACTORY_PAC_CONTROLLER_URL: http://pipelines-as-code-controller.pipelines-as-code.svc.cluster.local:8080
```

**Check** the Service name and port with `kubectl get svc -n pipelines-as-code`.

## 5. Secrets and factory objects

Create the Secrets in `deploy/secrets.example.yaml` with dev values, scoped to
the `-dev-local` repositories. `factory-ai-gateway` can be left out. Then:

```bash
kubectl apply -k deploy/environments/dev
```

**Check**: the apply creates the `ValidatingAdmissionPolicy` and its binding
without errors, and `kubectl get networkpolicy -A` lists none in the factory
namespace.

## 6. Connect GitLab

Merge-request and push runs need GitLab to reach the controller, and the
internal GitLab can't reach a laptop directly. Pick one:

- **Self-hosted gosmee inside the network** (recommended): the server runs
  where GitLab can reach it, and the client runs on the laptop and forwards to
  `kubectl port-forward -n pipelines-as-code svc/pipelines-as-code-controller 8080`.
  Webhook payloads carry repository details, so don't relay them through the
  public `hook.pipelinesascode.com`.
- **A routable VPN address** for the laptop, if your network allows it.

Then create the webhook (`tkn pac create repo`, or Settings > Webhooks) for
**Merge request, Push, Comments, and Tag push** with the `factory-gitlab-webhook`
secret.

Without a webhook, you can still run the push-class runs through the incoming
endpoint (step 7). Merge-request runs need the webhook.

## 7. Run the sequence

With a port-forward to the controller running:

```bash
export FACTORY_PAC_CONTROLLER_URL=http://localhost:8080
export FACTORY_PAC_REPOSITORY=image-hardening-factory
export FACTORY_PAC_INCOMING_SECRET=$(kubectl get secret -n image-factory factory-pac-incoming -o jsonpath='{.data.secret}' | base64 -d)
scripts/tekton/trigger_incoming.sh security-data-on-schedule
scripts/tekton/trigger_incoming.sh intake-on-schedule
```

Then follow [Bootstrap sequence](operations.md#bootstrap-sequence), steps 5 to
9: confirm `security-data/current.json` and the signed locks, open a trivial
merge request, merge it, and release `ubi10-minimal` to commercial before the
apps ([base-images.md](base-images.md#bootstrapping-the-ubi-10-base)).

Watch runs with `tkn pr list -n image-factory` and `tkn pr logs -f`.

Suspend the schedules if you don't want nightly runs on the laptop:

```bash
kubectl get cronjob -n image-factory -o name |
  xargs -I{} kubectl patch -n image-factory {} -p '{"spec":{"suspend":true}}'
```

## What a pass shows

A local run proves the trust boundaries (per-task ServiceAccounts, per-step
secrets, the admission policy, sealing), digest continuity through quarantine,
signing, and promotion, and the release-request flow. It does not prove network
isolation (the NetworkPolicies are removed), production scale, or amd64
behaviour on an arm64 laptop.

## Cleanup

```bash
kubectl delete -k deploy/environments/dev
```

Alternatively, reset Kubernetes in Rancher Desktop. Delete the dev images and
the `factory/` paths in the `-dev-local` Artifactory repositories if the run
published anything you don't want to keep.
