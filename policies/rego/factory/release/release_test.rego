package factory.release

import rego.v1

passing_input := {
  "image": "jira-lts",
  "imageDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "sbomValid": true,
  "compliancePassed": true,
  "testsPassed": true,
  "findings": [],
  "database": {"generatedAt": "2026-09-15T00:00:00Z"},
  "assessment": {
    "scanner": "trivy-grype-syft-osv-scanner",
    "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "assessmentPassed": true,
  },
  "scannerBackend": "delegated-scanners",
  "evaluatedAt": "2026-09-15T12:00:00Z",
  "policy": {
    "maximumDatabaseAgeHours": 72,
    "block": {"critical": true, "fixableHigh": true, "newHigh": true, "knownExploited": true},
  },
}

test_delegated_scanners_pass_when_required_evidence_exists if {
  result := decision with input as passing_input
  result.allow
}

test_unknown_backend_denied if {
  not allow with input as object.union(passing_input, {"scannerBackend": "other"})
}

test_fixable_high_outside_archive_warns_without_deny if {
  candidate := object.union(passing_input, {
    "findings": [{"id": "CVE-1", "component": "pkg:rpm/openssl@1", "severity": "HIGH", "fixAvailable": true, "inApplicationArchive": false}],
  })
  result := decision with input as candidate
  result.allow
  count(result.warn) == 1
}

test_fixable_high_in_archive_denies if {
  candidate := object.union(passing_input, {
    "findings": [{"id": "CVE-2", "component": "pkg:maven/demo@1", "severity": "HIGH", "fixAvailable": true, "inApplicationArchive": true}],
  })
  not allow with input as candidate
}
