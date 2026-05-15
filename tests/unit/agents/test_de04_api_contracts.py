"""Unit tests for DE-04 - Api Contracts Agent.

The LLM client and the git reader are mocked so these run without
network access. Behaviour and output_parser are exercised directly with
synthetic LLM payloads; the agent ``run()`` flow is exercised end-to-end
with both collaborators replaced.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from agents.de04_api_contracts import behaviour, output_parser
from core.exceptions import OutputParseError, PipelineStopError


# ---------------------------------------------------------------- fixtures

_BEHAVIOUR_CFG = {
    "on_empty_requirements": "stop_and_report",
    "on_no_contracts_found": "return_empty_registry",
    "min_requirements": 1,
    "contract_categories": [
        "crud_endpoint",
        "query_endpoint",
        "event_endpoint",
        "auth_endpoint",
        "file_endpoint",
        "integration_endpoint",
    ],
    "http_methods_allowed": ["GET", "POST", "PUT", "PATCH", "DELETE"],
}

_INPUTS_CFG = {
    "bs_docs": {
        "required": True,
        "type": "object",
        "on_fail": "stop_and_report",
        "description": "Business specification documents containing structured requirements, project context, business case, and constraints",
    },
    "data_design_response": {
        "required": True,
        "type": "object",
        "on_fail": "stop_and_report",
        "description": "Data design model and strategy from DE-03 agent output",
    },
}


def _spec(
    *,
    spec_id: str = "OS-001",
    endpoint_name: str = "POST /users",
    http_method: str = "POST",
    path: str = "/users",
    contract_category: str = "crud_endpoint",
    req_id_refs: List[str] | None = None,
    entity_refs: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "spec_id": spec_id,
        "endpoint_name": endpoint_name,
        "http_method": http_method,
        "path": path,
        "description": f"{endpoint_name} endpoint",
        "contract_category": contract_category,
        "request_schema": {"email": "string"},
        "response_schema": {"user_id": "uuid"},
        "req_id_refs": req_id_refs if req_id_refs is not None else ["REQ-001"],
        "entity_refs": entity_refs if entity_refs is not None else ["DM-001"],
    }


def _payload_with_inputs() -> Dict[str, Any]:
    """Payload with nested bs_docs and data_design_response structure."""
    return {
        "bs_docs": {
            "structured_requirements": [
                {"req_id": "REQ-001", "title": "Candidate registration"},
                {"req_id": "REQ-002", "title": "Resume upload"},
            ],
            "project_context": {"squad": "talent-platform", "domain": "recruitment"},
            "business_case": "AI job portal",
        },
        "data_design_response": {
            "data_model": [
                {"entity_id": "DM-001", "entity_name": "UserAccount"},
                {"entity_id": "DM-002", "entity_name": "Resume"},
            ]
        },
    }


def _http_payload() -> Dict[str, Any]:
    """Minimal payload for HTTP request (shared folder provides the rest)."""
    return {
        "bs_docs": {
            "project_context": {"squad": "talent-platform", "domain": "recruitment"},
            "business_case": "AI job portal",
            "structured_requirements": [],
        },
        "data_design_response": {
            "data_model": []
        },
    }


def _llm_payload(specs: List[Dict[str, Any]]) -> str:
    return json.dumps({"openapi_spec": specs, "schema_registry": {}})


# ---------------------------------------------------------------- behaviour

class TestValidateInputs:
    def test_passes_with_valid_payload(self):
        behaviour.validate_inputs(_payload_with_inputs(), _INPUTS_CFG, _BEHAVIOUR_CFG)

    def test_stops_on_empty_requirements(self):
        payload = _payload_with_inputs()
        payload["bs_docs"]["structured_requirements"] = []
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "structured_requirements" in exc_info.value.message.lower()

    def test_stops_on_missing_data_design(self):
        payload = _payload_with_inputs()
        payload["data_design_response"] = None
        with pytest.raises(PipelineStopError) as exc_info:
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)
        assert "data_design_response" in exc_info.value.message

    def test_stops_on_missing_bs_docs(self):
        payload = _payload_with_inputs()
        payload["bs_docs"] = None
        with pytest.raises(PipelineStopError):
            behaviour.validate_inputs(payload, _INPUTS_CFG, _BEHAVIOUR_CFG)


class TestRenumberSpecs:
    def test_sequential_ids(self):
        specs = [
            _spec(spec_id="OS-099"),
            _spec(spec_id="WRONG", endpoint_name="GET /users"),
            _spec(spec_id="", endpoint_name="DELETE /users"),
        ]
        renumbered = behaviour.renumber_specs(specs)
        assert [s["spec_id"] for s in renumbered] == ["OS-001", "OS-002", "OS-003"]


class TestCoerceReqIdRefs:
    def test_unknown_refs_dropped(self):
        specs = [
            _spec(req_id_refs=["REQ-001", "REQ-999"]),
            _spec(endpoint_name="GET /users", req_id_refs=["REQ-002", "", None, "REQ-999"]),
        ]
        # Add a spec with the req_id_refs key missing entirely
        spec_no_key = _spec(endpoint_name="DELETE /users")
        spec_no_key.pop("req_id_refs", None)
        specs.append(spec_no_key)
        reqs = [{"req_id": "REQ-001"}, {"req_id": "REQ-002"}]
        coerced = behaviour.coerce_req_id_refs(specs, reqs)
        assert coerced[0]["req_id_refs"] == ["REQ-001"]
        assert coerced[1]["req_id_refs"] == ["REQ-002"]
        assert coerced[2]["req_id_refs"] == []


class TestCoerceEntityRefs:
    def test_unknown_refs_dropped(self):
        specs = [
            _spec(entity_refs=["DM-001", "DM-999"]),
            _spec(endpoint_name="GET /users", entity_refs=["DM-002", "DM-999"]),
        ]
        entities = [{"entity_id": "DM-001"}, {"entity_id": "DM-002"}]
        coerced = behaviour.coerce_entity_refs(specs, entities)
        assert coerced[0]["entity_refs"] == ["DM-001"]
        assert coerced[1]["entity_refs"] == ["DM-002"]


class TestDedupeEndpoints:
    def test_merges_duplicates(self):
        specs = [
            _spec(endpoint_name="POST /users", req_id_refs=["REQ-001"], entity_refs=["DM-001"]),
            _spec(endpoint_name="POST /users", req_id_refs=["REQ-002"], entity_refs=["DM-002"]),
            _spec(endpoint_name="GET /users", req_id_refs=["REQ-003"]),
        ]
        deduped = behaviour.dedupe_endpoints(specs)
        assert len(deduped) == 2
        post = next(s for s in deduped if s["endpoint_name"] == "POST /users")
        assert sorted(post["req_id_refs"]) == ["REQ-001", "REQ-002"]
        assert sorted(post["entity_refs"]) == ["DM-001", "DM-002"]


class TestComputeRegistry:
    def test_full_coverage_yields_proceed(self):
        specs = [
            _spec(req_id_refs=["REQ-001"]),
            _spec(endpoint_name="GET /users", contract_category="query_endpoint", req_id_refs=["REQ-002"]),
        ]
        reqs = [{"req_id": "REQ-001"}, {"req_id": "REQ-002"}]
        registry = behaviour.compute_registry(specs, reqs, _BEHAVIOUR_CFG)
        assert registry["recommendation"] == "proceed"
        assert registry["total_contracts_generated"] == 2
        assert registry["uncovered_requirements"] == 0
        assert registry["contracts_by_category"]["crud_endpoint"] == 1
        assert registry["contracts_by_category"]["query_endpoint"] == 1

    def test_partial_coverage_yields_review(self):
        specs = [_spec(req_id_refs=["REQ-001"])]
        reqs = [{"req_id": "REQ-001"}, {"req_id": "REQ-002"}]
        registry = behaviour.compute_registry(specs, reqs, _BEHAVIOUR_CFG)
        assert registry["recommendation"] == "review_required"
        assert registry["uncovered_requirements"] == 1

    def test_no_contracts_yields_blocked(self):
        registry = behaviour.compute_registry([], [{"req_id": "REQ-001"}], _BEHAVIOUR_CFG)
        assert registry["recommendation"] == "blocked"
        assert registry["total_contracts_generated"] == 0


# ---------------------------------------------------------------- output parser

class TestOutputParser:
    def test_parses_well_formed_json(self):
        raw = _llm_payload([_spec()])
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
            allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
        )
        assert len(result["openapi_spec"]) == 1
        assert result["openapi_spec"][0]["http_method"] == "POST"

    def test_strips_json_fence(self):
        raw = f"```json\n{_llm_payload([_spec()])}\n```"
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
            allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
        )
        assert result["openapi_spec"][0]["endpoint_name"] == "POST /users"

    def test_rejects_unknown_http_method(self):
        raw = _llm_payload([_spec(http_method="OPTIONS")])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
                allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
            )
        assert "http_method" in exc_info.value.message

    def test_rejects_unknown_category(self):
        raw = _llm_payload([_spec(contract_category="webhook_endpoint")])
        with pytest.raises(OutputParseError) as exc_info:
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
                allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
            )
        assert "contract_category" in exc_info.value.message

    def test_rejects_invalid_recommendation(self):
        raw = json.dumps({
            "openapi_spec": [_spec()],
            "schema_registry": {"recommendation": "ship_it"},
        })
        with pytest.raises(OutputParseError):
            output_parser.parse(
                raw,
                allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
                allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
            )

    def test_rejects_invalid_json(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "not valid json",
                allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
                allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
            )

    def test_rejects_empty_response(self):
        with pytest.raises(OutputParseError):
            output_parser.parse(
                "",
                allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
                allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
            )

    def test_empty_spec_list_is_valid(self):
        raw = json.dumps({"openapi_spec": [], "schema_registry": None})
        result = output_parser.parse(
            raw,
            allowed_categories=_BEHAVIOUR_CFG["contract_categories"],
            allowed_methods=_BEHAVIOUR_CFG["http_methods_allowed"],
        )
        assert result["openapi_spec"] == []


# ---------------------------------------------------------------- end-to-end

class _FakeGitReader:
    """Returns canned JSON keyed by path - used as a test double."""

    def __init__(self, contents: Dict[str, Dict[str, Any]]) -> None:
        self._contents = contents
        self.calls: List[str] = []

    async def read_json(self, path: str) -> Dict[str, Any]:
        self.calls.append(path)
        return self._contents[path]


class TestAgentRun:
    """End-to-end ``run()`` test with both LLM client and git reader replaced."""

    @pytest.mark.asyncio
    async def test_full_flow_with_mocked_collaborators(self):
        from agents.de04_api_contracts import agent

        llm_text = _llm_payload([
            _spec(
                spec_id="WRONG-ID",
                endpoint_name="POST /users",
                req_id_refs=["REQ-001", "REQ-999"],
                entity_refs=["DM-001", "DM-999"],
            ),
            _spec(
                spec_id="ALSO-WRONG",
                endpoint_name="POST /users",
                req_id_refs=["REQ-002"],
                entity_refs=["DM-002"],
            ),
            _spec(
                spec_id="X",
                endpoint_name="GET /users/{id}",
                http_method="GET",
                contract_category="query_endpoint",
                req_id_refs=["REQ-001"],
                entity_refs=["DM-001"],
            ),
        ])

        fake_reader = _FakeGitReader({
            "runs/test-run-001/plan/AD-01_output.json": {
                "structured_requirements": [
                    {"req_id": "REQ-001", "title": "Candidate registration"},
                    {"req_id": "REQ-002", "title": "Resume upload"},
                ],
            },
            "runs/test-run-001/design/data_design_model_and_strategy.json": {
                "data_model": [
                    {"entity_id": "DM-001", "entity_name": "UserAccount"},
                    {"entity_id": "DM-002", "entity_name": "Resume"},
                ],
            },
        })

        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=AsyncMock(return_value=llm_text)):
            result = await agent.run(_http_payload(), run_id="test-run-001")

        # Dedupe merges the two POST /users entries; renumber yields sequential
        assert [s["spec_id"] for s in result["openapi_spec"]] == ["OS-001", "OS-002"]
        post = next(s for s in result["openapi_spec"] if s["endpoint_name"] == "POST /users")
        assert sorted(post["req_id_refs"]) == ["REQ-001", "REQ-002"]
        assert sorted(post["entity_refs"]) == ["DM-001", "DM-002"]
        # REQ-999 / DM-999 dropped by coercion
        assert "REQ-999" not in post["req_id_refs"]
        assert "DM-999" not in post["entity_refs"]
        # Registry is recomputed deterministically
        assert result["schema_registry"]["total_contracts_generated"] == 2
        assert result["schema_registry"]["uncovered_requirements"] == 0
        assert result["schema_registry"]["recommendation"] == "proceed"
        assert result["run_id"] == "test-run-001"
        assert result["agent_id"] == "DE-04"

    @pytest.mark.asyncio
    async def test_empty_requirements_stops_before_llm(self):
        from agents.de04_api_contracts import agent

        fake_reader = _FakeGitReader({
            "runs/test-run-002/plan/AD-01_output.json": {"structured_requirements": []},
            "runs/test-run-002/design/data_design_model_and_strategy.json": {
                "data_model": [{"entity_id": "DM-001"}],
            },
        })
        mock_call = AsyncMock()
        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError):
                await agent.run(_http_payload(), run_id="test-run-002")
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_data_model_stops_before_llm(self):
        from agents.de04_api_contracts import agent

        fake_reader = _FakeGitReader({
            "runs/test-run-003/plan/AD-01_output.json": {
                "structured_requirements": [{"req_id": "REQ-001"}],
            },
            "runs/test-run-003/design/data_design_model_and_strategy.json": {
                "data_model": [],
            },
        })
        mock_call = AsyncMock()
        with patch.object(agent, "_git_reader", fake_reader), \
             patch.object(agent._llm_client, "call", new=mock_call):
            with pytest.raises(PipelineStopError) as exc_info:
                await agent.run(_http_payload(), run_id="test-run-003")
            assert "data_design_model_and_strategy" in exc_info.value.message
        mock_call.assert_not_called()
