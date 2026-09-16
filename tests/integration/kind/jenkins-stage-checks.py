#!/usr/bin/env python3
"""Exercise the production stage wrapper in real, disposable Jenkins jobs."""

import argparse
import base64
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from runtime_config import render_pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(".local-factory/kind-review"))
    args = parser.parse_args()
    settings = json.loads((args.state / "settings.json").read_text())
    base = f"http://127.0.0.1:{settings['jenkinsPort']}"
    password = (args.state / "jenkins-password").read_text().strip()
    auth = base64.b64encode(("review:" + password).encode()).decode()
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def request(path, data=None, headers=None):
        return client.open(
            urllib.request.Request(
                base + path,
                data=data,
                headers={"Authorization": "Basic " + auth, **(headers or {})},
            ),
            timeout=20,
        ).read()

    crumb = json.loads(request("/crumbIssuer/api/json"))
    headers = {crumb["crumbRequestField"]: crumb["crumb"]}
    source = Path("Jenkinsfile").read_text()
    wrapper = source[source.index("def runFactoryStage(") : source.index("def isProtectedBranch()")]
    yaml = Path("tests/integration/kind/Pipeline.groovy").read_text().split("timeout(time:", 1)[0]
    yaml = render_pipeline(yaml, args.state)
    helpers = """
def artifactName(image, stageName) { "fixture-${image}-${stageName}" }
def inFactoryPod(template, runner, image, environment, body) { body() }
def withStageCredentials(credentials, body) { body() }
"""
    results = {}
    for case in ["gate", "failure", "abort"]:
        job_name = "factory-stage-" + case
        command = "mkdir -p evidence; echo retained > evidence/result.txt; "
        command += "echo READY_TO_ABORT; sleep 120" if case == "abort" else "exit 4"
        stage_name = "gate" if case == "gate" else "fixture"
        pipeline = (
            "import groovy.json.JsonSlurperClassic\n"
            + helpers
            + wrapper
            + yaml
            + """
podTemplate(cloud: 'kind', namespace: 'factory-harness', yaml: podYaml) {
  node(POD_LABEL) { container('factory') {
"""
            + f"runFactoryStage('fixture', '{stage_name}', '', '', [], 'evidence/**', '{command}', [], [], true)\n"
            + """
  } }
}
"""
        )
        # failure case exercises propagation through the blocking branch.
        if case == "failure":
            pipeline = pipeline.replace("[], [], true)", "[], [], false)")
        config = ET.Element("flow-definition")
        definition = ET.SubElement(
            config, "definition", {"class": "org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition"}
        )
        ET.SubElement(definition, "script").text = pipeline
        ET.SubElement(definition, "sandbox").text = "true"
        jobs = json.loads(request("/api/json?tree=jobs[name]"))["jobs"]
        endpoint = (
            "/job/" + job_name + "/config.xml"
            if any(j["name"] == job_name for j in jobs)
            else "/createItem?name=" + job_name
        )
        request(endpoint, ET.tostring(config), {**headers, "Content-Type": "application/xml"})
        previous = json.loads(request("/job/" + job_name + "/api/json")).get("nextBuildNumber", 1)
        request("/job/" + job_name + "/build", b"", headers)
        prefix = f"/job/{job_name}/{previous}"
        deadline = time.monotonic() + 240
        stopped = False
        while time.monotonic() < deadline:
            try:
                status = json.loads(request(prefix + "/api/json"))
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                time.sleep(2)
                continue
            if case == "abort" and not stopped:
                log = request(prefix + "/consoleText").decode()
                if "READY_TO_ABORT" in log:
                    request(prefix + "/stop", b"", headers)
                    stopped = True
            if not status["building"]:
                break
            time.sleep(2)
        else:
            raise TimeoutError(job_name)
        output = args.state / "stage-checks" / case
        output.mkdir(parents=True, exist_ok=True)
        (output / "build.json").write_text(json.dumps(status, indent=2))
        (output / "console.log").write_bytes(request(prefix + "/consoleText"))
        expected = "ABORTED" if case == "abort" else "FAILURE"
        assert status["result"] == expected, (case, status["result"])
        if case != "abort":
            assert any(a["relativePath"] == "evidence/result.txt" for a in status["artifacts"]), (
                case
            )
        results[case] = status["result"]
        print(
            f"{case}: {status['result']}, artifacts retained={bool(status['artifacts'])}",
            flush=True,
        )
    (args.state / "stage-checks/results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
