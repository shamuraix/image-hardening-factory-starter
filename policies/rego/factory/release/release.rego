package factory.release

import rego.v1

default allow := false

allow if count(deny) == 0

backend := object.get(input, "scannerBackend", "delegated-scanners")

decision := {
  "allow": allow,
  "deny": sort([message | deny[message]]),
  "warn": sort([message | warn[message]]),
  "scannerBackend": backend,
  "imageDigest": input.imageDigest,
}

deny contains "SBOM validation failed" if not input.sbomValid == true
deny contains "compliance evaluation failed" if not input.compliancePassed == true
deny contains "product integration tests failed" if not input.testsPassed == true
deny contains "candidate digest is invalid" if not regex.match("^sha256:[0-9a-f]{64}$", input.imageDigest)

deny contains "unknown scanner backend" if not backend == "delegated-scanners"

deny contains "delegated scanner assessment is incomplete or invalid" if {
  backend == "delegated-scanners"
  not input.assessment.assessmentPassed == true
}

deny contains "delegated scanner identity is invalid" if {
  backend == "delegated-scanners"
  not input.assessment.scanner == "trivy-grype-syft-osv-scanner"
}

deny contains "delegated scanners assessed a different image digest" if {
  backend == "delegated-scanners"
  not input.assessment.digest == input.imageDigest
}

deny contains "scanner database is stale, missing or from the future" if {
  backend == "delegated-scanners"
  not database_fresh
}

database_fresh if {
  age := time.parse_rfc3339_ns(input.evaluatedAt) - time.parse_rfc3339_ns(input.database.generatedAt)
  age >= 0
  age <= input.policy.maximumDatabaseAgeHours * 3600000000000
}

deny contains sprintf("Vulnerability blocked: %s", [f.id]) if {
  backend == "delegated-scanners"
  some f in input.findings
  blocked(f)
  not excluded(f)
  (f.inApplicationArchive == true or not outside_archive_warnable(f))
}

warn contains sprintf("Vulnerability warning: %s (%s)", [f.id, f.component]) if {
  backend == "delegated-scanners"
  some f in input.findings
  outside_archive_warnable(f)
  not excluded(f)
}

outside_archive_warnable(f) if {
  f.inApplicationArchive == false
  f.fixAvailable == true
  f.severity in {"HIGH", "CRITICAL"}
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

excluded(f) if {
  f.fixAvailable == true
  some exception in data.factory.exceptions.approved[input.image]
  exception.id == f.id
  exception.component == f.component
  exception.installedVersion == f.installedVersion
}
