package factory.release

import rego.v1

passing_input := {
	"image": "jira-lts",
	"imageDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
	"sbomValid": true,
	"compliancePassed": true,
	"testsPassed": true,
	"findings": [{"id": "legacy-critical", "severity": "Critical"}],
	"database": {"available": false},
	"fcs": {
		"scanner": "crowdstrike-fcs",
		"digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		"exitCode": 0,
		"sbomExitCode": 0,
		"reportValid": true,
		"sbomValid": true,
		"assessmentPassed": true,
	},
}

test_informational_legacy_findings_do_not_deny if {
	result := decision with input as passing_input
	result.allow
}

test_fcs_policy_denial_blocks_release if {
	candidate := object.union(passing_input, {
		"fcs": object.union(passing_input.fcs, {
			"exitCode": 2,
			"assessmentPassed": false,
		}),
	})
	result := decision with input as candidate
	not result.allow
}

test_fcs_digest_mismatch_blocks_release if {
	candidate := object.union(passing_input, {
		"fcs": object.union(passing_input.fcs, {"digest": "sha256:def"}),
	})
	result := decision with input as candidate
	not result.allow
}

test_missing_fcs_status_blocks_release if {
	candidate := object.remove(passing_input, {"fcs"})
	result := decision with input as candidate
	not result.allow
}

test_missing_exit_and_assessment_cannot_pass if {
	candidate := object.union(object.remove(passing_input, {"fcs"}), {"fcs": object.remove(passing_input.fcs, {"exitCode", "assessmentPassed"})})
	not allow with input as candidate
}

test_truthy_strings_cannot_pass if {
	candidate := object.union(passing_input, {"testsPassed": "false"})
	not allow with input as candidate
}

test_missing_candidate_digest_cannot_pass if {
	candidate := object.remove(passing_input, {"imageDigest"})
	not allow with input as candidate
}

grype_input := object.union(passing_input, {
	"scannerBackend": "grype",
	"assessment": {"scanner": "syft-grype", "assessmentPassed": true, "digest": passing_input.imageDigest},
	"evaluatedAt": "2026-09-15T12:00:00Z",
	"database": {"built": "2026-09-15T00:00:00Z"},
	"findings": [],
	"policy": {"maximumDatabaseAgeHours": 72, "block": {
		"critical": true, "fixableHigh": true, "newHigh": true, "knownExploited": true,
	}},
})

test_grype_without_fcs_passes if {
	allow with input as object.remove(grype_input, {"fcs"})
}

test_grype_stale_future_and_missing_database_denied if {
	every db in [{"built": "2026-09-01T00:00:00Z"}, {"built": "2026-09-16T00:00:00Z"}, {}] {
		not allow with input as object.union(object.remove(grype_input, {"database"}), {"database": db})
	}
}

test_grype_thresholds_deny if {
	every finding in [
		{"id": "critical", "severity": "CRITICAL"},
		{"id": "fixable", "severity": "HIGH", "fixAvailable": true},
		{"id": "new", "severity": "HIGH", "new": true},
		{"id": "kev", "severity": "LOW", "knownExploited": true},
		{"id": "unknown", "severity": "UNKNOWN"},
	] {
		not allow with input as object.union(grype_input, {"findings": [finding]})
	}
}

test_grype_missing_status_and_digest_mismatch_denied if {
	every assessment in [{}, {"scanner": "syft-grype", "assessmentPassed": true, "digest": "sha256:other"}] {
		not allow with input as object.union(object.remove(grype_input, {"assessment"}), {"assessment": assessment})
	}
}

test_unknown_backend_denied if {
	not allow with input as object.union(grype_input, {"scannerBackend": "other"})
}
