"""Unit tests for VA-04 - Compliance Agent.

The LLM client is mocked so these run without network access.
behaviour and output_parser are exercised directly with synthetic LLM
payloads; the agent ``run()`` flow is exercised end-to-end with the
LLM collaborator replaced.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from agents.va04_compliance import behaviour, output_parser
from core.exceptions import OutputParseError, PipelineStopError


# ---------------------------------------------------------------- fixtures

_BEHAVIOUR_CFG = {
    "on_empty_requirements":   "stop_and_report",
    "on_missing_policy_rules": "stop_and_report",
    "on_low_confidence":       "flag_and_continue",
    "min_requirements":        1,
    "audit_statuses": [
        "compliant",
        "non_compliant",
        "conditionally_compliant",
        "not_applicable",
    ],
    "blocking_statuses": ["non_compliant"],
}

_INPUTS_CFG = {
    "release_artefacts": {
        "required":  True,
        "type":      "array",
        "min_items": 1,
        "on_fail":   "stop_and_report",
    },
    "policy_rules": {
        "required":  True,
        "type":      "array",
        "min_items": 1,
        "on_fail":   "stop_and_report",
    },
    "project_context": {
        "required": True,
        "type":     "object",
        "on_fail":  "stop_and_report",
    },
    "business_case": {
        "required": True,
        "type":     "string",
        "on_fail":  "stop_and_report",
    },
    "constraints": {
        "required":   False,
        "type":       "object",
        "on_missing": "proceed_without",
    },
}


def _artefact(artefact_id: str = "SVC-001") -> Dict[str, Any]:
    return {"artefact_id": artefact_id, "type": "code_change", "description": "Test artefact"}


def _rule(rule_id: str = "POL-TLS-01") -> Dict[str, Any]:
    return {"rule_id": rule_id, "name": "Transport Security", "requirement": "TLS 1.2+"}


def _check(
    *,
    check_id:     str = "CA-001",
    check_name:   str = "TLS Check",
    artefact_ref: str = "SVC-001",
    policy_ref:   str = "POL-TLS-01",
    status:       str = "compliant",
    evidence:     str = "TLS 1.2 enforced via gateway config.",
    description:  str = "Service meets TLS requirement.",
) -> Dict[str, Any]:
    return {
        "check_id":     check_id,
        "check_name":   check_name,
        "artefact_ref": artefact_ref,
        "policy_ref":   policy_ref,
        "status":       status,
        "evidence":     evidence,
        "description":  description,
        "req_id_refs":  [],
    }


def _valid_payload() -> Dict[str, Any]:
    return {
        "release_artefacts": [_artefact()],
        "policy_rules":      [_rule()],
        "project_context":   {"squad": "payments", "domain": "fintech"},
        "business_case":     "Multi-currency payment support",
    }


def _llm_response(checks: List[Dict[str, Any]]) -> str:
    signoff = {
        "overall_status":      "all_compliant",
        "signoff_authority":   "VA-04",
        "total_checks":        len(checks),
        "compliant_count":     sum(1 for c in checks if c["status"] == "compliant"),
        "non_compliant_count": sum(1 for c in checks if c["status"] == "non_compliant"),
        "recommendation":      "proceed",
    }
    return json.dumps({
        "compliance_audit_trail": checks,
        "policy_signoff": signoff,
    })


# ---------------------------------------------------------------- behaviour — validate_inputs

class TestValidateInputs:
    def test_passes_with_valid_payload(self):
        behaviour.validate_inputs(_valid_payload(), _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_on_missing_release_artefacts(self):
        payload = _valid_payload()
        payload["release_artefacts"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "release_artefacts" in exc_info.value.message.lower()

    def test_stops_on_missing_policy_rules(self):
        payload = _valid_payload()
        payload["policy_rules"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "policy_rules" in exc_info.value.message.lower()

    def test_stops_on_missing_project_context(self):
        payload = _valid_payload()
        payload["project_context"] = None
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "project_context" in exc_info.value.message

    def test_stops_on_missing_business_case(self):
        payload = _valid_payload()
        payload["business_case"] = ""
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "business_case" in exc_info.value.message

    def test_proceeds_without_optional_constraints(self):
        payload = _valid_payload()
        payload.pop("constraints", None)
        behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)  # must not raise


# ---------------------------------------------------------------- behaviour — renumber_checks

class TestRenumberChecks:
    def test_sequential_ids(self):
        checks = [
            _check(check_id="WRONG"),
            _check(check_id="CA-099", check_name="Second check"),
            _check(check_id="",       check_name="Third check"),
        ]
        renumbered = behaviour.renumber_checks(checks)
        assert [c["check_id"] for c in renumbered] == ["CA-001", "CA-002", "CA-003"]


# ---------------------------------------------------------------- behaviour — coerce_artefact_refs

class TestCoerceArtefactRefs:
    def test_valid_refs_kept(self):
        checks   = [_check(artefact_ref="SVC-001"), _check(artefact_ref="DB-001", check_name="DB check")]
        artefacts = [_artefact("SVC-001"), _artefact("DB-001")]
        coerced = behaviour.coerce_artefact_refs(checks, artefacts)
        assert coerced[0]["artefact_ref"] == "SVC-001"
        assert coerced[1]["artefact_ref"] == "DB-001"

    def test_unknown_ref_preserved_as_is(self):
        checks    = [_check(artefact_ref="UNKNOWN-999")]
        artefacts = [_artefact("SVC-001")]
        coerced   = behaviour.coerce_artefact_refs(checks, artefacts)
        # Unknown ref stays; outer validator can flag it
        assert coerced[0]["artefact_ref"] == "UNKNOWN-999"


# ---------------------------------------------------------------- behaviour — coerce_policy_refs

class TestCoercePolicyRefs:
    def test_valid_refs_kept(self):
        checks = [_check(policy_ref="POL-TLS-01"), _check(policy_ref="POL-PII-02", check_name="PII")]
        rules  = [_rule("POL-TLS-01"), _rule("POL-PII-02")]
        coerced = behaviour.coerce_policy_refs(checks, rules)
        assert coerced[0]["policy_ref"] == "POL-TLS-01"
        assert coerced[1]["policy_ref"] == "POL-PII-02"


# ---------------------------------------------------------------- behaviour — enforce_audit_statuses

class TestEnforceAuditStatuses:
    def test_unknown_status_replaced_with_non_compliant(self):
        checks  = [_check(status="unknown_status"), _check(status="compliant", check_name="c2")]
        allowed = _BEHAVIOUR_CFG["audit_statuses"]
        result  = behaviour.enforce_audit_statuses(checks, allowed)
        assert result[0]["status"] == "non_compliant"
        assert result[1]["status"] == "compliant"

    def test_all_valid_statuses_kept(self):
        statuses = ["compliant", "non_compliant", "conditionally_compliant", "not_applicable"]
        checks   = [_check(status=s, check_name=s) for s in statuses]
        result   = behaviour.enforce_audit_statuses(checks, statuses)
        assert [c["status"] for c in result] == statuses


# ---------------------------------------------------------------- behaviour — compute_signoff

class TestComputeSignoff:
    def test_all_compliant_yields_proceed(self):
        checks = [_check(status="compliant"), _check(status="compliant", check_name="c2")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert signoff["recommendation"] == "proceed"
        assert signoff["overall_status"] == "all_compliant"
        assert signoff["total_checks"] == 2
        assert signoff["compliant_count"] == 2
        assert signoff["non_compliant_count"] == 0
        assert signoff["signoff_authority"] == "VA-04"

    def test_non_compliant_yields_blocked(self):
        checks = [_check(status="compliant"), _check(status="non_compliant", check_name="fail")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert signoff["recommendation"] == "blocked"
        assert signoff["non_compliant_count"] == 1

    def test_conditionally_compliant_yields_remediate(self):
        checks = [_check(status="conditionally_compliant")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert signoff["recommendation"] == "remediate"

    def test_empty_trail_yields_blocked(self):
        signoff = behaviour.compute_signoff([], _BEHAVIOUR_CFG)
        assert signoff["recommendation"] == "blocked"
        assert signoff["total_checks"] == 0

    def test_total_checks_equals_trail_length(self):
        checks = [_check(), _check(check_name="c2"), _check(check_name="c3")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert signoff["total_checks"] == 3

    def test_is_blocking_returns_true_for_blocked(self):
        checks  = [_check(status="non_compliant")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert behaviour.is_blocking(signoff) is True

    def test_is_blocking_returns_false_for_proceed(self):
        checks  = [_check(status="compliant")]
        signoff = behaviour.compute_signoff(checks, _BEHAVIOUR_CFG)
        assert behaviour.is_blocking(signoff) is False


# ---------------------------------------------------------------- output_parser

class TestOutputParser:
    def test_parses_well_formed_json(self):
        raw    = _llm_response([_check()])
        result = output_parser.parse(raw, audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])
        assert len(result["compliance_audit_trail"]) == 1
        assert result["compliance_audit_trail"][0]["status"] == "compliant"

    def test_strips_json_fence(self):
        raw    = f"```json\n{_llm_response([_check()])}\n```"
        result = output_parser.parse(raw, audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])
        assert result["compliance_audit_trail"][0]["check_id"] == "CA-001"

    def test_rejects_unknown_status(self):
        raw = _llm_response([_check(status="pass")])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(raw, audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])
        assert "status" in exc_info.value.message

    def test_rejects_empty_response(self):
        with pytest.raises(OutputParseError):
            output_parser.parse("", audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])

    def test_rejects_invalid_json(self):
        with pytest.raises(OutputParseError):
            output_parser.parse("not valid json at all", audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])

    def test_empty_trail_is_valid(self):
        raw    = json.dumps({"compliance_audit_trail": [], "policy_signoff": None})
        result = output_parser.parse(raw, audit_statuses=_BEHAVIOUR_CFG["audit_statuses"])
        assert result["compliance_audit_trail"] == []

    def test_unknown_recommendation_coerced_to_none(self):
        payload = {
            "compliance_audit_trail": [_check()],
            "policy_signoff": {
                "overall_status":      "all_compliant",
                "signoff_authority":   "VA-04",
                "total_checks":        1,
                "compliant_count":     1,
                "non_compliant_count": 0,
                "recommendation":      "ship_it",  # invalid
            }
        }
        result = output_parser.parse(
            json.dumps(payload),
            audit_statuses=_BEHAVIOUR_CFG["audit_statuses"],
        )
        # coerced to None; behaviour.compute_signoff will recompute
        assert result["policy_signoff"]["recommendation"] is None


# ---------------------------------------------------------------- end-to-end agent run

class TestAgentRun:
    @pytest.mark.asyncio
    async def test_full_flow_proceed(self):
        from agents.va04_compliance import agent

        llm_text = _llm_response([
            _check(check_id="WRONG-001", artefact_ref="SVC-001",   policy_ref="POL-TLS-01", status="compliant"),
            _check(check_id="WRONG-002", artefact_ref="SVC-001",   policy_ref="POL-PII-02", status="compliant", check_name="PII check"),
        ])

        payload = {
            "release_artefacts": [_artefact("SVC-001")],
            "policy_rules":      [_rule("POL-TLS-01"), _rule("POL-PII-02")],
            "project_context":   {"squad": "payments", "domain": "fintech"},
            "business_case":     "Multi-currency support",
        }

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(payload, run_id="test-va04-001")

        assert result["agent_id"] == "VA-04"
        assert result["run_id"]   == "test-va04-001"
        # IDs must be renumbered
        assert [c["check_id"] for c in result["compliance_audit_trail"]] == ["CA-001", "CA-002"]
        # Signoff must be recomputed deterministically
        assert result["policy_signoff"]["recommendation"]      == "proceed"
        assert result["policy_signoff"]["total_checks"]        == 2
        assert result["policy_signoff"]["compliant_count"]     == 2
        assert result["policy_signoff"]["non_compliant_count"] == 0

    @pytest.mark.asyncio
    async def test_non_compliant_yields_blocked_signoff(self):
        from agents.va04_compliance import agent

        llm_text = _llm_response([
            _check(status="compliant"),
            _check(status="non_compliant", check_name="PII fail", evidence="PII field unencrypted."),
        ])

        with patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_valid_payload(), run_id="test-va04-002")

        assert result["policy_signoff"]["recommendation"]      == "blocked"
        assert result["policy_signoff"]["non_compliant_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_artefacts_stops_before_llm(self):
        from agents.va04_compliance import agent

        payload = _valid_payload()
        payload["release_artefacts"] = []

        mock_call = AsyncMock()
        with patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError) as exc_info:
                await agent.run(payload, run_id="test-va04-003")

        mock_call.assert_not_called()
        assert "release_artefacts" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_empty_policy_rules_stops_before_llm(self):
        from agents.va04_compliance import agent

        payload = _valid_payload()
        payload["policy_rules"] = []

        mock_call = AsyncMock()
        with patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError) as exc_info:
                await agent.run(payload, run_id="test-va04-004")

        mock_call.assert_not_called()
        assert "policy_rules" in exc_info.value.message.lower()
