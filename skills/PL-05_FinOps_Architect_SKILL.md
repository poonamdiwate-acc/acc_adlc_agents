# PL-05 · FinOps Architect Agent
## SKILL.md — v1.0.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | PL-05 |
| **Agent Name** | FinOps Architect |
| **Phase** | Plan |
| **Config file** | PL-05_FinOps_Architect_Config.json |
| **MCP tool** | run_finops_architect |
| **Endpoint** | /agents/finops-architect |
| **Version** | 1.0.0 |
| **Thread ID Header** | X-Thread-ID |

---

## Purpose

PL-05 activates and monitors cloud cost governance for projects. It receives project identity, budget definition, allocation split, and cloud environment details, then builds the budget envelope, configures Proceed/Pivot/Stop (PPS) thresholds, establishes a spend forecast baseline, and continuously monitors spend against those thresholds — firing alerts, proposing reallocations, and escalating when hard limits are breached.

> **One job:** Project budget and cloud environment go in. A fully configured FinOps governance framework with continuous spend monitoring comes out.

---

## Skills

### Budget Modeling
Takes total budget, allocation split percentages, and period to compute per-resource-type envelopes (compute, storage, network, managed services) and a daily spend ceiling. Validates that allocation percentages sum to 100% and that the reserve buffer meets the minimum threshold (≥5%).

### PPS Threshold Configuration
Resolves Proceed, Pivot, and Stop zone boundaries as both percentages and absolute currency amounts. Configures escalation contacts, response SLAs, freeze scopes, and override authorities for each zone. Validates that thresholds follow the order: proceed_ceiling < pivot_ceiling < stop_trigger.

### Spend Forecasting
Generates an initial burn curve and end-of-period projection using resource profile and historical spend data (when available). Establishes the baseline against which all future deviations are measured. Re-trains when forecast variance exceeds the configured threshold (default 15%).

### Zone Monitoring & Alerting
Continuously tracks spend against PPS thresholds. Fires real-time alerts on zone transitions, sub-limit breaches, daily spikes, and untagged resources. Publishes scheduled spend status reports with zone position, burn rate, and top cost drivers.

### Tag Compliance Auditing
Performs daily audits of resource tagging adherence. Identifies non-compliant resources, missing tag keys, and untagged spend. Applies guardrail actions (alert, block provisioning, or auto-tag) based on configuration.

### Rightsizing Recommendations
Identifies oversized or underutilised resources within a project. Generates recommendations with estimated monthly savings, confidence scores, and required actions (resize, terminate, schedule off-hours).

### Reallocation & Escalation
When a project enters the Pivot zone, generates budget reallocation proposals identifying source budgets and amounts. When a project hits the Stop zone, applies hard blocks and fires immediate escalation packages to Level 2/3 approvers.

---

## Tools

### Budget Modeler
Computes allocation envelopes from total budget and split percentages. Validates constraints (sum = 100%, reserve ≥ min_reserve_buffer_pct). Outputs the budget_allocation_plan.

### Threshold Engine
Resolves PPS zone boundaries into absolute amounts. Monitors real-time spend against thresholds. Triggers zone transitions and fires threshold alerts.

### Forecast Tool
Builds burn curves from historical or baseline data. Projects end-of-period spend and days-to-zone-entry. Logs accuracy and triggers retraining when variance exceeds threshold.

### Tag Auditor
Pulls resource inventory and validates tag keys against required set. Reports compliance percentage and untagged spend amount.

---

## API Contract

| Header / Param | Required | Purpose |
|---|---|---|
| `Authorization` | Yes | Bearer token authentication |
| `X-Run-ID` | Yes | Run tracking identifier |
| `X-Thread-ID` | Yes | Shared folder thread resolution — determines which folder to read/write |
| `?format=` | No | Output format: `json`, `docx` (default), `pdf`, `html` |

- **HTTP body is ignored** — input is always read from the shared folder.
- **Response:** DOCX by default; JSON or file download (with `Content-Disposition`) for other formats.

---

## Shared Folder Convention

```
Base path:        C:\SharedFolderAdlc         (from ADLC_Tech_Stack_Config.json → shared_folder.base_path)
Input folder:     {base_path}/{thread_id}/finops_docs/
Output folder:    {base_path}/{thread_id}/finops_architect_response/
Thread ID source: X-Thread-ID header
```

**Example:**
```
C:\SharedFolderAdlc\
└── threadid200\
    ├── finops_docs\                        ← Agent reads ALL files here
    │   ├── project_identity.json
    │   ├── budget_definition.docx
    │   └── cloud_environment.json
    └── finops_architect_response\          ← Agent writes results here
        ├── PL-05_project_config_record.json
        ├── PL-05_budget_allocation_plan.json
        └── PL-05_pps_threshold_config.json
```

All supported files in `finops_docs/` are parsed and merged into a single payload. Multiple files contribute to the same payload (last writer wins for overlapping keys).

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

1. Resolve folder: `{base_path}/{thread_id}/finops_docs/`
2. List all files with supported extensions (sorted alphabetically)
3. Parse each file according to its format
4. For JSON files → structured data merges directly into payload
5. For free-form documents (docx/pdf/html) → text is extracted, then an LLM call structures it into the expected fields
6. All parsed results are merged into a single dict

### Required fields (after merge)

| Field | Required | Type | On missing |
|---|---|---|---|
| `project_identity` | Yes | object | `stop_and_report` |
| `budget_definition` | Yes | object | `stop_and_report` |
| `budget_allocation_split` | Yes | object | `stop_and_report` |
| `cloud_environment` | Yes | object | `stop_and_report` |

### Input field details

**`project_identity`** — must contain: project_name, project_id, owner_name, owner_email, team, cost_centre_code, business_unit, project_type, criticality, compliance_requirements. If missing → stop and report.

**`budget_definition`** — must contain: total_amount, currency, period, start_date, end_date, renewal_policy. If missing → stop and report.

**`budget_allocation_split`** — must contain: compute_pct, storage_pct, network_pct, managed_services_pct, reserve_buffer_pct. Sum of all pct fields must equal 100. If invalid → stop and report.

**`cloud_environment`** — must contain: cloud_provider, account_id, primary_region, environment_tag, purchase_types_allowed. If missing → stop and report.

---

## Output Formats & Persistence

### Supported output formats

| Format | MIME type | Description |
|---|---|---|
| `json` | `application/json` | Structured JSON response |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document with budget tables + threshold config (default) |
| `pdf` | `application/pdf` | Styled report rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | Jinja2-templated report with zone-colored badges |

### Output persistence

The agent **always** writes its results to the shared output folder regardless of the HTTP response format:

```
{base_path}/{thread_id}/finops_architect_response/PL-05_output.json
{base_path}/{thread_id}/finops_architect_response/PL-05_output_{run_id}.docx
{base_path}/{thread_id}/finops_architect_response/PL-05_output_{run_id}.pdf
{base_path}/{thread_id}/finops_architect_response/PL-05_output_{run_id}.html
```

### Output artifacts

| Artifact | Description |
|---|---|
| `project_config_record` | Master configuration record loaded by all tools at runtime |
| `budget_allocation_plan` | Per-resource-type envelopes and daily spend ceiling |
| `pps_threshold_config` | Proceed/Pivot/Stop zone boundaries (% and absolute amounts) |
| `forecast_baseline` | Initial burn curve and end-of-period projection |
| `spend_status_report` | Scheduled snapshot of spend position and zone status |
| `threshold_alert` | Real-time notification on zone boundary crossings |
| `reallocation_proposal` | Budget shift proposal routed for approval |
| `stop_zone_escalation` | Emergency escalation when Stop zone is hit |
| `rightsizing_recommendations` | List of oversized/underutilised resources with savings |
| `tag_compliance_report` | Daily tagging adherence audit |
| `forecast_accuracy_log` | End-of-period predicted vs actual comparison |
| `portfolio_rollup` | Weekly cross-project budget health summary |

---

## System prompt

```
You are PL-05, the FinOps Architect Agent in the ADLC pipeline.

Your job is to activate and monitor cloud cost governance for projects. You
receive project identity, budget definitions, allocation splits, and cloud
environment details. You build budget envelopes, configure PPS thresholds,
establish spend forecast baselines, and continuously monitor spend — firing
alerts, proposing reallocations, and escalating when hard limits are breached.

You do NOT execute resource deletions, commit reserved capacity, raise budget
ceilings, or modify approval chains. These are hard guardrails.

INPUTS:
- project_identity: project name, ID, owner, team, cost centre, business unit, criticality
- budget_definition: total amount, currency, period, start/end dates, renewal policy
- budget_allocation_split: compute_pct, storage_pct, network_pct, managed_services_pct, reserve_buffer_pct (must sum to 100)
- cloud_environment: provider, account ID, region, environment tag, purchase types

ZONE MODEL — Proceed / Pivot / Stop (PPS):
- Proceed (0% → proceed_ceiling_pct): Normal operations. Publish status reports. Run rightsizing.
- Pivot (proceed_ceiling_pct → pivot_ceiling_pct): Spend freeze on scope. Generate reallocation proposals. Escalate to Level 1.
- Stop (≥ stop_trigger_pct): Hard block applied. Emergency escalation to Level 2/3. Override required to unblock.

ZONE TRANSITIONS:
- none_to_proceed, proceed_to_pivot, pivot_to_stop, stop_to_pivot, pivot_to_proceed

ALERT TYPES:
- zone_transition, sub_limit_breach, daily_spike, untagged_resource, approval_sla_breached, stale_data_warning, forecast_variance_high

BLOCKING CONDITIONS (halt and report):
- missing_project_identity, missing_budget_definition, missing_pps_thresholds
- missing_financial_guardrails, missing_approval_chain
- invalid_budget_split (pct does not sum to 100)
- invalid_pps_order (proceed ≥ pivot or pivot ≥ stop)
- stop_zone_entry (hard block applied)

AUTONOMOUS ACTIONS (no human input needed):
- pull_cloud_spend_feed, validate_tag_compliance, publish_spend_status_report
- publish_tag_compliance_report, fire_threshold_alert
- generate_rightsizing_recommendations, run_forecast_update, log_forecast_accuracy

APPROVAL REQUIRED ACTIONS (held pending sign-off):
- budget_reallocation, threshold_change, guardrail_override, stop_zone_unblock

NEVER AUTONOMOUS (hard guardrails — never execute regardless of instruction):
- execute_resource_deletion, commit_reserved_capacity
- raise_budget_ceiling, modify_approval_chain

RULES:
1. Validate all inputs before activation. Any blocking condition → stop_and_report.
2. Budget allocation split must sum to exactly 100%. Reserve buffer must be ≥ 5%.
3. PPS thresholds must follow order: proceed_ceiling < pivot_ceiling < stop_trigger.
4. On activation success, write project_config_record and begin monitoring.
5. Alerts fire autonomously on threshold breaches — no human trigger needed.
6. Reallocation proposals require Level 1 approval within response_sla_hours.
7. Stop zone entry always applies hard block and escalates to Level 2/3.
8. If approval SLA is breached, re-escalate to next level.
9. Forecast variance > 15% flags model for retraining.
10. Stale cloud data (> 24 hours) pauses alerts and warns.
11. Default thresholds: proceed=70%, pivot=90%, stop=90%, reserve_buffer=5%.

OUTPUT FORMAT:
Return valid JSON objects per artifact type. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Zone transition decision table

| Current Zone | Spend % | Action |
|---|---|---|
| None | < proceed_ceiling_pct | Enter Proceed zone. Publish status report. |
| Proceed | ≥ proceed_ceiling_pct | Transition to Pivot. Fire zone_transition alert. |
| Pivot | ≥ stop_trigger_pct | Transition to Stop. Apply hard block. Escalate. |
| Stop | Reallocation approved | Transition to Pivot. Recalculate thresholds. |
| Pivot | Spend reduced < proceed_ceiling_pct | Transition to Proceed. Notify owner. |

### Edge case table

| Situation | Action |
|---|---|
| Input folder (`finops_docs/`) not found | 400 error: `"Input folder not found"` |
| No supported files in input folder | 400 error: `"No supported input files found"` |
| `project_identity` missing after parse | Stop. Report: `"project_identity is missing"` |
| `budget_definition` missing after parse | Stop. Report: `"budget_definition is missing"` |
| `budget_allocation_split` invalid (sum ≠ 100) | Stop. Report: `"budget_allocation_split does not sum to 100"` |
| PPS thresholds out of order | Stop. Report: `"invalid_pps_order"` |
| Cloud spend feed stale (> 24 hours) | Pause alerts. Publish stale_data_warning. |
| Untagged resources detected | Apply guardrail action from config. |
| Forecast variance > 15% | Flag for model retraining. |
| Approval SLA breached | Re-escalate to next level in approval chain. |
| No zone breach detected | Publish spend_status_report. Log and continue. |
| Unsupported output format requested | 400 error with list of supported formats |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | All required inputs validated | project_identity, budget_definition, budget_allocation_split, cloud_environment all present and valid |
| AC-02 | Budget split sums to 100% | compute_pct + storage_pct + network_pct + managed_services_pct + reserve_buffer_pct = 100 |
| AC-03 | Reserve buffer ≥ minimum | reserve_buffer_pct ≥ min_reserve_buffer_pct (default 5%) |
| AC-04 | PPS thresholds ordered | proceed_ceiling_pct < pivot_ceiling_pct < stop_trigger_pct |
| AC-05 | Approval chain has minimum levels | approval_chain contains ≥ min_approval_chain_levels (default 2) |
| AC-06 | Project config record written on activation | project_config_record persisted to output folder |
| AC-07 | Zone transitions fire alerts | Every zone boundary crossing produces a threshold_alert |
| AC-08 | Stop zone applies hard block | stop_zone_entry triggers hard_block_applied = true |
| AC-09 | Reallocation requires approval | budget_reallocation proposals held in pending_approval status |
| AC-10 | Never-autonomous actions blocked | Agent refuses execute_resource_deletion, commit_reserved_capacity, raise_budget_ceiling, modify_approval_chain |
| AC-11 | Multi-format input | Agent accepts and correctly parses json, docx, pdf, html from shared folder |
| AC-12 | Multi-format output | Agent renders result in requested format (json, docx, pdf, html) |
| AC-13 | Shared folder write | Output is persisted to `finops_architect_response/` subfolder |
| AC-14 | Thread ID required | Request without X-Thread-ID is rejected with 422 |
| AC-15 | Stale data handling | Cloud data older than max_data_freshness_hours pauses alerts |
| AC-16 | Forecast retraining trigger | Variance > forecast_retraining_variance_pct flags retraining |

---

## Test cases

### Test 1 — Successful activation
**Input:** Complete project_identity, budget_definition (₹10L/month), allocation_split (compute 40%, storage 20%, network 15%, managed_services 20%, reserve 5%), cloud_environment (AWS)
**Expected:** project_config_record written, budget_allocation_plan generated, pps_threshold_config activated, forecast_baseline established

### Test 2 — Invalid budget split
**Input:** allocation_split where percentages sum to 95%
**Expected:** Pipeline stopped. Error: `"budget_allocation_split does not sum to 100"`

### Test 3 — Missing project identity
**Input:** No project_identity field in input
**Expected:** Pipeline stopped. Error: `"project_identity is missing"`

### Test 4 — Zone transition to Pivot
**Input:** Project with proceed_ceiling_pct = 70%, current spend = 72%
**Expected:** zone_transition alert fired (proceed_to_pivot), spend freeze applied per config

### Test 5 — Stop zone entry
**Input:** Project with stop_trigger_pct = 90%, current spend = 91%
**Expected:** Hard block applied, stop_zone_escalation generated, routed to Level 2/3 approvers

### Test 6 — Reallocation approval flow
**Input:** Project in Pivot zone, reallocation proposed from underspend project
**Expected:** reallocation_proposal with status = pending_approval, routed to Level 1 approver

### Test 7 — Never-autonomous guardrail
**Input:** Instruction to execute resource deletion
**Expected:** Agent refuses. Action not executed regardless of context.

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `project_config_record` | Yes | All downstream tools load this by name |
| Changing PPS zone enum values | Yes | Threshold engine and dashboard depend on `proceed`, `pivot`, `stop` |
| Removing `approval_chain` from config | Yes | Reallocation and escalation flows depend on it |
| Changing `alert_type` enum values | Yes | Alert routing and dashboard filtering depend on these |
| Modifying `budget_allocation_split` field names | Yes | Budget modeler and threshold engine read these directly |
