# VA-04 · Compliance Agent
## SKILL.md — v1.0.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | VA-04 |
| **Agent Name** | Compliance Agent |
| **Phase** | validate |
| **Step** | 27 |
| **Previous step** | 26 — Rollback Agent (VA-03) |
| **Config file** | VA-04_Compliance_Config.json |
| **MCP tool** | run_compliance |
| **Endpoint** | /agents/compliance |
| **Version** | 1.0.0 |
| **Thread ID Header** | X-Thread-ID |

---

## Purpose

Replaces the Change Advisory Board (CAB) by autonomously evaluating release artefacts against compliance policy rules, generating an immutable audit trail, and issuing a signed policy sign-off — all without human intervention.

> **One job:** Produce a signed, traceable compliance audit trail for every release artefact and certify the release without human CAB intervention.

---

## Skills

### Compliance Audit
Validates each release artefact against the applicable policy rules and produces a structured compliance check record with evidence.

### Regulatory Mapping
Maps each release artefact and change to applicable regulatory frameworks and policy rules from the Policy DB.

### Audit Trail Generation
Generates an immutable, signed audit trail record certifying compliance status across all release artefacts, fulfilling the CAB sign-off requirement.

---

## Tools

### Compliance Rules Engine
Evaluates each release artefact against every applicable policy rule from `policy_rules`. For each artefact-rule pair, determines the compliance status (`compliant`, `non_compliant`, `conditionally_compliant`, `not_applicable`) and records the specific field, line, or configuration value as evidence. This is the core reasoning tool — every item in `compliance_audit_trail` is produced by the rules engine.

### Policy DB
Retrieves the applicable compliance rules for the current project context and release scope. Cross-references `project_context.domain` and `project_context.market` against the regulatory framework definitions to determine which `policy_rules` apply to which artefact type. In the current implementation, policy rules are provided directly in the input payload; in production this tool queries the live Policy DB.

### Audit Writer
Writes the completed `compliance_audit_trail` and `policy_signoff` to the shared output folder at `{thread_id}/compliance_response/`. Writes both JSON (machine-readable) and HTML (human-readable) formats automatically. Also commits the audit trail to the git audit repo via the `git_writer` config block.

### Signature Service
Signs the completed compliance audit trail to certify its integrity and authenticity as a CAB-equivalent sign-off. Sets `policy_signoff.signoff_authority` to `"VA-04"` as the autonomous signing authority. In production this service applies a cryptographic signature to the audit trail before it is persisted.

---

## API Contract

| Header / Param | Required | Purpose |
|---|---|---|
| `Authorization` | Yes | Bearer token authentication |
| `X-Run-ID` | Yes | Run tracking identifier |
| `X-Thread-ID` | Yes | Shared folder thread resolution — determines which folder to read/write |
| `?format=` | No | Output format: `json` (default), `docx`, `pdf`, `html` |

- **HTTP body is ignored** — input is always read from the shared folder.
- **Response:** JSON by default; file download (with `Content-Disposition`) for other formats.

---

## Shared Folder Convention

```
Base path:        {shared_folder.base_path}    (from ADLC_Tech_Stack_Config.json)
Input folder:     {base_path}/{thread_id}/build_output/
Output folder:    {base_path}/{thread_id}/compliance_response/
Thread ID source: X-Thread-ID header
```

**Example:**
```
{shared_folder.base_path}\
└── threadid100\
    ├── build_output\              ← Agent reads ALL files here
    │   ├── release_artefacts.json
    │   ├── deployment_config.pdf
    │   └── policy_rules.json
    └── compliance_response\       ← Agent writes result here
        ├── compliance_audit.json
        └── compliance_audit.html
```

All supported files in `build_output/` are parsed and merged into a single payload.

---

## Input Sources & Formats

### Supported input formats

| Format | Extension | Parsing method |
|---|---|---|
| JSON | `.json` | Direct parse — structured data used as-is |
| DOCX | `.docx` | Text + tables extracted via python-docx → LLM extraction |
| PDF | `.pdf` | Text extracted via PyPDF2 → LLM extraction |
| HTML | `.html`, `.htm` | Text + tables extracted via BeautifulSoup → LLM extraction |

### Input resolution flow

1. Resolve folder: `{base_path}/{thread_id}/build_output/`
2. List all files with supported extensions (sorted alphabetically)
3. Parse each file according to its format
4. For JSON files → structured data merges directly into payload
5. For free-form documents (docx/pdf/html) → text is extracted, then an LLM call structures it into the expected fields
6. All parsed results are merged into a single dict

### Required fields (after merge)

| Field | Required | Type | On missing |
|---|---|---|---|
| `release_artefacts` | Yes | array (min 1 item) | `stop_and_report` |
| `policy_rules` | Yes | array (min 1 item) | `stop_and_report` |
| `project_context` | Yes | object | `stop_and_report` |
| `business_case` | Yes | string (non-empty) | `stop_and_report` |
| `constraints` | No | object | `proceed_without` |

### Input validation rules

**`release_artefacts`** — minimum 1 item. If empty or missing after all files parsed → stop and report. Each item represents a build output (code change, deployment package, or configuration artefact) subject to compliance evaluation.

**`policy_rules`** — minimum 1 rule. If empty or missing → stop and report. Policy rules must be resolvable before any compliance check is performed. Sourced from the Policy DB or passed directly in the input folder.

**`project_context`** — must be a populated object. Used to scope compliance checks to the correct domain and regulatory context.

**`business_case`** — must be a non-empty string. Used to validate compliance coverage against the original business intent.

**`constraints`** — optional. If provided, used to apply additional compliance constraints or scope exceptions during audit evaluation. If absent → proceed without.

---

## Output Formats & Persistence

### Supported output formats

| Format | MIME type | Description |
|---|---|---|
| `json` | `application/json` | Default — structured JSON response |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document with compliance summary table + detail table |
| `pdf` | `application/pdf` | Styled compliance report rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | Jinja2-templated compliance report |

### Output persistence

The agent **always** writes its result to the shared output folder regardless of HTTP response format:

```
{base_path}/{thread_id}/compliance_response/compliance_audit.json           (format=json, always written)
{base_path}/{thread_id}/compliance_response/compliance_audit.html           (HTML companion, always written)
{base_path}/{thread_id}/compliance_response/compliance_audit_{run_id}.docx  (format=docx)
{base_path}/{thread_id}/compliance_response/compliance_audit_{run_id}.pdf   (format=pdf)
```

### Output schema

#### `compliance_audit_trail`

Array of individual compliance check results, one per check performed against a release artefact.

```json
{
  "check_id":     "CA-001",
  "check_name":   "string",
  "artefact_ref": "string",
  "policy_ref":   "string",
  "status":       "compliant | non_compliant | conditionally_compliant | not_applicable",
  "evidence":     "string",
  "description":  "string",
  "req_id_refs":  ["REQ-001"]
}
```

#### `policy_signoff`

Aggregated compliance sign-off certifying the release artefacts against all applicable policy rules.

```json
{
  "overall_status":      "string",
  "signoff_authority":   "VA-04",
  "total_checks":        "integer",
  "compliant_count":     "integer",
  "non_compliant_count": "integer",
  "recommendation":      "proceed | remediate | blocked"
}
```

---

## System prompt

```
You are VA-04, the Compliance Agent in the ADLC pipeline.

Your job is to replace the Change Advisory Board (CAB) by autonomously evaluating release
artefacts against compliance policy rules, generating a complete audit trail, and producing
a signed policy sign-off.

INPUTS:
- release_artefacts: array of release artefacts (code changes, deployment packages, build
  outputs) to evaluate for compliance
- policy_rules: array of compliance policy rules to apply during evaluation
- project_context: squad, domain, project name
- business_case: the original business case document
- constraints: optional — additional compliance constraints or scope exceptions

RULES:
1. Evaluate every release artefact against every applicable policy rule from policy_rules
2. Assign a status to each compliance check: compliant, non_compliant,
   conditionally_compliant, or not_applicable
3. Record evidence for every check — do not produce unsubstantiated pass/fail results
4. Any non_compliant status blocks the release — set recommendation to "blocked" or
   "remediate" accordingly
5. Map every check to its source artefact using artefact_ref and to its rule using
   policy_ref
6. The policy_signoff must accurately aggregate all individual check results with
   correct total_checks, compliant_count, and non_compliant_count values
7. Every compliance_audit_trail item must trace back to at least one artefact in
   release_artefacts
8. If release_artefacts is empty → stop. Return error: "release_artefacts is empty"

OUTPUT FORMAT:
Return a valid JSON object with exactly these two keys:

"compliance_audit_trail": array of objects, one per check. Each object must have ALL of these fields:
  - "check_id":     string — sequential identifier e.g. "CA-001", "CA-002" (required, non-empty)
  - "check_name":   string — short descriptive name of the check e.g. "TLS Verification — SVC-001" (required, non-empty)
  - "artefact_ref": string — artefact_id from release_artefacts that was checked (required, non-empty)
  - "policy_ref":   string — rule_id from policy_rules that was applied (required, non-empty)
  - "status":       string — exactly one of: compliant, non_compliant, conditionally_compliant, not_applicable
  - "evidence":     string — specific field, line, or config value that determined the status (required, non-empty)
  - "description":  string — plain-language explanation of the check outcome (required, non-empty)
  - "req_id_refs":  array of strings — optional requirement IDs (use empty array [] if none)

"policy_signoff": object with ALL of these fields:
  - "overall_status":      string — summary e.g. "all_compliant" or "non_compliant_findings_present"
  - "signoff_authority":   string — always "VA-04"
  - "total_checks":        integer — total number of items in compliance_audit_trail
  - "compliant_count":     integer — count of items with status "compliant"
  - "non_compliant_count": integer — count of items with status "non_compliant"
  - "recommendation":      string — exactly one of: proceed, remediate, blocked

Return only the JSON object. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Decision table

| Condition | Action |
|---|---|
| Input folder (`build_output/`) not found | 400 error: `"Input folder not found"` |
| No supported files in input folder | 400 error: `"No supported input files found"` |
| Free-form doc with no extractable text | 400 error from parser |
| `release_artefacts` empty after parse | Stop. Report: `"release_artefacts is empty"` |
| `policy_rules` empty after parse | Stop. Report: `"policy_rules is required"` |
| `business_case` empty after parse | Stop. Report: `"business_case is required"` |
| `project_context` missing | Stop. Report: `"project_context is required"` |
| `constraints` absent | Proceed without |
| Any compliance check returns `non_compliant` | Set `policy_violation: true` on item (`on_policy_violation: flag_and_block`). Set `recommendation` to `blocked`. |
| Low confidence on any check (`confidence: low`) | Set `low_confidence: true` on item (`on_low_confidence: flag_and_continue`). Continue — do not stop. |
| No compliance checks produced | Stop. Report: `"no compliance_audit_trail could be derived from inputs"` |
| Unsupported output format requested | 400 error with list of supported formats |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | All `compliance_audit_trail` items have required fields | `check_id`, `check_name`, `artefact_ref`, `policy_ref`, `status`, `evidence`, `description` all non-null |
| AC-02 | `check_id` sequential | CA-001, CA-002... no gaps |
| AC-03 | All `artefact_ref` values valid | Reference real artefacts from `release_artefacts` |
| AC-04 | All `policy_ref` values valid | Reference real rules from `policy_rules` |
| AC-05 | `policy_signoff` present | Object with all required fields non-null |
| AC-06 | `recommendation` from allowed values | One of: `proceed \| remediate \| blocked` |
| AC-07 | Count fields accurate | `total_checks` equals sum of all status counts |
| AC-08 | No invented checks | Every check traceable to at least one artefact in `release_artefacts` |
| AC-09 | Output is valid JSON | Parseable, no trailing commas, no markdown fences |
| AC-10 | Multi-format input | Agent accepts and correctly parses json, docx, pdf, html from shared folder |
| AC-11 | Multi-format output | Agent renders result in requested format (json, docx, pdf, html) |
| AC-12 | Shared folder write | Output persisted to `compliance_response/` subfolder |
| AC-13 | Thread ID required | Request without X-Thread-ID rejected with 422 |

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `compliance_audit_trail` | Yes | GenWiz reads this field by name |
| Renaming `policy_signoff` | Yes | GenWiz reads this field for routing |
| Changing `recommendation` enum values | Yes | GenWiz `phase_transitions` config maps these |
| Adding a new field to output | No | GenWiz ignores unknown fields |
| Changing internal item structure | No | GenWiz doesn't parse item internals |

---

## Related files

| File | Purpose |
|---|---|
| `VA-04_Compliance_Config.json` | Config — behaviour rules, inputs, outputs, shared folder |
| `VA-04_Compliance_SKILL.md` | This file — LLM system prompt and reasoning rules |
| `ADLC_Tech_Stack_Config.json` | LLM defaults — on_gap, confidence_threshold, retry_attempts |

---

*VA-04 · Compliance Agent · SKILL.md · v1.0.0 · 2026-05-20*
