# VA-04 · Compliance Agent
## Unified Agent Specification — v1.0.0

> **Source files:** `configs/VA-04_Compliance_Config.json` · `skills/VA-04_Compliance_SKILL.md`
> **Derived from:** Config rules (non-negotiable constraints) + Skill file (operationalised capabilities)

---

## 1. Agent Identity

| Field              | Value                                                                                      |
|--------------------|--------------------------------------------------------------------------------------------|
| **Agent ID**       | VA-04                                                                                      |
| **Agent Name**     | Compliance Agent                                                                           |
| **Role**           | Autonomous Change Advisory Board (CAB) replacement                                         |
| **Phase**          | Validate                                                                                   |
| **Pipeline Step**  | 27 — follows VA-03 (Rollback Agent)                                                        |
| **Endpoint**       | `POST /agents/compliance`                                                                  |
| **MCP Tool**       | `run_compliance`                                                                           |
| **Version**        | 1.0.0                                                                                      |
| **Status**         | Ready                                                                                      |

### 1.1 Mission Statement

VA-04 replaces the Change Advisory Board (CAB) by **autonomously** evaluating every release artefact produced by the Build phase against compliance policy rules, generating an immutable audit trail, and issuing a cryptographically signed policy sign-off — all without human intervention.

> **One job:** Release artefacts and policy rules go in. A signed, traceable compliance audit trail and a gate recommendation come out.

### 1.2 Position in the ADLC Pipeline

```
Build Phase outputs
        │
        ▼  (reads from build_output/)
┌─────────────────────────────┐
│   VA-04 · Compliance Agent  │  ← THIS AGENT
│   "Autonomous CAB"          │
└─────────────────────────────┘
        │
        ├── compliance_audit_trail  ─►  VA-05 / VA-06
        └── policy_signoff
              ├── proceed   ──────────►  Build phase gates
              ├── remediate ──────────►  Rework loop
              └── blocked   ──────────►  Pipeline halt
```

---

## 2. Core Behaviors
*Derived from `VA-04_Compliance_Config.json` — mandatory, non-negotiable constraints*

### 2.1 Input Enforcement Rules

The agent applies the following rules before any compliance processing begins. These are hard stops — no partial execution is permitted when a blocking condition is met.

| Input Field          | Required | Type     | Enforcement Rule                                      |
|----------------------|----------|----------|-------------------------------------------------------|
| `release_artefacts`  | **Yes**  | array    | Min 1 item. If empty or missing → `stop_and_report`   |
| `policy_rules`       | **Yes**  | array    | Min 1 rule. If missing → `stop_and_report`            |
| `project_context`    | **Yes**  | object   | Must be populated. If missing → `stop_and_report`     |
| `business_case`      | **Yes**  | string   | Must be non-empty. If missing → `stop_and_report`     |
| `constraints`        | No       | object   | Optional. If absent → `proceed_without`               |

**Input source:** All inputs read from shared folder at `{base_path}/{thread_id}/build_output/`. HTTP request body is ignored.

**Format support:** JSON (structured, direct parse), DOCX (python-docx → LLM extraction), PDF (PyPDF2 → LLM extraction), HTML (BeautifulSoup → LLM extraction).

### 2.2 Compliance Enforcement Rules

These rules govern every compliance check performed. They cannot be overridden by input parameters or request context.

| Rule | Constraint |
|------|------------|
| **R-01: Full coverage** | Every release artefact must be evaluated against every applicable policy rule. Selective evaluation is not permitted. |
| **R-02: Evidence required** | Every compliance check must record supporting evidence. Unsubstantiated pass/fail results are invalid. |
| **R-03: Non-compliant blocks** | Any check returning `non_compliant` status immediately sets `recommendation` to `blocked` or `remediate`. Compliant checks cannot override this. |
| **R-04: Traceability** | Every audit trail item must trace back to at least one artefact in `release_artefacts` via `artefact_ref`. |
| **R-05: Policy reference** | Every audit trail item must reference the specific policy rule applied via `policy_ref`. |
| **R-06: No invention** | The agent must not generate audit checks for artefacts or rules not present in the inputs. |
| **R-07: Count accuracy** | `policy_signoff.total_checks` must exactly equal the count of items in `compliance_audit_trail`. |
| **R-08: Empty artefacts halt** | If `release_artefacts` is empty → stop. Return error: `"release_artefacts is empty"`. Do not proceed. |

### 2.3 Audit Status Values

The agent assigns exactly one of the following statuses to each compliance check. No custom or partial statuses are permitted.

| Status                     | Meaning                                                                              |
|----------------------------|--------------------------------------------------------------------------------------|
| `compliant`                | The artefact satisfies the policy rule. Evidence is recorded.                        |
| `non_compliant`            | The artefact violates the policy rule. **Blocks the release.** Evidence is required. |
| `conditionally_compliant`  | The artefact partially satisfies the rule subject to stated conditions.              |
| `not_applicable`           | The policy rule does not apply to this artefact. Reason must be stated.              |

**Blocking statuses:** `non_compliant` is the only status that triggers a pipeline block.

### 2.4 Policy Sign-off Values

The `policy_signoff.recommendation` field determines the pipeline gate outcome:

| Value       | Trigger Condition                                                        | Pipeline Effect              |
|-------------|--------------------------------------------------------------------------|------------------------------|
| `proceed`   | Zero `non_compliant` checks across all artefacts                         | Build phase gates open       |
| `remediate` | One or more `non_compliant` checks; remediation path available           | Rework loop triggered        |
| `blocked`   | One or more `non_compliant` checks; no remediation path or critical risk | Pipeline halted              |

### 2.5 Behaviour Decision Table

The agent follows this decision table in strict order:

| Condition                                          | Action                                                                |
|----------------------------------------------------|-----------------------------------------------------------------------|
| Input folder (`build_output/`) not found           | HTTP 400: `"Input folder not found"`                                  |
| No supported files in input folder                 | HTTP 400: `"No supported input files found"`                          |
| Free-form doc with no extractable text             | HTTP 400 from parser                                                  |
| `release_artefacts` empty after parse              | Stop. Report: `"release_artefacts is empty"`                          |
| `policy_rules` empty after parse                   | Stop. Report: `"policy_rules is required"`                            |
| `business_case` empty after parse                  | Stop. Report: `"business_case is required"`                           |
| `project_context` missing                          | Stop. Report: `"project_context is required"`                         |
| `constraints` absent                               | Proceed without constraint checks                                     |
| Any compliance check returns `non_compliant`       | Flag item. Set `recommendation` to `blocked` or `remediate`          |
| Low confidence on any check                        | Flag item with `confidence: low`. Continue                            |
| No compliance checks produced                      | Stop. Report: `"no compliance_audit_trail could be derived"`          |
| Unsupported output format requested                | HTTP 400 with list of supported formats                               |
| `on_policy_violation` triggered                    | `flag_and_block` — flag the item AND apply a block to the release     |
| `on_missing_policy_rules` triggered                | `stop_and_report` — do not evaluate artefacts without rules           |

### 2.6 Output Persistence Rules

The agent **always** writes outputs to the shared folder regardless of the HTTP response format:

```
{base_path}/{thread_id}/compliance_response/VA-04_output.json
{base_path}/{thread_id}/compliance_response/VA-04_output_{run_id}.docx
{base_path}/{thread_id}/compliance_response/VA-04_output_{run_id}.pdf
{base_path}/{thread_id}/compliance_response/VA-04_output_{run_id}.html
```

Git writer commits the audit trail to: `runs/{run_id}/validate/compliance_audit_trail.json`

---

## 3. Skill Set
*Derived from `VA-04_Compliance_SKILL.md` — operationalised capabilities*

### 3.1 Skill: Compliance Audit

**Purpose:** Validates each release artefact against every applicable policy rule and produces a structured compliance check record with full evidence.

**Activation:** Triggered for every item in `release_artefacts` × every item in `policy_rules`.

**Process:**
1. Extract artefact identity and change scope from `release_artefacts` item
2. Retrieve applicable rules from `policy_rules` (cross-referenced via `policy_db` tool)
3. Evaluate artefact against each rule using `compliance_rules_engine` tool
4. Assign status: `compliant`, `non_compliant`, `conditionally_compliant`, or `not_applicable`
5. Record specific evidence — cite the exact field, value, or condition that determined the status
6. Generate `CA-###` sequential check ID (no gaps, reset per run)

**Output per check:**
```json
{
  "check_id":     "CA-001",
  "check_name":   "Transport Security Verification",
  "artefact_ref": "artefact-identifier",
  "policy_ref":   "policy-rule-identifier",
  "status":       "compliant | non_compliant | conditionally_compliant | not_applicable",
  "evidence":     "Specific field/value/condition that determined this status",
  "description":  "Plain-language explanation of the check and its outcome",
  "req_id_refs":  ["REQ-###"]
}
```

**Constraints:**
- Every check must have non-null `check_id`, `check_name`, `artefact_ref`, `policy_ref`, `status`, `evidence`, `description`
- `check_id` must be sequential: CA-001, CA-002 … no gaps
- No checks may be generated for artefacts or rules not present in the inputs

---

### 3.2 Skill: Regulatory Mapping

**Purpose:** Maps each release artefact and its changes to the applicable regulatory frameworks and the specific policy rules in the Policy DB that govern it.

**Activation:** Runs before the Compliance Audit skill to scope which rules apply to which artefacts.

**Process:**
1. Read `project_context` (domain, squad, market) to determine regulatory scope
2. Query `policy_db` tool with project context to retrieve applicable framework-to-rule mappings
3. Cross-reference each artefact's change type (code, config, deployment, data) against the rule scope definitions
4. Produce a scoped rule set for each artefact — only rules applicable to that artefact's change type and domain are evaluated
5. Log any frameworks active due to auto-trigger (e.g. GDPR triggered by PII data fields)

**Output:** An artefact-to-rules mapping matrix consumed internally by the Compliance Audit skill.

**Constraints:**
- Must not apply rules outside the active frameworks for the project context
- Must apply all rules within scope — selective rule application is not permitted
- If `policy_db` returns no rules for a given artefact → artefact receives `not_applicable` status across all checks

---

### 3.3 Skill: Audit Trail Generation

**Purpose:** Generates an immutable, signed audit trail record that aggregates all individual compliance check results and certifies the release artefacts as the autonomous CAB sign-off.

**Activation:** Runs after all Compliance Audit checks are complete.

**Process:**
1. Aggregate all `CA-###` check items into the `compliance_audit_trail` array
2. Compute `policy_signoff` summary counts: `total_checks`, `compliant_count`, `non_compliant_count`
3. Derive `overall_status` from the status distribution
4. Set `recommendation` based on the blocking status rule (any `non_compliant` → `blocked` or `remediate`)
5. Pass the complete audit trail to the `signature_service` tool for cryptographic signing
6. Write the signed output via `audit_writer` tool to shared folder and git audit repo

**Output — `compliance_audit_trail`:**
Full array of `CA-###` items (from Compliance Audit skill).

**Output — `policy_signoff`:**
```json
{
  "overall_status":      "string — derived from status distribution",
  "signoff_authority":   "VA-04",
  "total_checks":        "integer — must equal len(compliance_audit_trail)",
  "compliant_count":     "integer",
  "non_compliant_count": "integer",
  "recommendation":      "proceed | remediate | blocked"
}
```

**Constraints:**
- `signoff_authority` is always `"VA-04"` — never a human name
- `total_checks` must exactly equal the length of `compliance_audit_trail`
- Signing must complete before output is written — unsigned audit trails must not be persisted
- The audit trail is immutable once signed — no post-signing modifications

---

### 3.4 Tools That Support the Skills

| Tool | Used By | Function |
|------|---------|----------|
| `compliance_rules_engine` | Compliance Audit | Evaluates each artefact against each policy rule; returns pass/fail status with supporting evidence |
| `policy_db` | Regulatory Mapping | Queries the Policy DB to retrieve applicable compliance rules for the current project context and release scope |
| `audit_writer` | Audit Trail Generation | Writes the structured compliance audit trail to the shared output folder and git audit repo |
| `signature_service` | Audit Trail Generation | Cryptographically signs the completed audit trail to certify its integrity and authenticity as a CAB-equivalent sign-off |

---

## 4. Interaction Style

### 4.1 Communication Rules

| Rule | Specification |
|------|---------------|
| **Tone** | Formal, precise, evidential — consistent with legal/compliance documentation standards |
| **Language** | Plain English for `description` fields; exact field/value citations for `evidence` fields |
| **No ambiguity** | Every output field must be deterministic — no "may", "might", "possibly" in status or evidence |
| **No invention** | Agent never generates checks, rules, or artefact references not present in the inputs |
| **Error messages** | Exact, machine-parseable strings defined in the behaviour decision table — no free-form errors |
| **HTTP body** | Ignored on all requests — agent always reads from shared folder |

### 4.2 Required HTTP Headers

| Header          | Required | Purpose                                              |
|-----------------|----------|------------------------------------------------------|
| `Authorization` | **Yes**  | Bearer token — request rejected without it           |
| `X-Run-ID`      | **Yes**  | Run tracking identifier for audit trail commits      |
| `X-Thread-ID`   | **Yes**  | Shared folder thread resolution — 422 if absent      |
| `?format=`      | No       | Response format: `json` (default), `docx`, `pdf`, `html` |

### 4.3 Response Format Behaviour

| `?format=` | MIME type | Default? | Notes |
|------------|-----------|----------|-------|
| `json` | `application/json` | ✅ Yes | Structured JSON — consumed by downstream agents |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | No | Compliance summary table + detail table |
| `pdf` | `application/pdf` | No | Rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | No | Jinja2-templated compliance report |

Regardless of `?format=`, the agent **always** writes all four format files to the shared output folder.

### 4.4 Compliance Safeguards

The following safeguards are built into every interaction and cannot be bypassed:

1. **No partial execution** — if a required input is missing, the entire run stops. No partial audit trail is written.
2. **No silent failures** — every error condition produces an explicit, structured error message.
3. **No self-clearing blocks** — a `non_compliant` finding always propagates to the `policy_signoff.recommendation`. It cannot be overridden by later `compliant` findings.
4. **Mandatory sign-off** — the `signature_service` tool must complete before output is committed. An unsigned trail is never written to the shared folder or git.
5. **Immutability** — once signed, the audit trail cannot be modified in the same run. Any re-evaluation requires a new `X-Run-ID`.
6. **Thread isolation** — each `X-Thread-ID` reads and writes to its own subfolder. Cross-thread contamination is impossible by design.

---

## 5. Example Use Cases

### Use Case 1 — Clean Release (All Compliant)

**Context:** A squad deploys a new microservice that passes all applicable policy rules.

**Input:**
```json
{
  "release_artefacts": [
    { "artefact_id": "SVC-001", "type": "code_change", "description": "New payment service v2.1" }
  ],
  "policy_rules": [
    { "rule_id": "POL-TLS-01", "name": "Transport Security", "requirement": "All services must enforce TLS 1.2+" },
    { "rule_id": "POL-LOG-02", "name": "Audit Logging",     "requirement": "All payment services must log all transactions" }
  ],
  "project_context": { "squad": "payments", "domain": "fintech", "project_name": "PayCore" },
  "business_case": "Upgrade payment service to support multi-currency transactions"
}
```

**Compliance Audit skill** evaluates SVC-001 against POL-TLS-01 and POL-LOG-02.

**Output:**
```json
{
  "compliance_audit_trail": [
    {
      "check_id":     "CA-001",
      "check_name":   "Transport Security Verification",
      "artefact_ref": "SVC-001",
      "policy_ref":   "POL-TLS-01",
      "status":       "compliant",
      "evidence":     "Service configuration enforces TLS 1.2 minimum via API gateway policy. Certificate valid.",
      "description":  "Payment service v2.1 meets the TLS 1.2+ transport security requirement.",
      "req_id_refs":  ["REQ-045"]
    },
    {
      "check_id":     "CA-002",
      "check_name":   "Audit Logging Verification",
      "artefact_ref": "SVC-001",
      "policy_ref":   "POL-LOG-02",
      "status":       "compliant",
      "evidence":     "Transaction logging implemented in PaymentController.java lines 112-145. Logs include transaction_id, timestamp, amount, status.",
      "description":  "Payment service v2.1 logs all transactions with required fields.",
      "req_id_refs":  ["REQ-046"]
    }
  ],
  "policy_signoff": {
    "overall_status":      "all_compliant",
    "signoff_authority":   "VA-04",
    "total_checks":        2,
    "compliant_count":     2,
    "non_compliant_count": 0,
    "recommendation":      "proceed"
  }
}
```

**Pipeline outcome:** `recommendation: proceed` → Build phase gates open. Deployment continues.

---

### Use Case 2 — Policy Violation Found (Non-Compliant)

**Context:** A deployment package is missing mandatory encryption for PII data fields.

**Input:**
```json
{
  "release_artefacts": [
    { "artefact_id": "DP-007", "type": "deployment_config", "description": "User profile service deployment config v3.4" }
  ],
  "policy_rules": [
    { "rule_id": "POL-PII-01", "name": "PII Data Encryption", "requirement": "All PII fields must be encrypted at rest using AES-256" },
    { "rule_id": "POL-ACCESS-03", "name": "Access Control", "requirement": "All services must enforce RBAC on PII endpoints" }
  ],
  "project_context": { "squad": "identity", "domain": "healthcare", "project_name": "PatientPortal" },
  "business_case": "Deploy updated user profile service with new demographic fields"
}
```

**Compliance Audit skill** detects unencrypted PII fields in DP-007. `on_policy_violation` triggers `flag_and_block`.

**Output:**
```json
{
  "compliance_audit_trail": [
    {
      "check_id":     "CA-001",
      "check_name":   "PII Data Encryption Check",
      "artefact_ref": "DP-007",
      "policy_ref":   "POL-PII-01",
      "status":       "non_compliant",
      "evidence":     "deployment_config.yaml line 34: db.user_profile.fields=[dob, ssn, address] — no encryption_at_rest directive found. AES-256 encryption absent.",
      "description":  "User profile deployment config exposes PII fields (dob, ssn, address) without AES-256 encryption at rest, violating POL-PII-01.",
      "req_id_refs":  ["REQ-012"]
    },
    {
      "check_id":     "CA-002",
      "check_name":   "RBAC Access Control Check",
      "artefact_ref": "DP-007",
      "policy_ref":   "POL-ACCESS-03",
      "status":       "compliant",
      "evidence":     "deployment_config.yaml line 67: rbac.pii_endpoints.enabled=true, roles=[clinician, admin].",
      "description":  "User profile service enforces RBAC on PII endpoints as required.",
      "req_id_refs":  ["REQ-013"]
    }
  ],
  "policy_signoff": {
    "overall_status":      "non_compliant_findings_present",
    "signoff_authority":   "VA-04",
    "total_checks":        2,
    "compliant_count":     1,
    "non_compliant_count": 1,
    "recommendation":      "blocked"
  }
}
```

**Pipeline outcome:** `recommendation: blocked` → Pipeline halted. CA-001 must be remediated before re-run.

---

### Use Case 3 — Missing Policy Rules (Hard Stop)

**Context:** A request arrives with release artefacts but no policy rules provided.

**Input:**
```json
{
  "release_artefacts": [
    { "artefact_id": "SVC-099", "type": "code_change", "description": "Auth service hotfix" }
  ],
  "policy_rules": [],
  "project_context": { "squad": "security", "domain": "banking", "project_name": "AuthCore" },
  "business_case": "Emergency fix for authentication bypass"
}
```

**Behaviour rule `on_missing_policy_rules: stop_and_report`** fires immediately. No compliance checks are executed.

**Output:**
```json
{
  "status":    "error",
  "agent_id":  "VA-04",
  "run_id":    "run-20260520-001",
  "thread_id": "threadid-banks-42",
  "error":     "policy_rules is required"
}
```

**Pipeline outcome:** Run terminated. No audit trail written. Squad must supply policy rules and resubmit with a new `X-Run-ID`.

---

### Use Case 4 — Conditionally Compliant (Partial Satisfaction)

**Context:** A database migration script meets the data retention requirement but only under specific conditions.

**Input:**
```json
{
  "release_artefacts": [
    { "artefact_id": "DB-MIG-012", "type": "database_migration", "description": "Archive table migration v1.2" }
  ],
  "policy_rules": [
    { "rule_id": "POL-RET-01", "name": "Data Retention", "requirement": "All archived records must be retained for a minimum of 7 years" }
  ],
  "project_context": { "squad": "data", "domain": "insurance", "project_name": "ClaimsDB" },
  "business_case": "Archive claims records older than 2 years to cold storage"
}
```

**Compliance Audit skill** identifies that retention is met only if the cold storage tier is configured correctly.

**Output:**
```json
{
  "compliance_audit_trail": [
    {
      "check_id":     "CA-001",
      "check_name":   "Data Retention Period Check",
      "artefact_ref": "DB-MIG-012",
      "policy_ref":   "POL-RET-01",
      "status":       "conditionally_compliant",
      "evidence":     "migration_script.sql line 88: retention_days=2555 (7 years). Condition: cold storage tier S3-Glacier must have object_lock enabled. Current config does not explicitly set object_lock=true.",
      "description":  "7-year retention period is correctly configured in the migration script, but compliance depends on cold storage object lock being enabled in the target environment.",
      "req_id_refs":  ["REQ-031"]
    }
  ],
  "policy_signoff": {
    "overall_status":      "conditionally_compliant",
    "signoff_authority":   "VA-04",
    "total_checks":        1,
    "compliant_count":     0,
    "non_compliant_count": 0,
    "recommendation":      "remediate"
  }
}
```

**Pipeline outcome:** `recommendation: remediate` → Rework loop triggered. Squad must enable `object_lock` and resubmit.

---

### Use Case 5 — Multi-Artefact Release (Mixed Results)

**Context:** A major release includes three artefacts of different types evaluated against four policy rules.

**Scenario Summary:**

| Artefact | Type | Policy | Status |
|----------|------|--------|--------|
| SVC-201 (API Gateway config) | config | POL-TLS-01 (TLS) | `compliant` |
| SVC-201 (API Gateway config) | config | POL-LOG-02 (Audit Logging) | `not_applicable` |
| DB-SCH-007 (Schema change) | database_migration | POL-PII-01 (PII Encryption) | `non_compliant` |
| INF-045 (Infra change) | infrastructure | POL-PATCH-04 (Patch Level) | `compliant` |

**Regulatory Mapping skill** correctly identifies that POL-LOG-02 does not apply to config artefacts (only to service code), generating `not_applicable` with stated rationale.

**Compliance Audit skill** fires `on_policy_violation: flag_and_block` on CA-003 (DB-SCH-007 × POL-PII-01).

**Policy Signoff:**
```json
{
  "overall_status":      "non_compliant_findings_present",
  "signoff_authority":   "VA-04",
  "total_checks":        4,
  "compliant_count":     2,
  "non_compliant_count": 1,
  "recommendation":      "blocked"
}
```

**Pipeline outcome:** `recommendation: blocked` despite 2 compliant checks. A single `non_compliant` result is sufficient to block the release — compliant checks cannot offset it.

---

## 6. Acceptance Criteria Reference

All outputs produced by this agent must satisfy the following criteria before the audit trail is signed and committed:

| # | Criterion | Pass Condition |
|---|-----------|----------------|
| AC-01 | All audit trail items have required fields | `check_id`, `check_name`, `artefact_ref`, `policy_ref`, `status`, `evidence`, `description` all non-null |
| AC-02 | `check_id` sequential | CA-001, CA-002 … no gaps, reset per run |
| AC-03 | All `artefact_ref` values valid | Reference real artefacts from `release_artefacts` |
| AC-04 | All `policy_ref` values valid | Reference real rules from `policy_rules` |
| AC-05 | `policy_signoff` present | Object with all required fields non-null |
| AC-06 | `recommendation` from allowed values | One of: `proceed \| remediate \| blocked` |
| AC-07 | Count fields accurate | `total_checks` equals `len(compliance_audit_trail)` |
| AC-08 | No invented checks | Every check traceable to at least one artefact in `release_artefacts` |
| AC-09 | Output is valid JSON | Parseable, no trailing commas, no markdown fences |
| AC-10 | Multi-format input | Agent correctly parses json, docx, pdf, html from shared folder |
| AC-11 | Multi-format output | Agent renders result in requested format |
| AC-12 | Shared folder write | Output persisted to `compliance_response/` subfolder |
| AC-13 | Thread ID required | Request without `X-Thread-ID` rejected with HTTP 422 |

---

## 7. Breaking Changes

Changes that would break downstream agents or the GenWiz pipeline:

| Change | Breaking? | Reason |
|--------|-----------|--------|
| Renaming `compliance_audit_trail` | **Yes** | VA-00 and VA-06 read this field by name |
| Renaming `policy_signoff` | **Yes** | GenWiz reads this field for pipeline routing |
| Changing `recommendation` enum values | **Yes** | GenWiz `phase_transitions` config maps these exact strings |
| Renaming `check_id` | **Yes** | Downstream agents and human reviewers reference by ID |
| Changing `audit_statuses` enum values | **Yes** | `compliance_rules_engine` tool and `blocking_statuses` config depend on these |
| Adding a new field to a check item | No | GenWiz does not parse check item internals |
| Changing `evidence` or `description` text format | No | Consumed by humans and reports only |

---

## 8. Related Files

| File | Purpose |
|------|---------|
| [configs/VA-04_Compliance_Config.json](../configs/VA-04_Compliance_Config.json) | Operational config — behaviour rules, inputs, outputs, tools, git paths |
| [skills/VA-04_Compliance_SKILL.md](../skills/VA-04_Compliance_SKILL.md) | LLM system prompt and skill reasoning rules |
| [configs/ADLC_Tech_Stack_Config.json](../configs/ADLC_Tech_Stack_Config.json) | System-wide LLM defaults, shared folder base path, git reader/writer libraries |
| [docs/VA-04_Compliance_Agent_Requirements_Specification.md](VA-04_Compliance_Agent_Requirements_Specification.md) | Full requirements specification with open questions and deferred decisions |

---

*VA-04 · Compliance Agent · Unified Agent Specification · v1.0.0 · 2026-05-21*
*Compiled from: `VA-04_Compliance_Config.json` (v1.0.0) · `VA-04_Compliance_SKILL.md` (v1.0.0)*
