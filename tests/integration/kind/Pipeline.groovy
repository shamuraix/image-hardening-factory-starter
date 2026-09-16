def podYaml = '''
apiVersion: v1
kind: Pod
spec:
  automountServiceAccountToken: false
  hostUsers: false
  # FACTORY_RUNTIME_CLASS
  securityContext:
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
  containers:
    - name: jnlp
      workingDir: /tmp/jenkins-agent
      env:
        - {name: JENKINS_AGENT_WORKDIR, value: /tmp/jenkins-agent}
        - {name: HOME, value: /tmp/jenkins-agent}
    - name: factory
      image: localhost/factory-review-runner:review
      imagePullPolicy: Never
      workingDir: /tmp/jenkins-agent
      command: [sleep]
      args: [99d]
      securityContext:
        privileged: false
        procMount: Unmasked
        allowPrivilegeEscalation: true
        seccompProfile: {type: Unconfined}
        appArmorProfile: {type: Unconfined}
      resources:
        requests: {cpu: 250m, memory: 512Mi}
        limits: {cpu: "2", memory: 2Gi}
      volumeMounts:
        - {name: source, mountPath: /source, readOnly: true}
        - {name: tmp, mountPath: /tmp}
  volumes:
    - name: source
      configMap: {name: factory-source}
    - name: tmp
      emptyDir: {sizeLimit: 8Gi}
'''

timeout(time: 30, unit: 'MINUTES') {
  podTemplate(cloud: 'kind', namespace: 'factory-harness', yaml: podYaml) {
    node(POD_LABEL) {
      container('factory') {
        sh 'tar -xzf /source/source.tar.gz -C .'
        withEnv(['PYTHONPATH=.', 'FACTORY_BUILD_ID=kind-review', 'FACTORY_BUILDKIT_NO_PROCESS_SANDBOX=true']) {
          stage('catalog and behavioral tests') {
            sh 'python3 -m factory.cli validate --catalog catalog/images; env -u FACTORY_BUILD_ID -u FACTORY_BUILDKIT_NO_PROCESS_SANDBOX python3 -m unittest discover -s tests/unit -q; opa test policies/rego'
          }
          stage('rootless BuildKit') {
            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
              sh 'scripts/runtime_preflight.sh smoke/preflight && tests/integration/kind/build-smoke.sh'
            }
          }
          stage('registry and signed evidence') {
            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: false) {
              sh 'tests/integration/kind/registry-smoke.sh'
            }
          }
          stage('artifact handoff') {
            sh 'mkdir -p smoke/handoff; sha256sum factory/pipeline.py > smoke/handoff/checksum.txt'
            stash(name: 'handoff', includes: 'smoke/handoff/**')
          }
        }
        archiveArtifacts(artifacts: 'smoke/**/*.json,smoke/**/*.txt,smoke/**/*.log', allowEmptyArchive: true)
      }
    }
  }
  podTemplate(cloud: 'kind', namespace: 'factory-harness', yaml: podYaml) {
    node(POD_LABEL) {
      container('factory') {
        stage('verify handoff in fresh pod') {
          sh 'tar -xzf /source/source.tar.gz -C .'
          unstash('handoff')
          sh 'sha256sum -c smoke/handoff/checksum.txt'
        }
      }
    }
  }
}
