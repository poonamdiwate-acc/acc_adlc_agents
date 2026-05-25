"""Unit tests for VA-05 — QA Assurance Auditor Agent.

The LLM client is mocked so these run without network access. VA-05 has no
git reader — all inputs arrive in the payload dict (the router merges them
from the shared folder before calling the handler).

Coverage:
* ``behaviour.validate_inputs``     — pre-flight input rules (FR-1.1, FR-2.1)
* ``behaviour.review_audit_trail``  — deterministic chronology / duplicate /
                                      signature / checkpoint checks (FR-1.2–1.4)
* ``behaviour.reconcile_dispositions`` — FR-2.5 / NFR-2 enforcement
* ``behaviour.renumber_findings``   — sequential FIND-### ids (AC-07)
* ``behaviour.build_assurance_summary`` — overall_assurance classification
* ``behaviour.build_assurance_signoff`` — immutability + blocked-state nulling
* ``output_parser.parse``           — JSON / fence / enum validation
* ``agent.run``                     — end-to-end with mocked LLM
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from agents.va05_qa_assurance_auditor import behaviour, output_parser
from core.exceptions import OutputParseError, PipelineStopError


# ---------------------------------------------------------------- fixtures

_BEHAVIOUR_CFG = {
    "on_empty_audit_trail": "stop_and_report",
    "on_missing_evidence": "escalate_never_accept",
    "on_audit_gap": "block_signoff",
    "on_sla_breach": "block_signoff",
    "min_audit_entries": 1,
    "disposition_values": ["Accepted", "Rejected", "Escalated"],
    "audit_finding_types": [
        "missing_record",
        "duplicate_record",
        "out_of_sequence",
        "signature_invalid",
        "checkpoint_not_covered",
        "untraceable_entry",
    ],
    "severity_levels": ["critical", "high", "medium", "low"],
    "blocking_severities": ["critical", "high"],
    "overall_assurance_values": ["clean", "qualified", "blocked"],
    "recommendation_values": [
        "proceed",
        "proceed_with_escalation",
        "hold_for_remediation",
    ],
}

_INPUTS_CFG = {
    "audit_trail": {
        "required": True,
        "type": "array",
        "min_items": 1,
        "on_fail": "stop_and_report",
    },
    "exception_flags": {
        "required": False,
        "type": "array",
        "default": [],
        "on_missing": "proceed_with_empty",
    },
    "project_context": {
        "required": True,
        "type": "object",
        "on_fail": "stop_and_report",
        "expected_fields": ["cycle_id"],
        "soft_fields": ["domain", "market", "project_name", "sla_seconds"],
    },
    "business_case": {
        "required": False,
        "type": "string",
        "default": "",
        "on_missing": "proceed_without",
    },
    "checkpoint_expectations": {
        "required": False,
        "type": "object",
        "on_missing": "proceed_without",
    },
}


def _audit_entry(
    *,
    entry_id: str,
    timestamp: str,
    control_ref: str = "CTRL-SEC-001",
    signature: str = "sig:abc",
    actor: str = "compliance-agent",
) -> Dict[str, Any]:
    return {
        "entry_id": entry_id,
        "timestamp": timestamp,
        "control_ref": control_ref,
        "actor": actor,
        "signature": signature,
    }


def _clean_payload() -> Dict[str, Any]:
    """A valid, complete VA-05 payload (clean cycle)."""
    return {
        "audit_trail": [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z",
                         control_ref="CTRL-SEC-001"),
            _audit_entry(entry_id="AUD-002", timestamp="2026-05-12T09:00:00Z",
                         control_ref="CTRL-SEC-002"),
        ],
        "exception_flags": [
            {
                "exception_id": "EXC-001",
                "severity": "medium",
                "source_control": "CTRL-SEC-001",
                "evidence_refs": ["EVID-001"],
            },
        ],
        "project_context": {
            "cycle_id": "CYCLE-2026-05",
            "domain": "payments",
            "market": "EU",
            "project_name": "Test Cycle",
            "sla_seconds": 600,
        },
        "business_case": "Clean cycle smoke test.",
    }


def _llm_payload(
    *,
    exception_log: List[Dict[str, Any]],
    audit_findings: List[Dict[str, Any]] | None = None,
) -> str:
    return json.dumps({
        "exception_log": exception_log,
        "audit_findings": audit_findings or [],
        "assurance_signoff": None,
        "assurance_summary": None,
    })


# ---------------------------------------------------------------- behaviour: validate_inputs

class TestValidateInputs:
    def test_passes_with_clean_payload(self):
        behaviour.validate_inputs(_clean_payload(), _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_when_audit_trail_missing(self):
        payload = _clean_payload()
        del payload["audit_trail"]
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "audit_trail" in exc_info.value.message

    def test_stops_when_audit_trail_empty(self):
        payload = _clean_payload()
        payload["audit_trail"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        # min_items rule fires first
        assert exc_info.value.detail.get("min_items") == 1

    def test_business_case_optional_when_blank(self):
        """business_case is optional — empty/missing must not raise."""
        payload = _clean_payload()
        payload["business_case"] = ""
        behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_when_project_context_missing(self):
        payload = _clean_payload()
        del payload["project_context"]
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "project_context" in exc_info.value.message

    def test_stops_when_cycle_id_missing(self):
        payload = _clean_payload()
        payload["project_context"] = {"domain": "payments"}
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "cycle_id" in exc_info.value.message

    def test_exception_flags_can_be_empty_array(self):
        """exception_flags is optional — empty array is valid."""
        payload = _clean_payload()
        payload["exception_flags"] = []
        behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_exception_flags_can_be_missing(self):
        """exception_flags is now optional — fully absent must not raise."""
        payload = _clean_payload()
        del payload["exception_flags"]
        behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_checkpoint_expectations_optional(self):
        payload = _clean_payload()
        # absent — must not raise
        assert "checkpoint_expectations" not in payload
        behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)


# ---------------------------------------------------------------- behaviour: apply_input_defaults

class TestApplyInputDefaults:
    def test_complete_payload_reports_status_complete(self):
        payload = _clean_payload()
        result = behaviour.apply_input_defaults(payload, _INPUTS_CFG)
        assert result["status"] == "complete"
        assert result["missing"] == []
        assert result["degraded"] == []

    def test_missing_business_case_is_defaulted_and_tracked(self):
        payload = _clean_payload()
        del payload["business_case"]
        result = behaviour.apply_input_defaults(payload, _INPUTS_CFG)
        assert payload["business_case"] == ""
        assert "business_case" in result["missing"]
        assert result["status"] == "partial"

    def test_missing_exception_flags_is_defaulted_to_empty(self):
        payload = _clean_payload()
        del payload["exception_flags"]
        result = behaviour.apply_input_defaults(payload, _INPUTS_CFG)
        assert payload["exception_flags"] == []
        assert "exception_flags" in result["missing"]
        assert result["status"] == "partial"

    def test_project_context_soft_fields_are_tracked_as_degraded(self):
        payload = _clean_payload()
        # cycle_id present but soft fields absent
        payload["project_context"] = {"cycle_id": "CYCLE-X"}
        result = behaviour.apply_input_defaults(payload, _INPUTS_CFG)
        assert result["status"] == "partial"
        # all four soft fields should be reported degraded
        assert "project_context.domain" in result["degraded"]
        assert "project_context.market" in result["degraded"]
        assert "project_context.project_name" in result["degraded"]
        assert "project_context.sla_seconds" in result["degraded"]

    def test_required_inputs_untouched(self):
        """apply_input_defaults must NEVER mutate required inputs."""
        payload = _clean_payload()
        original_trail = list(payload["audit_trail"])
        behaviour.apply_input_defaults(payload, _INPUTS_CFG)
        assert payload["audit_trail"] == original_trail


# ---------------------------------------------------------------- behaviour: review_audit_trail

class TestReviewAuditTrail:
    def test_clean_trail_produces_no_findings(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z"),
            _audit_entry(entry_id="AUD-002", timestamp="2026-05-12T09:00:00Z"),
        ]
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=None)
        assert findings == []

    def test_detects_duplicate_entry_id(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z"),
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T09:00:00Z"),
        ]
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=None)
        assert any(f["finding_type"] == "duplicate_record" for f in findings)

    def test_detects_out_of_sequence(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T10:00:00Z"),
            _audit_entry(entry_id="AUD-002", timestamp="2026-05-12T09:00:00Z"),
        ]
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=None)
        types = [f["finding_type"] for f in findings]
        assert "out_of_sequence" in types

    def test_detects_missing_signature_as_critical(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z"),
            {
                "entry_id": "AUD-002",
                "timestamp": "2026-05-12T09:00:00Z",
                "control_ref": "CTRL-SEC-002",
                "actor": "compliance-agent",
                "signature": "",
            },
        ]
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=None)
        sig_findings = [f for f in findings if f["finding_type"] == "signature_invalid"]
        assert len(sig_findings) == 1
        assert sig_findings[0]["severity"] == "critical"

    def test_checkpoint_coverage_gap_raises_finding(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z",
                         control_ref="CTRL-SEC-001"),
        ]
        expectations = {
            "checkpoints": [
                {"control_ref": "CTRL-SEC-001"},
                {"control_ref": "CTRL-SEC-002"},
            ]
        }
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=expectations)
        uncovered = [f for f in findings if f["finding_type"] == "checkpoint_not_covered"]
        assert len(uncovered) == 1
        assert uncovered[0]["control_ref"] == "CTRL-SEC-002"
        assert uncovered[0]["severity"] == "high"

    def test_checkpoint_coverage_skipped_when_no_expectations(self):
        trail = [
            _audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z"),
        ]
        findings = behaviour.review_audit_trail(trail, checkpoint_expectations=None)
        assert findings == []

    def test_empty_trail_returns_empty(self):
        """Defensive — validate_inputs would block first, but the function
        itself must not crash on an empty input."""
        assert behaviour.review_audit_trail([], checkpoint_expectations=None) == []


# ---------------------------------------------------------------- behaviour: reconcile_dispositions

class TestReconcileDispositions:
    def test_accepted_without_evidence_is_demoted_to_escalated(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Accepted",
                "evidence_refs": [],
                "rationale": "looks fine",
                "audit_trail_ref": "AUD-001",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["disposition"] == "Escalated"
        assert result[0]["escalation_target"] == "validate_orchestrator"
        assert "[VA-05 enforcement]" in result[0]["rationale"]

    def test_accepted_with_evidence_is_preserved(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Accepted",
                "evidence_refs": ["EVID-001"],
                "rationale": "within tolerance",
                "audit_trail_ref": "AUD-001",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["disposition"] == "Accepted"
        assert result[0]["escalation_target"] is None

    def test_unresolved_audit_ref_is_nulled(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Accepted",
                "evidence_refs": ["EVID-001"],
                "rationale": "evidence ok",
                "audit_trail_ref": "AUD-DOES-NOT-EXIST",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["audit_trail_ref"] is None
        # Accepted with bad ref escalates per NFR-2
        assert result[0]["disposition"] == "Escalated"

    def test_default_reviewer_and_timestamp_stamped(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Rejected",
                "evidence_refs": ["EVID-001"],
                "rationale": "breaches policy",
                "audit_trail_ref": "AUD-001",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["reviewer"] == "VA-05"
        assert isinstance(result[0]["reviewed_at"], str) and result[0]["reviewed_at"]

    def test_non_escalated_clears_escalation_target(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Rejected",
                "evidence_refs": ["EVID-001"],
                "rationale": "out of tolerance",
                "audit_trail_ref": "AUD-001",
                "escalation_target": "should_be_wiped",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["escalation_target"] is None

    def test_escalated_without_target_gets_default(self):
        log = [
            {
                "exception_id": "EXC-001",
                "disposition": "Escalated",
                "evidence_refs": ["EVID-001"],
                "rationale": "exceeds VA-05 authority",
                "audit_trail_ref": "AUD-001",
            }
        ]
        audit_trail = [_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")]
        result = behaviour.reconcile_dispositions(log, audit_trail, _BEHAVIOUR_CFG)
        assert result[0]["escalation_target"] == "validate_orchestrator"


# ---------------------------------------------------------------- behaviour: renumber_findings

class TestRenumberFindings:
    def test_assigns_sequential_ids(self):
        findings = [
            {"finding_id": "FIND-099", "finding_type": "duplicate_record"},
            {"finding_type": "out_of_sequence"},
            {"finding_id": "garbage", "finding_type": "missing_record"},
        ]
        result = behaviour.renumber_findings(findings)
        assert [f["finding_id"] for f in result] == ["FIND-001", "FIND-002", "FIND-003"]

    def test_empty_list_returns_empty(self):
        assert behaviour.renumber_findings([]) == []


# ---------------------------------------------------------------- behaviour: build_assurance_summary

class TestBuildAssuranceSummary:
    def test_clean_when_no_findings_and_all_accepted(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[{"disposition": "Accepted"}, {"disposition": "Accepted"}],
            audit_findings=[],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["overall_assurance"] == "clean"
        assert summary["recommendation"] == "proceed"
        assert summary["dispositions_by_type"]["Accepted"] == 2

    def test_blocked_when_high_severity_finding(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[],
            audit_findings=[{"severity": "high", "finding_type": "missing_record"}],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["overall_assurance"] == "blocked"
        assert summary["recommendation"] == "hold_for_remediation"

    def test_blocked_when_critical_finding(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[],
            audit_findings=[{"severity": "critical", "finding_type": "signature_invalid"}],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["overall_assurance"] == "blocked"

    def test_qualified_when_escalation_only(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[{"disposition": "Escalated"}],
            audit_findings=[],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["overall_assurance"] == "qualified"
        assert summary["recommendation"] == "proceed_with_escalation"

    def test_qualified_when_only_non_blocking_findings(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[{"disposition": "Accepted"}],
            audit_findings=[{"severity": "medium", "finding_type": "out_of_sequence"}],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["overall_assurance"] == "qualified"

    def test_partial_input_downgrades_clean_to_qualified(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[{"disposition": "Accepted"}],
            audit_findings=[],
            behaviour_cfg=_BEHAVIOUR_CFG,
            input_completeness={"status": "partial", "missing": ["business_case"], "degraded": []},
        )
        assert summary["overall_assurance"] == "qualified"
        assert summary["recommendation"] == "proceed_with_escalation"
        assert summary["input_completeness"]["status"] == "partial"
        assert "business_case" in summary["input_completeness"]["missing"]

    def test_partial_input_does_not_soften_blocked(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[],
            audit_findings=[{"severity": "critical", "finding_type": "signature_invalid"}],
            behaviour_cfg=_BEHAVIOUR_CFG,
            input_completeness={"status": "partial", "missing": ["business_case"], "degraded": []},
        )
        assert summary["overall_assurance"] == "blocked"

    def test_complete_input_preserves_clean(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id="AUD-001", timestamp="2026-05-12T08:00:00Z")],
            exception_log=[{"disposition": "Accepted"}],
            audit_findings=[],
            behaviour_cfg=_BEHAVIOUR_CFG,
            input_completeness={"status": "complete", "missing": [], "degraded": []},
        )
        assert summary["overall_assurance"] == "clean"

    def test_counts_are_complete(self):
        summary = behaviour.build_assurance_summary(
            audit_trail=[_audit_entry(entry_id=f"AUD-{i}", timestamp="2026-05-12T08:00:00Z")
                         for i in range(3)],
            exception_log=[
                {"disposition": "Accepted"},
                {"disposition": "Rejected"},
                {"disposition": "Escalated"},
            ],
            audit_findings=[{"severity": "low", "finding_type": "duplicate_record"}],
            behaviour_cfg=_BEHAVIOUR_CFG,
        )
        assert summary["audit_entries_reviewed"] == 3
        assert summary["audit_findings_raised"] == 1
        assert summary["exceptions_reviewed"] == 3
        assert summary["dispositions_by_type"] == {
            "Accepted": 1, "Rejected": 1, "Escalated": 1,
        }


# ---------------------------------------------------------------- behaviour: build_assurance_signoff

class TestBuildAssuranceSignoff:
    def test_signoff_is_none_when_blocked(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-X"},
            audit_findings=[{"severity": "critical", "finding_id": "FIND-001"}],
            exception_log=[],
            assurance_summary={"overall_assurance": "blocked"},
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations=None,
        )
        assert signoff is None

    def test_signoff_issued_when_clean(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-2026-05"},
            audit_findings=[],
            exception_log=[],
            assurance_summary={
                "overall_assurance": "clean",
                "exceptions_reviewed": 0,
                "dispositions_by_type": {"Accepted": 0, "Rejected": 0, "Escalated": 0},
            },
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations=None,
        )
        assert signoff is not None
        assert signoff["immutable"] is True
        assert signoff["cycle_id"] == "CYCLE-2026-05"
        assert signoff["issued_by"] == "VA-05"
        assert signoff["audit_integrity"] == "verified"
        assert signoff["checkpoint_coverage"] == "skipped"
        assert signoff["signoff_id"].startswith("SIGN-")
        assert signoff["blocking_findings"] == []

    def test_signoff_marks_checkpoint_partial_when_gap(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-X"},
            audit_findings=[
                {"finding_id": "FIND-001", "finding_type": "checkpoint_not_covered",
                 "severity": "high"},
            ],
            exception_log=[],
            assurance_summary={
                "overall_assurance": "qualified",
                "exceptions_reviewed": 0,
                "dispositions_by_type": {"Accepted": 0, "Rejected": 0, "Escalated": 0},
            },
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations={"checkpoints": [{"control_ref": "CTRL-X"}]},
        )
        # A "high" finding is blocking → signoff should actually NOT be issued
        # by the classifier — but build_assurance_signoff trusts the summary
        # we pass. Confirm fields when summary says qualified.
        assert signoff is not None
        assert signoff["checkpoint_coverage"] == "partial"
        assert signoff["blocking_findings"] == ["FIND-001"]

    def test_signoff_records_inputs_completeness_when_partial(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-X"},
            audit_findings=[],
            exception_log=[],
            assurance_summary={
                "overall_assurance": "qualified",
                "exceptions_reviewed": 0,
                "dispositions_by_type": {"Accepted": 0, "Rejected": 0, "Escalated": 0},
            },
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations=None,
            input_completeness={"status": "partial", "missing": ["business_case"], "degraded": []},
        )
        assert signoff is not None
        assert signoff["inputs_completeness"] == "partial"

    def test_signoff_omits_inputs_completeness_when_arg_absent(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-X"},
            audit_findings=[],
            exception_log=[],
            assurance_summary={
                "overall_assurance": "clean",
                "exceptions_reviewed": 0,
                "dispositions_by_type": {"Accepted": 0, "Rejected": 0, "Escalated": 0},
            },
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations=None,
        )
        assert "inputs_completeness" not in signoff

    def test_signoff_complete_coverage_when_no_gap_finding(self):
        signoff = behaviour.build_assurance_signoff(
            project_context={"cycle_id": "CYCLE-X"},
            audit_findings=[],
            exception_log=[],
            assurance_summary={
                "overall_assurance": "clean",
                "exceptions_reviewed": 0,
                "dispositions_by_type": {"Accepted": 0, "Rejected": 0, "Escalated": 0},
            },
            behaviour_cfg=_BEHAVIOUR_CFG,
            checkpoint_expectations={"checkpoints": [{"control_ref": "CTRL-X"}]},
        )
        assert signoff["checkpoint_coverage"] == "complete"


# ---------------------------------------------------------------- output parser

class TestOutputParser:
    def test_parses_well_formed_json(self):
        raw = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "source_control": "CTRL-SEC-001",
                "severity": "medium",
                "disposition": "Accepted",
                "rationale": "within tolerance",
                "evidence_refs": ["EVID-001"],
                "audit_trail_ref": "AUD-001",
            }
        ])
        result = output_parser.parse(
            raw,
            allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
        )
        assert len(result["exception_log"]) == 1
        assert result["exception_log"][0]["disposition"] == "Accepted"
        assert result["audit_findings"] == []

    def test_strips_json_fence(self):
        raw = "```json\n" + _llm_payload(exception_log=[]) + "\n```"
        result = output_parser.parse(
            raw,
            allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
        )
        assert result["exception_log"] == []

    def test_drops_llm_supplied_signoff_and_summary(self):
        """assurance_signoff and assurance_summary from the LLM must be
        ignored — behaviour rebuilds them deterministically."""
        raw = json.dumps({
            "exception_log": [],
            "audit_findings": [],
            "assurance_signoff": {"immutable": False, "signoff_id": "EVIL"},
            "assurance_summary": {"overall_assurance": "clean"},
        })
        result = output_parser.parse(
            raw,
            allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
        )
        assert "assurance_signoff" not in result
        assert "assurance_summary" not in result

    def test_rejects_unknown_disposition(self):
        raw = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "severity": "medium",
                "disposition": "Maybe",
                "rationale": "X",
                "evidence_refs": [],
            }
        ])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )
        assert "disposition" in exc_info.value.message

    def test_rejects_unknown_severity(self):
        raw = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "severity": "catastrophic",
                "disposition": "Accepted",
                "rationale": "X",
                "evidence_refs": ["EVID-001"],
            }
        ])
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )

    def test_rejects_unknown_finding_type(self):
        raw = _llm_payload(
            exception_log=[],
            audit_findings=[
                {
                    "finding_type": "made_up_type",
                    "severity": "high",
                    "description": "X",
                    "recommendation": "Y",
                }
            ],
        )
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )
        assert "finding_type" in exc_info.value.message

    def test_rejects_invalid_json(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "not valid json",
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )

    def test_rejects_empty_response(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "",
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )

    def test_empty_log_is_valid(self):
        result = output_parser.parse(
            _llm_payload(exception_log=[]),
            allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
        )
        assert result["exception_log"] == []
        assert result["audit_findings"] == []

    def test_rejects_missing_required_disposition_fields(self):
        """Pydantic schema requires rationale, disposition, severity, exception_id."""
        raw = json.dumps({
            "exception_log": [{"exception_id": "EXC-001"}],
            "audit_findings": [],
        })
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_dispositions=_BEHAVIOUR_CFG["disposition_values"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
                allowed_finding_types=_BEHAVIOUR_CFG["audit_finding_types"],
            )


# ---------------------------------------------------------------- end-to-end

class TestAgentRun:
    """End-to-end ``run()`` flow with the LLM client replaced by a stub."""

    @pytest.mark.asyncio
    async def test_clean_cycle_produces_signoff(self):
        from agents.va05_qa_assurance_auditor import agent

        llm_text = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "source_control": "CTRL-SEC-001",
                "severity": "medium",
                "disposition": "Accepted",
                "rationale": "Within documented tolerance.",
                "evidence_refs": ["EVID-001"],
                "audit_trail_ref": "AUD-001",
            }
        ])

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_clean_payload(), run_id="test-run-001")

        assert result["agent_id"] == "VA-05"
        assert result["run_id"] == "test-run-001"
        assert result["audit_findings"] == []
        assert result["assurance_summary"]["overall_assurance"] == "clean"
        assert result["assurance_summary"]["recommendation"] == "proceed"
        assert result["assurance_signoff"] is not None
        assert result["assurance_signoff"]["immutable"] is True
        assert result["exception_log"][0]["disposition"] == "Accepted"

    @pytest.mark.asyncio
    async def test_empty_audit_trail_stops_before_llm(self):
        from agents.va05_qa_assurance_auditor import agent

        payload = _clean_payload()
        payload["audit_trail"] = []
        mock_call = AsyncMock()
        with patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError):
                await agent.run(payload, run_id="test-run-002")
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_checkpoint_gap_blocks_signoff(self):
        """Deterministic finding (high severity checkpoint gap) must block
        sign-off even if the LLM returns clean Accepted dispositions."""
        from agents.va05_qa_assurance_auditor import agent

        payload = _clean_payload()
        # Expectation references a control that has no audit entry
        payload["checkpoint_expectations"] = {
            "checkpoints": [
                {"control_ref": "CTRL-SEC-001"},
                {"control_ref": "CTRL-NOT-IN-TRAIL"},
            ]
        }
        llm_text = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "source_control": "CTRL-SEC-001",
                "severity": "medium",
                "disposition": "Accepted",
                "rationale": "Within tolerance.",
                "evidence_refs": ["EVID-001"],
                "audit_trail_ref": "AUD-001",
            }
        ])

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(payload, run_id="test-run-003")

        assert result["assurance_summary"]["overall_assurance"] == "blocked"
        assert result["assurance_summary"]["recommendation"] == "hold_for_remediation"
        assert result["assurance_signoff"] is None
        types = [f["finding_type"] for f in result["audit_findings"]]
        assert "checkpoint_not_covered" in types

    @pytest.mark.asyncio
    async def test_partial_input_downgrades_to_qualified_with_completeness_block(self):
        """Missing optional inputs (business_case) → sign-off still issued
        but downgraded to qualified, with input_completeness recorded."""
        from agents.va05_qa_assurance_auditor import agent

        payload = _clean_payload()
        del payload["business_case"]
        llm_text = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "source_control": "CTRL-SEC-001",
                "severity": "medium",
                "disposition": "Accepted",
                "rationale": "Within tolerance.",
                "evidence_refs": ["EVID-001"],
                "audit_trail_ref": "AUD-001",
            }
        ])

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(payload, run_id="test-run-partial")

        assert result["assurance_summary"]["overall_assurance"] == "qualified"
        assert result["assurance_summary"]["recommendation"] == "proceed_with_escalation"
        completeness = result["assurance_summary"]["input_completeness"]
        assert completeness["status"] == "partial"
        assert "business_case" in completeness["missing"]
        assert result["assurance_signoff"] is not None
        assert result["assurance_signoff"]["inputs_completeness"] == "partial"

    @pytest.mark.asyncio
    async def test_accepted_without_evidence_is_force_escalated(self):
        """FR-2.5 — LLM cannot close an exception with no evidence."""
        from agents.va05_qa_assurance_auditor import agent

        llm_text = _llm_payload(exception_log=[
            {
                "exception_id": "EXC-001",
                "source_control": "CTRL-SEC-001",
                "severity": "medium",
                "disposition": "Accepted",
                "rationale": "Looks fine to me.",
                "evidence_refs": [],
                "audit_trail_ref": "AUD-001",
            }
        ])

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_clean_payload(), run_id="test-run-004")

        item = result["exception_log"][0]
        assert item["disposition"] == "Escalated"
        assert item["escalation_target"] == "validate_orchestrator"
        assert "[VA-05 enforcement]" in item["rationale"]
        # Escalated → qualified (not blocked) since no audit findings
        assert result["assurance_summary"]["overall_assurance"] == "qualified"
        assert result["assurance_signoff"] is not None
