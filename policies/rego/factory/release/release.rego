package factory.release

import rego.v1

default allow := false

allow if count(deny) == 0

decision := {"allow": allow, "deny": sort([message | deny[message]])}

deny contains "SBOM validation failed" if not input.sbomValid == true
deny contains "compliance evaluation failed" if not input.compliancePassed == true
deny contains "product integration tests failed" if not input.testsPassed == true
deny contains "CrowdStrike FCS scanner identity is invalid" if not input.fcs.scanner == "crowdstrike-fcs"
deny contains "CrowdStrike FCS report validation failed" if not input.fcs.reportValid == true
deny contains "CrowdStrike FCS SBOM validation failed" if not input.fcs.sbomValid == true
deny contains "candidate digest is invalid" if not regex.match("^sha256:[0-9a-f]{64}$", input.imageDigest)
deny contains "CrowdStrike FCS assessed a different or missing image digest" if not input.fcs.digest == input.imageDigest
deny contains "CrowdStrike FCS assessment failed or is incomplete" if not input.fcs.assessmentPassed == true
deny contains "CrowdStrike FCS assessment exit code is invalid" if not input.fcs.exitCode == 0
deny contains "CrowdStrike FCS SBOM exit code is invalid" if not input.fcs.sbomExitCode == 0
