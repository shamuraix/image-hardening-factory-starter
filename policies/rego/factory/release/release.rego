package factory.release

import rego.v1

default allow := false

allow if count(deny) == 0

backend := object.get(input, "scannerBackend", "fcs")

decision := {"allow": allow, "deny": sort([message | deny[message]]), "scannerBackend": backend, "imageDigest": input.imageDigest}

deny contains "SBOM validation failed" if not input.sbomValid == true
deny contains "compliance evaluation failed" if not input.compliancePassed == true
deny contains "product integration tests failed" if not input.testsPassed == true

deny contains "CrowdStrike FCS scanner identity is invalid" if {
	backend == "fcs"
	not input.fcs.scanner == "crowdstrike-fcs"
}

deny contains "CrowdStrike FCS report validation failed" if {
	backend == "fcs"
	not input.fcs.reportValid == true
}

deny contains "CrowdStrike FCS SBOM validation failed" if {
	backend == "fcs"
	not input.fcs.sbomValid == true
}

deny contains "candidate digest is invalid" if not regex.match("^sha256:[0-9a-f]{64}$", input.imageDigest)

deny contains "CrowdStrike FCS assessed a different or missing image digest" if {
	backend == "fcs"
	not input.fcs.digest == input.imageDigest
}

deny contains "CrowdStrike FCS assessment failed or is incomplete" if {
	backend == "fcs"
	not input.fcs.assessmentPassed == true
}

deny contains "CrowdStrike FCS assessment exit code is invalid" if {
	backend == "fcs"
	not input.fcs.exitCode == 0
}

deny contains "CrowdStrike FCS SBOM exit code is invalid" if {
	backend == "fcs"
	not input.fcs.sbomExitCode == 0
}

deny contains "unknown scanner backend" if not backend in {"fcs", "grype"}

deny contains "Grype assessment is incomplete or invalid" if {
	backend == "grype"
	not input.assessment.assessmentPassed == true
}

deny contains "Grype scanner identity is invalid" if {
	backend == "grype"
	not input.assessment.scanner == "syft-grype"
}

deny contains "Grype assessed a different image digest" if {
	backend == "grype"
	not input.assessment.digest == input.imageDigest
}

deny contains "Grype database is stale, missing or from the future" if {
	backend == "grype"
	not database_fresh
}

database_fresh if {
	age := time.parse_rfc3339_ns(input.evaluatedAt) - time.parse_rfc3339_ns(input.database.built)
	age >= 0
	age <= input.policy.maximumDatabaseAgeHours * 3600000000000
}

deny contains sprintf("Grype vulnerability blocked: %s", [f.id]) if {
	backend == "grype"
	some f in input.findings
	blocked(f)
}

blocked(f) if f.severity == "UNKNOWN"

blocked(f) if {
	input.policy.block.critical
	f.severity == "CRITICAL"
}

blocked(f) if {
	input.policy.block.fixableHigh
	f.severity == "HIGH"
	f.fixAvailable
}

blocked(f) if {
	input.policy.block.newHigh
	f.severity == "HIGH"
	f.new
}

blocked(f) if {
	input.policy.block.knownExploited
	f.knownExploited
}
