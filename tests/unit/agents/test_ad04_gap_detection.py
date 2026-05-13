"""Unit tests for AD-04 — Gap Detection Agent.

The LLM client and the git reader are both mocked so these run without
network access. Behaviour and output_parser are exercised directly with
synthetic LLM payloads; the agent ``run()`` flow is exercised end-to-end
with both collaborators replaced.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from agents.ad04_gap_detection import behaviour, output_parser
from core.exceptions import OutputParseError, PipelineStopError


# ---------------------------------------------------------------- fixtures

_BEHAVIOUR_CFG = {
    "on_empty_requirements": "stop_and_report",
    "min_requirements": 1,
    "gap_categories": [
        "missing_acceptance_criteria",
        "ambiguous_language",
        "implied_but_unstated",
        "conflicting_requirements",
        "out_of_scope_not_flagged",
        "non_measurable_nfr",
        "missing_actor",
        "missing_business_value",
    ],
    "severity_levels": ["critical", "high", "medium", "low"],
    "blocking_severities": ["critical", "high"],
}

_INPUTS_CFG = {
    "structured_requirements": {
        "required": True,
        "type": "array",
        "min_items": 1,
        "on_fail": "stop_and_report",
    },
    "business_case": {
        "required": True,
        "type": "string",
        "on_fail": "stop_and_report",
    },
    "project_context": {
        "required": True,
        "type": "object",
        "on_fail": "stop_and_report",
    },
    "scope_boundaries": {
        "required": False,
        "type": "object",
        "on_missing": "proceed_without",
    },
}


def _payload_with_reqs() -> Dict[str, Any]:
    """Payload as if git inputs already merged — used by behaviour tests."""
    return {
        "structured_requirements": [
            {"req_id": "REQ-001", "title": "Login", "description": "User can log in"},
            {"req_id": "REQ-002", "title": "Logout", "description": "User can log out"},
        ],
        "business_case": "Customers need self-service authentication.",
        "project_context": {"squad": "auth", "domain": "identity"},
    }


def _http_payload() -> Dict[str, Any]:
    """Payload as it arrives over HTTP — no git-sourced fields."""
    return {
        "business_case": "Customers need self-service authentication.",
        "project_context": {"squad": "auth", "domain": "identity"},
    }


def _llm_payload(gaps: List[Dict[str, Any]]) -> str:
    return json.dumps({"gap_report": gaps, "gap_summary": {}})


# ---------------------------------------------------------------- behaviour

class TestValidateInputs:
    def test_passes_with_valid_payload(self):
        behaviour.validate_inputs(_payload_with_reqs(), _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_on_empty_requirements(self):
        payload = _payload_with_reqs()
        payload["structured_requirements"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        message = exc_info.value.message.lower()
        assert "structured_requirements" in message

    def test_stops_on_missing_business_case(self):
        payload = _payload_with_reqs()
        payload["business_case"] = ""
        with pytest.raises(PipelineStopError):
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_below_min_items(self):
        inputs_cfg = dict(_INPUTS_CFG)
        inputs_cfg["structured_requirements"] = dict(
            _INPUTS_CFG["structured_requirements"], min_items=3
        )
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(_payload_with_reqs(), inputs_cfg, _BEHAVIOUR_CFG)
        assert exc_info.value.detail["min_items"] == 3


class TestSummarise:
    def test_clean_report(self):
        summary = behaviour.summarise([], total_requirements=5, behaviour_cfg=_BEHAVIOUR_CFG)
        assert summary["overall_quality"] == "clean"
        assert summary["recommendation"] == "proceed"
        assert summary["blocking_gaps"] == 0
        assert summary["total_gaps_found"] == 0
        assert summary["total_requirements_analysed"] == 5

    def test_high_severity_blocks(self):
        gaps = [
            {"gap_id": "GAP-001", "severity": "high", "gap_type": "non_measurable_nfr"},
            {"gap_id": "GAP-002", "severity": "low", "gap_type": "ambiguous_language"},
        ]
        summary = behaviour.summarise(gaps, total_requirements=4, behaviour_cfg=_BEHAVIOUR_CFG)
        assert summary["overall_quality"] == "needs_attention"
        assert summary["recommendation"] == "resolve_blocking_gaps_first"
        assert summary["blocking_gaps"] == 1

    def test_critical_triggers_rework(self):
        gaps = [{"gap_id": "GAP-001", "severity": "critical", "gap_type": "missing_actor"}]
        summary = behaviour.summarise(gaps, total_requirements=2, behaviour_cfg=_BEHAVIOUR_CFG)
        assert summary["overall_quality"] == "blocked"
        assert summary["recommendation"] == "significant_rework_needed"
        assert summary["blocking_gaps"] == 1

    def test_only_medium_low_needs_attention(self):
        gaps = [
            {"gap_id": "GAP-001", "severity": "medium", "gap_type": "ambiguous_language"},
            {"gap_id": "GAP-002", "severity": "low", "gap_type": "missing_business_value"},
        ]
        summary = behaviour.summarise(gaps, total_requirements=3, behaviour_cfg=_BEHAVIOUR_CFG)
        assert summary["overall_quality"] == "needs_attention"
        assert summary["blocking_gaps"] == 0

    def test_severity_counts_complete(self):
        gaps = [
            {"gap_id": "GAP-001", "severity": "high", "gap_type": "non_measurable_nfr"},
        ]
        summary = behaviour.summarise(gaps, total_requirements=1, behaviour_cfg=_BEHAVIOUR_CFG)
        for sev in _BEHAVIOUR_CFG["severity_levels"]:
            assert sev in summary["gaps_by_severity"]


class TestRenumberGaps:
    def test_sequential_ids(self):
        gaps = [
            {"gap_id": "GAP-099", "severity": "high"},
            {"gap_id": "xxxx", "severity": "low"},
            {"severity": "medium"},
        ]
        renumbered = behaviour.renumber_gaps(gaps)
        assert [g["gap_id"] for g in renumbered] == ["GAP-001", "GAP-002", "GAP-003"]


class TestCoerceReqIdRefs:
    def test_unknown_refs_become_none(self):
        gaps = [
            {"gap_id": "GAP-001", "req_id_ref": "REQ-999"},
            {"gap_id": "GAP-002", "req_id_ref": "REQ-001"},
            {"gap_id": "GAP-003", "req_id_ref": ""},
            {"gap_id": "GAP-004", "req_id_ref": None},
        ]
        reqs = [{"req_id": "REQ-001"}]
        coerced = behaviour.coerce_req_id_refs(gaps, reqs)
        assert coerced[0]["req_id_ref"] is None
        assert coerced[1]["req_id_ref"] == "REQ-001"
        assert coerced[2]["req_id_ref"] is None
        assert coerced[3]["req_id_ref"] is None


# ---------------------------------------------------------------- output parser

class TestOutputParser:
    def test_parses_well_formed_json(self):
        raw = _llm_payload([
            {
                "gap_id": "GAP-001",
                "req_id_ref": "REQ-001",
                "gap_type": "ambiguous_language",
                "severity": "high",
                "description": "Vague wording.",
                "recommendation": "Make it measurable.",
                "auto_resolvable": False,
            }
        ])
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
        )
        assert len(result["gap_report"]) == 1
        assert result["gap_report"][0]["gap_id"] == "GAP-001"

    def test_strips_json_fence(self):
        gaps = [
            {
                "gap_id": "GAP-001",
                "req_id_ref": None,
                "gap_type": "missing_actor",
                "severity": "low",
                "description": "No actor named.",
                "recommendation": "Add an actor.",
                "auto_resolvable": False,
            }
        ]
        raw = f"```json\n{_llm_payload(gaps)}\n```"
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
        )
        assert result["gap_report"][0]["gap_type"] == "missing_actor"

    def test_rejects_unknown_gap_type(self):
        raw = _llm_payload([
            {
                "gap_id": "GAP-001",
                "req_id_ref": None,
                "gap_type": "made_up_category",
                "severity": "high",
                "description": "X",
                "recommendation": "Y",
                "auto_resolvable": False,
            }
        ])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            )
        assert "gap_type" in exc_info.value.message

    def test_rejects_unknown_severity(self):
        raw = _llm_payload([
            {
                "gap_id": "GAP-001",
                "req_id_ref": None,
                "gap_type": "ambiguous_language",
                "severity": "catastrophic",
                "description": "X",
                "recommendation": "Y",
                "auto_resolvable": False,
            }
        ])
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            )

    def test_rejects_invalid_json(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "not valid json at all",
                allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            )

    def test_rejects_empty_response(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "",
                allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
                allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
            )

    def test_empty_report_is_valid(self):
        raw = json.dumps({"gap_report": [], "gap_summary": None})
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["gap_categories"],
            allowed_severities=_BEHAVIOUR_CFG["severity_levels"],
        )
        assert result["gap_report"] == []


# ---------------------------------------------------------------- end-to-end

class _FakeGitReader:
    """Returns canned JSON regardless of path — used as a test double."""

    def __init__(self, content: Dict[str, Any]) -> None:
        self._content = content
        self.calls: List[str] = []

    async def read_json(self, path: str) -> Dict[str, Any]:
        self.calls.append(path)
        return self._content


class TestAgentRun:
    """End-to-end ``run()`` test with both the LLM client and the git
    reader replaced by test doubles."""

    @pytest.mark.asyncio
    async def test_full_flow_with_mocked_collaborators(self):
        from agents.ad04_gap_detection import agent

        llm_text = _llm_payload([
            {
                "gap_id": "WRONG-ID",
                "req_id_ref": "REQ-001",
                "gap_type": "ambiguous_language",
                "severity": "high",
                "description": "REQ-001 is vague.",
                "recommendation": "Add measurable criteria.",
                "auto_resolvable": False,
            },
            {
                "gap_id": "ALSO-WRONG",
                "req_id_ref": "REQ-999",
                "gap_type": "implied_but_unstated",
                "severity": "low",
                "description": "Business case mentions audit logging.",
                "recommendation": "Add an audit logging requirement.",
                "auto_resolvable": False,
            },
        ])

        fake_reader = _FakeGitReader({
            "structured_requirements": [
                {"req_id": "REQ-001", "title": "Login"},
                {"req_id": "REQ-002", "title": "Logout"},
            ],
        })

        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_http_payload(), run_id="test-run-001")

        assert fake_reader.calls == ["runs/test-run-001/plan/PL-01_output.json"]
        assert [g["gap_id"] for g in result["gap_report"]] == ["GAP-001", "GAP-002"]
        assert result["gap_report"][1]["req_id_ref"] is None  # REQ-999 coerced
        assert result["gap_summary"]["blocking_gaps"] == 1
        assert result["gap_summary"]["overall_quality"] == "needs_attention"
        assert result["gap_summary"]["recommendation"] == "resolve_blocking_gaps_first"
        assert result["run_id"] == "test-run-001"
        assert result["agent_id"] == "AD-04"

    @pytest.mark.asyncio
    async def test_empty_requirements_stops_before_llm(self):
        from agents.ad04_gap_detection import agent

        fake_reader = _FakeGitReader({"structured_requirements": []})
        mock_call = AsyncMock()
        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError):
                await agent.run(_http_payload(), run_id="test-run-002")
        mock_call.assert_not_called()
