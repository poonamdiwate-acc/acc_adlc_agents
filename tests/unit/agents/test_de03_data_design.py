"""Unit tests for DE-03 - Data Design Agent.

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

from agents.de03_data_design import behaviour, output_parser
from core.exceptions import OutputParseError, PipelineStopError


# ---------------------------------------------------------------- fixtures

_BEHAVIOUR_CFG = {
    "on_empty_requirements": "stop_and_report",
    "min_requirements": 1,
    "entity_categories": [
        "core_business",
        "transactional",
        "reference_lookup",
        "audit_history",
        "configuration",
        "derived_analytical",
    ],
    "storage_classes": [
        "relational",
        "document",
        "key_value",
        "columnar",
        "graph",
        "vector",
        "search",
        "object_blob",
    ],
    "confidence_levels": ["high", "medium", "low"],
    "blocking_confidence": ["low"],
}

_INPUTS_CFG = {
    "structured_requirements": {
        "required": True,
        "type": "array",
        "min_items": 1,
        "on_fail": "stop_and_report",
    },
}


def _payload_with_reqs() -> Dict[str, Any]:
    return {
        "structured_requirements": [
            {"req_id": "REQ-001", "title": "Candidate registration"},
            {"req_id": "REQ-002", "title": "Resume upload"},
        ],
    }


def _http_payload() -> Dict[str, Any]:
    """Payload as it arrives over HTTP — no git-sourced fields."""
    return {}


def _llm_payload(
    data_model: List[Dict[str, Any]],
    storage_selection: Dict[str, Any] | None = None,
) -> str:
    return json.dumps({
        "data_model": data_model,
        "storage_selection": storage_selection or {
            "primary_store": {
                "technology": "PostgreSQL",
                "storage_class": "relational",
                "rationale": "Strong consistency and joins.",
                "data_types": ["UserAccount"],
                "confidence": "high",
            },
            "secondary_stores": [],
            "overall_strategy": "PostgreSQL as system of record.",
        },
    })


def _entity(
    *,
    entity_id: str = "DM-001",
    entity_name: str = "UserAccount",
    category: str = "core_business",
    confidence: str = "high",
    req_id_refs: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": f"{entity_name} entity",
        "category": category,
        "confidence": confidence,
        "attributes": [
            {"name": "id", "type": "uuid", "required": True, "description": "PK"},
        ],
        "relationships": [],
        "req_id_refs": req_id_refs if req_id_refs is not None else ["REQ-001"],
    }


# ---------------------------------------------------------------- behaviour

class TestValidateInputs:
    def test_passes_with_valid_payload(self):
        behaviour.validate_inputs(_payload_with_reqs(), _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_on_empty_requirements(self):
        payload = _payload_with_reqs()
        payload["structured_requirements"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "structured_requirements" in exc_info.value.message.lower()

    def test_stops_below_min_items(self):
        inputs_cfg = dict(_INPUTS_CFG)
        inputs_cfg["structured_requirements"] = dict(
            _INPUTS_CFG["structured_requirements"], min_items=3
        )
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(_payload_with_reqs(), inputs_cfg, _BEHAVIOUR_CFG)
        assert exc_info.value.detail["min_items"] == 3


class TestRenumberEntities:
    def test_sequential_ids(self):
        entities = [
            {"entity_id": "DM-099"},
            {"entity_id": "xxxx"},
            {},
        ]
        renumbered = behaviour.renumber_entities(entities)
        assert [e["entity_id"] for e in renumbered] == ["DM-001", "DM-002", "DM-003"]


class TestCoerceReqIdRefs:
    def test_unknown_refs_dropped(self):
        entities = [
            {"entity_id": "DM-001", "req_id_refs": ["REQ-001", "REQ-999"]},
            {"entity_id": "DM-002", "req_id_refs": ["REQ-002", "", None, "REQ-999"]},
            {"entity_id": "DM-003", "req_id_refs": None},
            {"entity_id": "DM-004"},
        ]
        reqs = [{"req_id": "REQ-001"}, {"req_id": "REQ-002"}]
        coerced = behaviour.coerce_req_id_refs(entities, reqs)
        assert coerced[0]["req_id_refs"] == ["REQ-001"]
        assert coerced[1]["req_id_refs"] == ["REQ-002"]
        assert coerced[2]["req_id_refs"] == []
        assert coerced[3]["req_id_refs"] == []


class TestBlockingItems:
    def test_low_confidence_entity_blocks(self):
        entities = [
            _entity(entity_id="DM-001", confidence="high"),
            _entity(entity_id="DM-002", confidence="low"),
        ]
        storage = {
            "primary_store": {"confidence": "high"},
            "secondary_stores": [{"confidence": "low"}],
        }
        result = behaviour.blocking_items(entities, storage, _BEHAVIOUR_CFG)
        assert result["entities"] == ["DM-002"]
        assert result["stores"] == ["secondary_stores[0]"]
        assert behaviour.is_blocking(result) is True

    def test_all_high_not_blocking(self):
        entities = [_entity(confidence="high")]
        storage = {"primary_store": {"confidence": "high"}, "secondary_stores": []}
        result = behaviour.blocking_items(entities, storage, _BEHAVIOUR_CFG)
        assert result == {"entities": [], "stores": []}
        assert behaviour.is_blocking(result) is False


class TestFindUncoveredRequirements:
    def test_functional_reqs_without_entities_flagged(self):
        entities = [_entity(req_id_refs=["REQ-001"])]
        reqs = [
            {"req_id": "REQ-001", "type": "functional"},
            {"req_id": "REQ-002", "type": "functional"},
            {"req_id": "REQ-003", "type": "non_functional"},
            {"req_id": "REQ-004", "type": "non_functional", "gaps_detected": True},
        ]
        uncovered = behaviour.find_uncovered_requirements(entities, reqs)
        # REQ-002 is functional and uncovered; REQ-004 has gaps_detected;
        # REQ-003 has neither so it's ignored.
        assert sorted(uncovered) == ["REQ-002", "REQ-004"]


# ---------------------------------------------------------------- output parser

class TestOutputParser:
    def test_parses_well_formed_json(self):
        raw = _llm_payload([_entity()])
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
            allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
            allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
        )
        assert len(result["data_model"]) == 1
        assert result["storage_selection"]["primary_store"]["technology"] == "PostgreSQL"

    def test_strips_json_fence(self):
        raw = f"```json\n{_llm_payload([_entity()])}\n```"
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
            allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
            allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
        )
        assert result["data_model"][0]["entity_name"] == "UserAccount"

    def test_rejects_unknown_category(self):
        raw = _llm_payload([_entity(category="made_up")])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )
        assert "category" in exc_info.value.message

    def test_rejects_unknown_confidence(self):
        raw = _llm_payload([_entity(confidence="absolutely_certain")])
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )

    def test_rejects_unknown_storage_class(self):
        raw = _llm_payload(
            [_entity()],
            storage_selection={
                "primary_store": {
                    "technology": "Unknown",
                    "storage_class": "magic_blob",
                    "rationale": "x",
                    "data_types": [],
                    "confidence": "high",
                },
                "secondary_stores": [],
                "overall_strategy": "x",
            },
        )
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )

    def test_rejects_bad_relationship_type(self):
        entity = _entity()
        entity["relationships"] = [
            {"entity": "Order", "type": "some_other", "description": "x"}
        ]
        raw = _llm_payload([entity])
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )

    def test_rejects_invalid_json(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "not valid json",
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )

    def test_rejects_empty_response(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "",
                allowed_categories=_BEHAVIOUR_CFG["entity_categories"],
                allowed_storage_classes=_BEHAVIOUR_CFG["storage_classes"],
                allowed_confidence_levels=_BEHAVIOUR_CFG["confidence_levels"],
            )


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
    """End-to-end ``run()`` test with both LLM client and git reader replaced."""

    @pytest.mark.asyncio
    async def test_full_flow_with_mocked_collaborators(self):
        from agents.de03_data_design import agent

        llm_text = _llm_payload(
            [
                _entity(
                    entity_id="WRONG-ID",
                    entity_name="UserAccount",
                    req_id_refs=["REQ-001", "REQ-999"],
                ),
                _entity(
                    entity_id="ALSO-WRONG",
                    entity_name="AuditEvent",
                    category="audit_history",
                    confidence="low",
                    req_id_refs=["REQ-002"],
                ),
            ],
            storage_selection={
                "primary_store": {
                    "technology": "PostgreSQL",
                    "storage_class": "relational",
                    "rationale": "ACID + joins.",
                    "data_types": ["UserAccount", "AuditEvent"],
                    "confidence": "high",
                },
                "secondary_stores": [
                    {
                        "technology": "Redis",
                        "storage_class": "key_value",
                        "purpose": "Session cache",
                        "rationale": "Sub-10ms session lookup.",
                        "confidence": "high",
                    }
                ],
                "overall_strategy": "Postgres SoT + Redis cache.",
            },
        )

        fake_reader = _FakeGitReader({
            "structured_requirements": [
                {"req_id": "REQ-001", "title": "Candidate registration", "type": "functional"},
                {"req_id": "REQ-002", "title": "Audit logging", "type": "non_functional"},
            ],
        })

        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_http_payload(), run_id="test-run-001")

        assert fake_reader.calls == ["runs/test-run-001/plan/AD-01_output.json"]
        assert [e["entity_id"] for e in result["data_model"]] == ["DM-001", "DM-002"]
        # REQ-999 dropped, REQ-001 kept
        assert result["data_model"][0]["req_id_refs"] == ["REQ-001"]
        assert result["data_model"][1]["req_id_refs"] == ["REQ-002"]
        # Storage came through intact
        assert result["storage_selection"]["primary_store"]["technology"] == "PostgreSQL"
        assert result["storage_selection"]["secondary_stores"][0]["technology"] == "Redis"
        assert result["run_id"] == "test-run-001"
        assert result["agent_id"] == "DE-03"

    @pytest.mark.asyncio
    async def test_empty_requirements_stops_before_llm(self):
        from agents.de03_data_design import agent

        fake_reader = _FakeGitReader({"structured_requirements": []})
        mock_call = AsyncMock()
        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError):
                await agent.run(_http_payload(), run_id="test-run-002")
        mock_call.assert_not_called()

