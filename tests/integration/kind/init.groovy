import jenkins.model.Jenkins
import hudson.security.HudsonPrivateSecurityRealm
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import org.csanchez.jenkins.plugins.kubernetes.KubernetesCloud
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition

def j = Jenkins.get()
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount('review', System.getenv('HARNESS_PASSWORD'))
j.setSecurityRealm(realm)
def auth = new FullControlOnceLoggedInAuthorizationStrategy()
auth.setAllowAnonymousRead(false)
j.setAuthorizationStrategy(auth)
j.setNumExecutors(0)
def cloud = new KubernetesCloud('kind')
cloud.setServerUrl('https://kubernetes.default.svc')
cloud.setNamespace('factory-harness')
cloud.setJenkinsUrl('http://jenkins.factory-harness.svc.cluster.local:8080/')
cloud.setJenkinsTunnel('jenkins.factory-harness.svc.cluster.local:50000')
cloud.setContainerCapStr('4')
j.clouds.replace(cloud)
// Preserve the runtime-selected definition on controller restarts. Source
// updates are applied explicitly by jenkins.py refresh.
if (!j.getItem('factory-harness')) {
    def job = j.createProject(WorkflowJob, 'factory-harness')
    job.setDefinition(new CpsFlowDefinition(new File('/harness/Pipeline.groovy').text, true))
    job.save()
}
j.save()
