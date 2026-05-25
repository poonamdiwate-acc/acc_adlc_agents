# VA-04 · Compliance Agent
## Requirements Specification Document — v1.0.0

---

## 1. Overview

| Field              | Value                                      |
|--------------------|--------------------------------------------|
| **Agent ID**       | VA-04                                      |
| **Agent Name**     | Compliance Agent                           |
| **Phase**          | validate                                   |
| **Step**           | 4 (within Validate phase)                  |
| **Previous Step**  | VA-03 — Test Coverage Agent                |
| **Next Step**      | VA-05 — Performance Validator Agent        |
| **Config File**    | `VA-04_Compliance_Config.json`             |
| **Skill File**     | `VA-04_Compliance_SKILL.md`                |
| **MCP Tool**       | `run_compliance_check`                     |
| **Endpoint**       | `/agents/compliance`                       |
| **Version**        | 1.0.0                                      |
| **Status**         | Deferred — planned for Iteration 2         |

---

## 2. Purpose

VA-04 is the compliance validation agent in the ADLC Validate phase. It accepts design artefacts, API contracts, data models, and architectural decisions produced by upstream Design agents, then validates them against applicable regulatory frameworks, internal governance policies, and security standards.

The agent produces a **Compliance Report** that identifies violations, assigns severity levels, maps each finding to the relevant regulatory clause, and emits an overall compliance recommendation that the Validate Orchestrator uses to gate the transition to the Build phase.

> **One job:** Design artefacts and regulatory requirements go in. A compliance report with findings, severity ratings, and a gate recommendation comes out.

---

## 3. Position in the ADLC Pipeline

```
VA-00 Validate Orchestrator
        │
        ├──► VA-01 Code Quality Agent
        │         │ code_quality_report
        │         ▼
        ├──► VA-02 Security Scanner Agent
        │         │ security_scan_report
        │         ▼
        ├──► VA-03 Test Coverage Agent
        │         │ test_coverage_report
        │         ▼
        ├──► VA-04 Compliance Agent          ◄── THIS AGENT
        │         │ compliance_report
        │         ▼
        ├──► VA-05 Performance Validator Agent
        │         │ performance_report
        │         ▼
        └──► VA-06 Validate Review Agent
                  │
                  ├── approved ──────────► Build phase
                  └── rejected ──────────► VA-01 (rework loop)
```

---

## 4. Skills

### 4.1 Regulatory Compliance Checker
Validates design artefacts against applicable regulatory frameworks (GDPR, HIPAA, PCI-DSS, SOC 2, ISO 27001). Maps each data entity, API endpoint, and architectural decision to the relevant regulatory clause and flags violations with evidence references.

### 4.2 Policy Validator
Checks outputs against internal Accenture/client governance policies — naming conventions, data classification rules, retention policies, cross-border data transfer restrictions. Reports policy gaps as findings with remediation guidance.

### 4.3 Compliance Report Builder
Aggregates all findings from the regulatory and policy checks into a structured compliance report. Assigns severity (critical / high / medium / low / info) to each finding, computes an overall compliance score, and sets the gate recommendation.

---

## 5. Tools

### 5.1 Regulatory Framework Mapper
Maps project-level regulatory requirements (from `structured_requirements`) to applicable standards. Produces a framework coverage matrix — which clauses apply, which artefacts they govern, and which artefacts are yet to be assessed.

### 5.2 Severity Classifier
Classifies each compliance finding by severity using a configurable risk matrix. Severity levels: `critical`, `high`, `medium`, `low`, `info`. Blocking threshold is configurable per framework.

### 5.3 Remediation Advisor
Generates actionable remediation guidance for each finding. References the violated clause, identifies the affected artefact, and proposes a specific corrective action.

---

## 6. Inputs

| Field                        | Required | Type     | Source                                                              | On Missing              |
|------------------------------|----------|----------|---------------------------------------------------------------------|-------------------------|
| `structured_requirements`    | Yes      | array    | Shared folder — `{thread_id}/bs_docs/` (json, docx, pdf, html)     | `stop_and_report`       |
| `api_contracts`              | Yes      | object   | Shared folder — `{thread_id}/api_contracts_response/`              | `stop_and_report`       |
| `data_design_model`          | Yes      | array    | Shared folder — `{thread_id}/data_design_response/`                | `stop_and_report`       |
| `regulatory_profile`         | Yes      | object   | Shared folder — `{thread_id}/bs_docs/` or config default           | `use_default_profile`   |
| `architecture_decisions`     | No       | object   | Shared folder — `{thread_id}/brd_response/agent_architecture.json` | `proceed_without`       |
| `security_scan_report`       | No       | object   | Shared folder — `{thread_id}/va02_response/`                       | `proceed_without`       |
| `project_context`            | No       | object   | Extracted from `bs_docs`                                           | `use_defaults`          |

### 6.1 Input Validation Rules

**`structured_requirements`** — array of REQ-### items from the Plan phase. Used to determine which regulatory frameworks apply (e.g., presence of PII data requirements triggers GDPR checks). Minimum 1 item. If missing → stop and report.

**`api_contracts`** — output of DE-04. Contains `openapi_spec` (array of OS-### endpoint definitions) and `schema_registry`. Used to check authentication patterns, data exposure, and transport security on each endpoint. If missing → stop and report.

**`data_design_model`** — output of DE-03. Contains `data_model` (array of DM-### entity definitions) with data classifications and storage locations. Used to check data residency, retention, and PII handling. If missing → stop and report.

**`regulatory_profile`** — object specifying which regulatory frameworks to enforce. If not present in `bs_docs`, the agent uses the `default_regulatory_profile` defined in config (see Section 10). Fields:
```json
{
  "frameworks":    ["GDPR", "SOC2"],
  "jurisdiction":  "EU",
  "data_classes":  ["PII", "financial"],
  "industry":      "financial_services"
}
```

**`architecture_decisions`** — optional agent architecture JSON from BRD phase. Provides context on communication patterns, trust boundaries, and authentication strategy that inform compliance findings.

**`security_scan_report`** — optional output of VA-02. If present, the compliance agent cross-references security findings with compliance obligations to avoid duplicate reporting and to elevate security findings that also constitute compliance violations.

---

## 7. Outputs

### 7.1 `compliance_report`

Top-level output object written to shared folder at `{thread_id}/compliance_response/compliance_report.json`.

```json
{
  "report_id":               "CR-001",
  "run_id":                  "string",
  "thread_id":               "string",
  "generated_at":            "ISO-8601 timestamp",
  "regulatory_frameworks":   ["GDPR", "SOC2", "PCI-DSS"],
  "total_artefacts_checked": 42,
  "findings":                [ /* array of CF-### items — see 7.2 */ ],
  "summary": {
    "total_findings":        12,
    "critical":              1,
    "high":                  3,
    "medium":                5,
    "low":                   2,
    "info":                  1,
    "compliance_score":      74,
    "coverage_matrix":       { /* framework → clause → status */ }
  },
  "recommendation":          "review_required"
}
```

### 7.2 Compliance Finding Item (`findings` array)

Each finding in the `findings` array follows this schema:

```json
{
  "finding_id":        "CF-001",
  "severity":          "high",
  "framework":         "GDPR",
  "clause":            "Article 32 — Security of processing",
  "artefact_type":     "api_endpoint",
  "artefact_ref":      "OS-007",
  "description":       "POST /users endpoint transmits PII fields without enforcing TLS 1.2+.",
  "evidence":          "openapi_spec[OS-007].request_schema contains 'email', 'dob' with no transport security constraint.",
  "remediation":       "Add transport security constraint to OS-007. Enforce TLS 1.2 minimum via API gateway policy.",
  "req_id_refs":       ["REQ-014"],
  "dm_id_refs":        ["DM-003"],
  "status":            "open"
}
```

| Field            | Type    | Description                                                                  |
|------------------|---------|------------------------------------------------------------------------------|
| `finding_id`     | string  | CF-### sequential, unique per report                                         |
| `severity`       | enum    | `critical \| high \| medium \| low \| info`                                  |
| `framework`      | string  | Applicable regulatory framework (GDPR, HIPAA, PCI-DSS, SOC2, ISO-27001)     |
| `clause`         | string  | Specific clause or control reference within the framework                    |
| `artefact_type`  | enum    | `api_endpoint \| data_entity \| architecture_decision \| configuration`      |
| `artefact_ref`   | string  | Reference ID (OS-###, DM-###, or free text for arch decisions)               |
| `description`    | string  | Plain-language description of the violation                                  |
| `evidence`       | string  | Exact location and content in the input artefact that constitutes the breach |
| `remediation`    | string  | Specific, actionable corrective action                                       |
| `req_id_refs`    | array   | REQ-### items that this finding relates to                                   |
| `dm_id_refs`     | array   | DM-### entities affected                                                     |
| `status`         | enum    | `open \| waived \| resolved` — always `open` in initial report               |

### 7.3 `recommendation` Values

| Value              | Meaning                                                                      |
|--------------------|------------------------------------------------------------------------------|
| `proceed`          | No critical or high findings. Safe to continue to Build phase.               |
| `review_required`  | Medium or low findings present. Human review recommended before proceeding.  |
| `blocked`          | One or more critical or high findings. Build phase must not start.           |

---

## 8. Behaviour Rules

| Condition                                                    | Action                                                                                  |
|--------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| `structured_requirements` missing or empty                   | Stop. Report: `"structured_requirements is missing or empty"`                           |
| `api_contracts` missing                                      | Stop. Report: `"api_contracts output from DE-04 is required"`                           |
| `data_design_model` missing                                  | Stop. Report: `"data_design_model output from DE-03 is required"`                       |
| `regulatory_profile` missing                                 | Use `default_regulatory_profile` from config. Log info.                                 |
| `architecture_decisions` missing                             | Proceed without architecture-level checks. Log info.                                    |
| `security_scan_report` missing                               | Proceed without cross-referencing security findings. Log info.                          |
| No applicable regulatory framework found                     | Return empty `findings` array. Set `recommendation: proceed`. Log warning.              |
| One or more `critical` findings                              | Set `recommendation: blocked`                                                           |
| One or more `high` findings, no `critical`                   | Set `recommendation: blocked` (configurable — can be `review_required` via config)      |
| Only `medium` / `low` / `info` findings                      | Set `recommendation: review_required`                                                   |
| No findings across all artefacts                             | Set `recommendation: proceed`. Compliance score: 100.                                   |
| PII data entity found with no GDPR framework in profile      | Auto-add GDPR to active frameworks. Log warning.                                        |
| Financial data entity found with no PCI-DSS in profile       | Auto-add PCI-DSS to active frameworks. Log warning.                                     |
| `on_low_confidence` triggered on any finding                 | Flag finding with `confidence: low`. Include in report. Continue.                       |
| Two requirements map to the same violation                   | Create one finding. Include all REQ-### in `req_id_refs`.                               |

---

## 9. Acceptance Criteria

| #     | Criterion                          | Pass Condition                                                                                              |
|-------|------------------------------------|-------------------------------------------------------------------------------------------------------------|
| AC-01 | All findings have required fields  | `finding_id`, `severity`, `framework`, `clause`, `artefact_type`, `artefact_ref`, `description`, `remediation` all non-null |
| AC-02 | `finding_id` is sequential         | CF-001, CF-002 … no gaps, reset per run                                                                     |
| AC-03 | Severity from allowed values       | One of: `critical \| high \| medium \| low \| info`                                                         |
| AC-04 | `recommendation` aligns with findings | `blocked` if any critical/high present; `review_required` for medium/low; `proceed` if none               |
| AC-05 | All `req_id_refs` are valid        | Reference real REQ-### from `structured_requirements`                                                       |
| AC-06 | All `dm_id_refs` are valid         | Reference real DM-### from `data_design_model`, or empty array                                              |
| AC-07 | All `artefact_ref` values are valid| Reference real OS-### from `api_contracts.openapi_spec`, or real DM-###, or valid arch decision ID         |
| AC-08 | Output is valid JSON               | Parseable, no trailing commas, no markdown fences                                                           |
| AC-09 | `compliance_score` is integer 0–100| Derived as: `((total_artefacts_checked - critical_weight) / total_artefacts_checked) * 100`                |
| AC-10 | Summary counts are consistent      | `total_findings` = sum of all severity counts                                                               |
| AC-11 | PII auto-framework trigger works   | GDPR added automatically when PII data entities detected and GDPR not in profile                            |
| AC-12 | Output written to shared folder    | `{thread_id}/compliance_response/compliance_report.json` and `.docx` both written                          |

---

## 10. Configuration

The agent is controlled by `VA-04_Compliance_Config.json`. Key configuration blocks:

### 10.1 LLM Config Override
```json
"llm_config_override": {
  "max_tokens": 16384,
  "timeout_seconds": 120
}
```

### 10.2 Behaviour Settings
```json
"behaviour": {
  "on_empty_requirements":    "stop_and_report",
  "on_missing_api_contracts": "stop_and_report",
  "on_missing_data_model":    "stop_and_report",
  "on_low_confidence":        "flag_and_continue",
  "blocking_severities":      ["critical", "high"],
  "default_regulatory_profile": {
    "frameworks":  ["GDPR", "SOC2"],
    "jurisdiction": "EU",
    "data_classes": ["PII"],
    "industry":    "general"
  },
  "severity_weights": {
    "critical": 10,
    "high":      5,
    "medium":    2,
    "low":       1,
    "info":      0
  }
}
```

### 10.3 Supported Regulatory Frameworks
```json
"supported_frameworks": [
  "GDPR",
  "HIPAA",
  "PCI-DSS",
  "SOC2",
  "ISO-27001",
  "CCPA",
  "NIST-CSF"
]
```

---

## 11. Data Flow

### 11.1 Inputs from Shared Folder

```
{thread_id}/
├── bs_docs/                            ← structured_requirements, regulatory_profile
│   └── requirements.json / .docx / .pdf / .html
├── api_contracts_response/             ← api_contracts (DE-04 output)
│   └── api_contracts_design.json
├── data_design_response/               ← data_design_model (DE-03 output)
│   └── data_design_model_and_strategy.json
├── va02_response/                      ← security_scan_report (VA-02 output, optional)
│   └── security_scan_report.json
└── brd_response/                       ← architecture_decisions (optional)
    └── agent_architecture.json
```

### 11.2 Outputs to Shared Folder

```
{thread_id}/
└── compliance_response/
    ├── compliance_report.json          ← machine-readable (always written)
    └── compliance_report.docx          ← human-readable Word document (always written)
```

---

## 12. API Contract

### 12.1 Endpoint

```
POST /agents/compliance
```

### 12.2 Headers

| Header          | Required | Description                                         |
|-----------------|----------|-----------------------------------------------------|
| `X-Thread-ID`   | Yes      | Identifies the shared folder thread to read/write   |
| `X-Run-ID`      | Yes      | Execution tracking ID for audit trail               |
| `Authorization` | Yes      | Bearer token                                        |
| `Content-Type`  | No       | `application/json` (body is optional — reads from shared folder) |

### 12.3 Query Parameters

| Parameter | Values          | Default | Description                       |
|-----------|-----------------|---------|-----------------------------------|
| `format`  | `json` / `docx` | `json`  | Response format for HTTP reply    |

### 12.4 Response — Success (200)

```json
{
  "status":            "success",
  "agent_id":          "VA-04",
  "run_id":            "string",
  "thread_id":         "string",
  "compliance_report": { /* full compliance_report object */ },
  "output_path":       "{thread_id}/compliance_response/compliance_report.json"
}
```

### 12.5 Response — Failure (400 / 500)

```json
{
  "status":    "error",
  "agent_id":  "VA-04",
  "run_id":    "string",
  "thread_id": "string",
  "error":     "structured_requirements is missing or empty"
}
```

---

## 13. Git Integration

### 13.1 Git Reader
```json
"git_reader": {
  "enabled":           false,
  "read_path_pattern": "runs/{run_id}/validate/VA-04_output.json",
  "phase":             "validate"
}
```

### 13.2 Git Writer
```json
"git_writer": {
  "write_path_pattern": "runs/{run_id}/validate/compliance_report.json",
  "commit_msg_pattern": "feat(VA-04): Compliance Agent · {run_id}"
}
```

---

## 14. Breaking Changes

| Change                                      | Breaking? | Reason                                                     |
|---------------------------------------------|-----------|------------------------------------------------------------|
| Renaming `compliance_report`                | Yes       | VA-00 and VA-06 read this field by name                    |
| Renaming `recommendation`                   | Yes       | VA-00 `phase_transitions` config maps these values         |
| Changing `recommendation` enum values       | Yes       | VA-00 routing logic depends on exact values                |
| Renaming `finding_id`                       | Yes       | Downstream agents and human reviewers reference by ID      |
| Changing `severity` enum values             | Yes       | `blocking_severities` config and `severity_classifier` tool depend on these |
| Adding a new field to a finding item        | No        | VA-00 does not parse finding internals                     |
| Changing `evidence` or `remediation` text   | No        | Consumed by humans only                                    |

---

## 15. Related Files

| File                                   | Purpose                                                          |
|----------------------------------------|------------------------------------------------------------------|
| `VA-04_Compliance_Config.json`         | Config — behaviour rules, inputs, outputs, shared folder I/O    |
| `VA-04_Compliance_SKILL.md`            | SKILL.md — LLM system prompt and reasoning rules                 |
| `ADLC_Tech_Stack_Config.json`          | LLM defaults — model, confidence_threshold, shared_folder path  |
| `skills/regulatory/`                   | Regulatory framework definition files (GDPR, HIPAA, PCI-DSS…)  |

---

## 16. A2A Compatibility

This agent is **A2A compatible**. It:

- Reads inputs from shared folder using `X-Thread-ID` header routing
- Writes outputs to shared folder: `{thread_id}/compliance_response/`
- Supports multiple input formats: JSON, DOCX, PDF, HTML
- Writes both JSON and DOCX output files automatically
- Uses `X-Run-ID` header for execution tracking and audit trail
- Can be triggered by VA-00 Orchestrator or called directly via REST

---

## 17. Open Questions / Deferred Decisions

| # | Question                                                                                     | Owner     | Status   |
|---|----------------------------------------------------------------------------------------------|-----------|----------|
| 1 | Should `blocking_severities` include `high` by default, or only `critical`?                  | Architect | Open     |
| 2 | Which regulatory framework files will be stored under `skills/regulatory/`?                  | Compliance Team | Open |
| 3 | Should the agent support custom policy rules defined per client/project?                      | Product   | Open     |
| 4 | Is a human-in-the-loop interrupt needed before VA-06 if `recommendation: blocked`?           | Architect | Open     |
| 5 | Should `compliance_score` formula be weighted by severity or a simple pass/fail ratio?       | Architect | Open     |

---

*VA-04 · Compliance Agent · Requirements Specification · v1.0.0 · 2026-05-20*
