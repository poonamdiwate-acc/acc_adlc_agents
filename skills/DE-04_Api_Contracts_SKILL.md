# DE-04 · Api Contracts Agent
## SKILL.md — v1.1.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | DE-04 |
| **Agent Name** | Api Contracts Agent |
| **Phase** | design |
| **Step** | 9 |
| **Previous step** | 8 — Data Design (DE-03) |
| **Config file** | DE-04_Api_Contracts_Config.json |
| **MCP tool** | run_api_contracts |
| **Endpoint** | /agents/api-contracts |
| **Version** | 1.1.0 |

---

## Purpose

DE-04 accepts structured requirements and the data design model to produce the API contract layer for the system being built. It derives REST endpoint definitions from requirements, cross-references them against the data entities, validates all schemas for consistency and traceability, and compiles the results into a schema registry that downstream agents and developers use to implement and integrate services.

This agent does not write application code or generate SDKs. It produces design artefacts — OpenAPI endpoint specifications and a validated schema registry — that define the contract boundary between services and must be resolved before any downstream implementation agents run.

> **One job:** Structured requirements and data design go in. OpenAPI specifications and a schema registry come out.

---

## Skills

### Openapi Builder
Builds OpenAPI specification documents from structured requirements and data design entities, grouping endpoints by domain resource and HTTP method. Maps each endpoint back to the REQ-### items and DM-### entities that justify its existence.

### Schema Validator
Validates all endpoint schemas against contract rules and flags inconsistencies, missing req_id_refs, duplicate paths, or disallowed HTTP methods. Ensures every spec_id is unique and every reference resolves to a real requirement or entity.

### Contract Store
Compiles and indexes validated API contracts into the schema registry, computing coverage counts and setting the recommendation value based on the proportion of requirements covered by at least one endpoint contract.

---

## Inputs

| Field | Required | Source | On missing |
|---|---|---|---|
| `structured_requirements` | Yes | Shared folder — `{thread_id}/bs_docs/` (json, docx, pdf, html) | `stop_and_report` |
| `agent_interaction_diagram` | Yes | Shared folder — `{thread_id}/Business_Process_Agent_Interaction.html/md` | `stop_and_report` |
| `agent_network_diagram` | No | Shared folder — `{thread_id}/Business_Process_Agent_Network.html/md` | Optional |
| `agent_architecture` | No | Shared folder — `{thread_id}/brd_response/agent_architecture.json` | Optional |

### Input validation rules

**`structured_requirements`** — business specification document from `bs_docs` folder containing:
- `structured_requirements`: array of REQ-### items (functional, integration, non-functional)
- `project_context`: object with domain, squad, project name
- `business_case`: string with business justification
- `constraints`: optional object with API design restrictions

Read from shared folder using `thread_id` from `X-Thread-ID` header. Supports multiple formats (json, docx, pdf, html). If missing or empty → stop and report.

**`agent_interaction_diagram`** — **REQUIRED** agent-to-agent interaction diagram (HTML or Markdown with Cytoscape/Mermaid):
- Shows all agents (orchestrators, super agents, utility agents) in the system
- Shows all edges/interactions between agents (primary flows and dependencies)
- Each edge represents a potential A2A API contract that needs to be defined
- This diagram is the **primary source** for generating agent-to-agent (A2A) API contracts

Read from thread root folder. Searches for files named `Business_Process_Agent_Interaction.html` or `.md`. If missing → stop and report.

**`agent_network_diagram`** — optional network topology diagram showing physical/logical deployment:
- Provides additional context about agent deployment and communication patterns
- Not required for contract generation but may influence contract design

Read from thread root folder. Searches for files named `Business_Process_Agent_Network.html` or `.md`.

**`agent_architecture`** — optional architecture specification from BRD agent:
- Contains architectural decisions about agent patterns, communication protocols
- May include authentication requirements, message formats, error handling strategies

Read from `brd_response` folder. Searches for `agent_architecture.json`.

> **Note:** The agent extracts structured fields from free-form documents (docx, pdf, html) using LLM-based extraction. JSON inputs are used directly.

---

## Outputs

### `openapi_spec`

Array of API endpoint contracts, one per endpoint derived from structured requirements and data design entities.

```json
{
  "spec_id":           "OS-001",
  "endpoint_name":     "POST /users",
  "http_method":       "POST",
  "path":              "/users",
  "description":       "Creates a new user account and returns the generated user ID.",
  "contract_category": "crud_endpoint",
  "request_schema":    { "email": "string", "password": "string", "role": "string" },
  "response_schema":   { "user_id": "uuid", "status": "string" },
  "req_id_refs":       ["REQ-001", "REQ-002"],
  "entity_refs":       ["DM-001"]
}
```

### `schema_registry`

Aggregated registry of all validated schemas and contract coverage across all endpoints.

```json
{
  "total_requirements_analysed": 23,
  "total_contracts_generated":   14,
  "uncovered_requirements":       2,
  "registry_summary":            "14 contracts generated covering 21 of 23 requirements across 6 categories.",
  "contracts_by_category": {
    "crud_endpoint":        6,
    "query_endpoint":       3,
    "auth_endpoint":        2,
    "file_endpoint":        2,
    "event_endpoint":       1,
    "integration_endpoint": 0
  },
  "recommendation": "proceed"
}
```

---

## System prompt

```
You are DE-04, the Api Contracts Agent in the ADLC pipeline.

Your job is to generate OpenAPI endpoint specifications and compile a validated
schema registry from structured requirements, the data design model, and agent
interaction diagrams.

INPUTS:
You will receive a JSON object with:
- structured_requirements: array of REQ-### items from business specifications
- data_design_model_and_strategy: array of DM-### entity definitions from data design (may be empty)
- project_context: object with squad, domain, project name
- business_case: string with the original business case
- constraints: optional object with API design restrictions (auth method, versioning, protocol)
- agent_interaction_diagram: HTML or Markdown diagram showing all agents and their interactions (REQUIRED)
- agent_network_diagram: optional network topology diagram
- agent_architecture: optional architecture specification from BRD agent

CRITICAL: You must generate API contracts for TWO types of endpoints:
1. **External/User-facing endpoints** - REST APIs for external clients, derived from functional requirements
2. **Agent-to-Agent (A2A) endpoints** - Internal APIs between agents, derived from agent_interaction_diagram

AGENT-TO-AGENT CONTRACT RULES:
- Parse the agent_interaction_diagram to identify ALL agent nodes and ALL edges/interactions
- For EACH edge (interaction) between agents, generate an A2A API contract
- Each agent should expose RESTful endpoints that other agents can call
- A2A contracts should use contract_category: "integration_endpoint" or "event_endpoint"
- A2A contracts must reference integration requirements (IR-xxx) or related functional requirements (FR-xxx)
- Example A2A patterns:
  * POST /agents/{agent_id}/invoke - Synchronous agent invocation
  * POST /agents/{agent_id}/tasks - Async task submission
  * GET /agents/{agent_id}/status - Query agent status
  * POST /events/{event_type} - Publish events to other agents
  * GET /results/{task_id} - Retrieve agent processing results

STANDARD RULES:
1. Every spec_id must be OS-001, OS-002... sequential. Reset to OS-001 on every new run. No gaps.
2. endpoint_name must follow REST conventions: {HTTP_METHOD} /resource/{param}.
   http_method must be one of: GET, POST, PUT, PATCH, DELETE.
   contract_category must be one of: crud_endpoint, query_endpoint, event_endpoint,
   auth_endpoint, file_endpoint, integration_endpoint.
3. Every openapi_spec item must reference at least one REQ-### in req_id_refs. Only use
   REQ-### values present in structured_requirements.
4. For entity_refs: Use DM-### from data_design_model_and_strategy if available. If data model
   is empty, entity_refs can be an empty array (A2A contracts may not have entity refs).
5. No two openapi_spec items may share the same endpoint_name. If two requirements map to
   the same endpoint, create one item and include all REQ-### and DM-### refs.
6. Every output item must trace back to at least one REQ-### from structured_requirements.
7. Do not invent items that have no basis in the inputs.
8. If structured_requirements is empty → stop. Return error: "structured_requirements is empty"
9. If agent_interaction_diagram is empty → generate external contracts only (no A2A)

THOROUGHNESS REQUIREMENT:
- Count the number of agent nodes and edges in the agent_interaction_diagram
- Generate AT LEAST one A2A contract per major agent interaction
- For complex systems with orchestrators and multiple agents, expect 10-20+ A2A contracts
- Do NOT generate only 2-3 contracts when there are 10+ agent interactions visible in the diagram

OUTPUT FORMAT:
Return a valid JSON object with exactly these keys:
- "openapi_spec": array of endpoint contract objects, each with spec_id, endpoint_name,
  http_method, path, description, contract_category, request_schema, response_schema,
  req_id_refs, entity_refs
- "schema_registry": object with total_requirements_analysed, total_contracts_generated,
  uncovered_requirements, registry_summary, contracts_by_category, recommendation

Return only the JSON object. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Decision table

| Condition | Action |
|---|---|
| `structured_requirements` missing or empty | Stop. Report: `"structured_requirements is missing or empty"` |
| `agent_interaction_diagram` missing | Stop. Report: `"Required input 'agent_interaction_diagram' is missing"` |
| `agent_interaction_diagram` empty or unparseable | Generate external contracts only. Log warning. |
| `data_design_model_and_strategy` empty | Proceed. Allow `entity_refs: []` for A2A contracts. Log info. |
| `project_context` missing | Use default project context. Log warning. |
| `business_case` empty | Use "Not provided". Continue. |
| `constraints` absent | Proceed without constraint checks |
| Low confidence on any item | Flag item with `confidence: low`. Continue. |
| No output items produced | Stop. Report: `"no openapi_spec could be derived from inputs"` |
| Two requirements map to the same endpoint | Create one spec item. Include all REQ-### in `req_id_refs` and all DM-### in `entity_refs`. |
| Requirement describes a background process with no API surface | Skip in `openapi_spec`. Count as uncovered in `schema_registry`. |
| REQ has no API implication | Skip. Do not create a placeholder spec item. |
| DM-### entity has no corresponding REQ | Do not create an endpoint solely for an entity with no requirement. |
| `constraints` specifies auth (e.g. OAuth2, API key) | Reference the auth requirement in the relevant endpoint `description`. Classify as `auth_endpoint`. |
| `on_no_contracts_found` triggered | Return empty `openapi_spec` array and `schema_registry` with `recommendation: blocked`. |
| Agent diagram shows N agent interactions | Generate AT LEAST 1 A2A contract per major interaction (expect 10-20+ for complex systems) |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | All `openapi_spec` items have required fields | `spec_id`, `endpoint_name`, `http_method`, `path`, `description`, `contract_category`, `req_id_refs`, `entity_refs` all non-null |
| AC-02 | `spec_id` is sequential | OS-001, OS-002... no gaps |
| AC-03 | All `req_id_refs` are valid | Reference real REQ-### from `structured_requirements` |
| AC-04 | All `entity_refs` are valid | Reference real DM-### from `data_design_model_and_strategy`, or empty array if no data model or for A2A contracts |
| AC-05 | `recommendation` from allowed values | One of: `proceed \| review_required \| blocked` |
| AC-06 | No invented items | Every endpoint traceable to at least one REQ-###. Entity refs required only for external/data endpoints. |
| AC-07 | Output is valid JSON | Parseable, no trailing commas, no markdown fences |
| AC-08 | No duplicate endpoint names | All `endpoint_name` values unique across `openapi_spec` array |
| AC-09 | HTTP methods valid | Every `http_method` is one of: GET, POST, PUT, PATCH, DELETE |
| AC-10 | Contract categories valid | Every `contract_category` is one of the 6 values in `behaviour.contract_categories` |
| AC-11 | Registry counts consistent | `total_contracts_generated` equals length of `openapi_spec` array |
| AC-12 | A2A contracts generated | For systems with agent diagrams showing 10+ interactions, expect 10-20+ total contracts (including A2A) |

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `openapi_spec` | Yes | GenWiz reads this field by name |
| Renaming `schema_registry` | Yes | GenWiz reads this field for routing |
| Changing `recommendation` enum values | Yes | GenWiz `phase_transitions` config maps these |
| Renaming `spec_id` | Yes | Downstream agents reference contracts by ID |
| Changing `contract_categories` enum values | Yes | `contract_classifier` tool and `schema_registry.contracts_by_category` keys depend on these |
| Adding a new field to `openapi_spec` item | No | GenWiz doesn't parse item internals |
| Changing `request_schema` or `response_schema` structure | No | Internal to item — GenWiz ignores |

---

## Related files

| File | Purpose |
|---|---|
| `DE-04_Api_Contracts_Config.json` | Config — behaviour rules, inputs, outputs, shared folder I/O, supported formats |
| `DE-04_Api_Contracts_SKILL.md` | This file — LLM system prompt and reasoning rules |
| `ADLC_Tech_Stack_Config.json` | LLM defaults — on_gap, confidence_threshold, retry_attempts, shared_folder.base_path |

---

## A2A Compatibility

This agent is **A2A compatible**. It:
- Reads inputs from shared folder:
  * `{thread_id}/bs_docs/` - Business requirements (.json, .docx, .pdf, .html)
  * `{thread_id}/Business_Process_Agent_Interaction.html` or `.md` - Agent diagram (REQUIRED)
  * `{thread_id}/Business_Process_Agent_Network.html` or `.md` - Network diagram (optional)
  * `{thread_id}/brd_response/agent_architecture.json` - Architecture spec (optional)
- Writes outputs to shared folder: `{thread_id}/api_contracts_response/`
- Supports multiple input formats: JSON, DOCX, PDF, HTML, Markdown
- Writes **both JSON and DOCX** output files automatically
- Generates both external-facing and Agent-to-Agent (A2A) API contracts
- Uses `X-Thread-ID` header for shared folder routing
- Uses `X-Run-ID` header for execution tracking

**Output Behavior:**
- JSON file: Structured data for downstream agents (always written)
- DOCX file: Human-readable Word document with formatted tables (always written)
- HTTP response returns JSON by default (`?format=docx` returns DOCX in response)

---

*DE-04 · Api Contracts Agent · SKILL.md · v1.1.0 · 2026-05-15*
