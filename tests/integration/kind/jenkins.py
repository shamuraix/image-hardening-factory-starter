#!/usr/bin/env python3
"""Trigger the local harness without exposing its password in process arguments."""

import argparse
import base64
import http.cookiejar
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from runtime_config import render_pipeline

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run", "status", "log", "refresh", "collect"])
parser.add_argument("--state", type=Path, default=Path(".local-factory/kind-review"))
args = parser.parse_args()
settings_path = args.state / "settings.json"
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
base = f"http://127.0.0.1:{settings.get('jenkinsPort', 18080)}"
password = (args.state / "jenkins-password").read_text().strip()
auth = base64.b64encode(f"review:{password}".encode()).decode()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, data=None, headers=None):
    req = urllib.request.Request(
        base + path, data=data, headers={"Authorization": f"Basic {auth}", **(headers or {})}
    )
    return opener.open(req, timeout=15).read()


if args.command == "refresh":
    config = ET.fromstring(request("/job/factory-harness/config.xml"))
    pipeline = Path("tests/integration/kind/Pipeline.groovy").read_text()
    pipeline = render_pipeline(pipeline, args.state)
    config.find("./definition/script").text = pipeline
    crumb = json.loads(request("/crumbIssuer/api/json"))
    request(
        "/job/factory-harness/config.xml",
        data=ET.tostring(config),
        headers={crumb["crumbRequestField"]: crumb["crumb"], "Content-Type": "application/xml"},
    )
    print("Updated harness pipeline")
elif args.command == "run":
    crumb = json.loads(request("/crumbIssuer/api/json"))
    request(
        "/job/factory-harness/build", data=b"", headers={crumb["crumbRequestField"]: crumb["crumb"]}
    )
    print("Queued factory-harness")
elif args.command == "status":
    status = json.loads(request("/job/factory-harness/lastBuild/api/json"))
    result = {key: status.get(key) for key in ("number", "building", "result", "url")}
    (args.state / "last-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
elif args.command == "collect":
    status = json.loads(request("/job/factory-harness/lastBuild/api/json"))
    destination = args.state / "results" / str(status["number"])
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "build.json").write_text(json.dumps(status, indent=2))
    build_path = f"/job/factory-harness/{status['number']}"
    (destination / "console.log").write_bytes(request(build_path + "/consoleText"))
    for artifact in status.get("artifacts", []):
        relative = Path(artifact["relativePath"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unexpected artifact path")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(request(build_path + "/artifact/" + relative.as_posix()))
    plugins = request("/pluginManager/api/json?tree=plugins[shortName,version,active]")
    (destination / "plugins.json").write_bytes(plugins)
    print(f"Saved build {status['number']} diagnostics to {destination}")
else:
    log = request("/job/factory-harness/lastBuild/consoleText")
    (args.state / "jenkins-console.log").write_bytes(log)
    print(log.decode(errors="replace")[-16000:])
