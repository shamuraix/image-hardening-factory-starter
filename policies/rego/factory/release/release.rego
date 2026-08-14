package factory.release

import rego.v1

default allow := false

allow if count(deny) == 0

decision := {"allow": allow, "deny": sort([message | deny[message]])}

deny contains "SBOM validation failed" if not input.sbomValid
deny contains "compliance evaluation failed" if not input.compliancePassed
deny contains "product integration tests failed" if not input.testsPassed
deny contains "CrowdStrike FCS scanner identity is missing" if not input.fcs.scanner
deny contains "CrowdStrike FCS scanner identity is invalid" if input.fcs.scanner != "crowdstrike-fcs"
deny contains "CrowdStrike FCS report validation failed" if not input.fcs.reportValid
deny contains "CrowdStrike FCS SBOM validation failed" if not input.fcs.sbomValid
deny contains "CrowdStrike FCS image digest is missing" if not input.fcs.digest
deny contains "CrowdStrike FCS assessed a different image digest" if input.fcs.digest != input.imageDigest
deny contains sprintf("CrowdStrike FCS image assessment failed or denied with exit code %d", [input.fcs.exitCode]) if not input.fcs.assessmentPassed
