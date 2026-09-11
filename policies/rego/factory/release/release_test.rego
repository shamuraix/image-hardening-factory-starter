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
