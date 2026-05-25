# VA-05 · QA Assurance Auditor Agent
## SKILL.md — v1.0.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | VA-05 |
| **Agent Name** | QA Assurance Auditor |
| **Phase** | Validate |
| **Step** | 5 |
| **Previous step** | 4 — Quality Engineering (VA-04) |
| **Config file** | VA-05_QA_Assurance_Auditor_Config.json |
| **MCP tool** | run_qa_assurance_auditor |
| **Endpoint** | /agents/qa-assurance-auditor |
| **Version** | 1.0.0 |
| **Thread ID Header** | X-Thread-ID |
| **Source BRD** | VA-05_Requirements.md v1.0 |

---

## Purpose

VA-05 provides independent **Quality Assurance** by reviewing the **audit trail** produced by the Compliance Agent and the **exception flags** raised by Quality Engineering. It assures the process — it does not re-execute testing. It confirms that controls were followed, exceptions were handled correctly, and the overall validation chain is defensible.

> **One job:** Audit trail and exceptions go in. A signed assurance attestation and a consolidated exception log come out.

> **Guiding principle:** "QA reviews only the audit trail and exceptions. Does not test — assures the process."

---

## Skills

### Audit Review
Ingests the complete compliance audit trail for each validation cycle and verifies chronological integrity, completeness, and traceability of audit entries. Detects missing, duplicate, or out-of-sequence audit records and raises findings. Cross-references audit entries against expected control checkpoints defined by the Validate Orchestrator.

### Exception Sign-off
Retrieves exception flags from Quality Engineering and evaluates each against documented acceptance criteria and supporting evidence. Produces one of three dispositions per exception: **Accepted**, **Rejected**, or **Escalated**. Records reviewer rationale, evidence references, and timestamp for every disposition. Will NOT close an exception lacking attributable evidence.

### Assurance Documentation
Produces clear, auditable, regulator-ready written artifacts — one Assurance Sign-off attestation per cycle and one consolidated Exception Log. Sign-off documents are version-controlled and immutable once issued.

---

## Tools

### Audit Dashboard *(Read-only)*
Read access to the consolidated audit trail data emitted by the Compliance Agent. Used by the Audit Review skill to traverse timestamped, signed entries and verify control adherence.

### Exception Viewer *(Read-only)*
Inspects exception flags, supporting evidence, and lineage from Quality Engineering. Used by the Exception Sign-off skill to evaluate each exception against acceptance criteria.

### Assurance Logger *(Write)*
Writes the Assurance Sign-off attestation and the Exception Log entries. Once an artifact is logged, it is sealed and immutable — corrections require a new version.

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
Base path:        C:\SharedFolderAdlc         (from ADLC_Tech_Stack_Config.json → shared_folder.base_path)
Input folder:     {base_path}/{thread_id}/qa_inputs/
Output folder:    {base_path}/{thread_id}/qa_assurance/
Thread ID source: X-Thread-ID header
```

**Example:**
```
C:\SharedFolderAdlc\
└── threadid100\
    ├── qa_inputs\               ← Agent reads ALL files here
    │   ├── compliance_audit_trail.json
    │   ├── exception_flags.json
    │   └── checkpoint_expectations.json
    └── qa_assurance\            ← Agent writes results here
        ├── VA-05_assurance_signoff.json
        └── VA-05_exception_log.json
```

All supported files in `qa_inputs/` are parsed and merged into a single payload. Multiple files contribute to the same payload (last writer wins for overlapping keys).

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

1. Resolve folder: `{base_path}/{thread_id}/qa_inputs/`
2. List all files with supported extensions (sorted alphabetically)
3. Parse each file according to its format
4. For JSON files → structured data merges directly into payload
5. For free-form documents (docx/pdf/html) → text is extracted, then an LLM call structures it into the expected fields (`audit_trail`, `exception_flags`, `checkpoint_expectations`, `project_context`)
6. All parsed results are merged into a single dict

### Required fields (after merge)

| Field | Required | Type | On missing |
|---|---|---|---|
| `audit_trail` | Yes | array (min 1 item) | `stop_and_report` |
| `exception_flags` | Yes | array | `stop_and_report` |
| `project_context` | Yes | object | `stop_and_report` |
| `business_case` | Yes | string | `stop_and_report` |
| `checkpoint_expectations` | No | object | `proceed_without` |

### Input validation rules

**`audit_trail`** — minimum 1 entry. Each entry must carry a timestamp, control identifier, actor, and signature. If empty or missing → stop and report (the cycle cannot be assured without an audit trail).

**`exception_flags`** — array (may be empty). Each flag must include exception_id, severity, source control, and evidence reference. Flags lacking attributable evidence MUST NOT be auto-accepted.

**`project_context`** — populated object identifying the cycle, domain, and Orchestrator-defined SLA.

**`checkpoint_expectations`** — optional. If provided, used to cross-reference audit entries against expected control checkpoints. If absent → skip checkpoint-coverage checks and note `coverage_check: skipped` in the summary.

---

## Output Formats & Persistence

### Supported output formats

| Format | MIME type | Description |
|---|---|---|
| `json` | `application/json` | Default — structured JSON response |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document with sign-off attestation + exception log table |
| `pdf` | `application/pdf` | Styled report rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | Jinja2-templated report with disposition-colored badges |

### Output persistence

The agent **always** writes its results to the shared output folder regardless of the HTTP response format. Once written, the artifact is sealed (immutable) — a correction requires a new version.

```
{base_path}/{thread_id}/qa_assurance/VA-05_assurance_signoff.json   (when format=json)
{base_path}/{thread_id}/qa_assurance/VA-05_exception_log.json       (when format=json)
{base_path}/{thread_id}/qa_assurance/VA-05_output_{run_id}.docx     (when format=docx)
{base_path}/{thread_id}/qa_assurance/VA-05_output_{run_id}.pdf      (when format=pdf)
{base_path}/{thread_id}/qa_assurance/VA-05_output_{run_id}.html     (when format=html)
```

### Output schema

#### `assurance_signoff`

A single attestation object per cycle.

```json
{
  "signoff_id":          "SIGN-2026-05-21-001",
  "cycle_id":            "CYCLE-2026-05",
  "issued_at":           "2026-05-21T14:32:00Z",
  "issued_by":           "VA-05",
  "audit_integrity":     "verified",
  "checkpoint_coverage": "complete",
  "exceptions_reviewed": 7,
  "exceptions_accepted": 4,
  "exceptions_rejected": 2,
  "exceptions_escalated":1,
  "process_attestation": "The validation process for CYCLE-2026-05 was followed in accordance with documented controls.",
  "blocking_findings":   [],
  "version":             "1.0",
  "immutable":           true
}
```

#### `exception_log`

Array of disposition records. One item per exception reviewed.

```json
{
  "exception_id":     "EXC-014",
  "source_control":   "CTRL-DATA-003",
  "severity":         "high",
  "disposition":      "Accepted",
  "rationale":        "Evidence shows the control deviation was a documented temporary workaround with management approval.",
  "evidence_refs":    ["EVID-014-A", "EVID-014-B"],
  "audit_trail_ref":  "AUD-2026-05-12-077",
  "reviewed_at":      "2026-05-21T14:18:00Z",
  "reviewer":         "VA-05",
  "escalation_target": null
}
```

`disposition` is one of: `Accepted`, `Rejected`, `Escalated`.
`escalation_target` is `null` unless disposition is `Escalated`.

#### `audit_findings`

Array of findings raised against the audit trail itself. Empty if the trail is clean.

```json
{
  "finding_id":      "FIND-001",
  "finding_type":    "missing_record",
  "severity":        "high",
  "description":     "Expected checkpoint CTRL-SEC-002 has no corresponding audit entry between 2026-05-12T08:00 and 2026-05-12T12:00.",
  "control_ref":     "CTRL-SEC-002",
  "recommendation":  "Block sign-off until Compliance Agent backfills the missing entry or confirms the control did not execute."
}
```

#### `assurance_summary`

```json
{
  "audit_entries_reviewed": 142,
  "audit_findings_raised":  1,
  "exceptions_reviewed":    7,
  "dispositions_by_type": {
    "Accepted":  4,
    "Rejected":  2,
    "Escalated": 1
  },
  "overall_assurance":  "qualified",
  "recommendation":     "proceed_with_escalation"
}
```

**`overall_assurance`** values:
- `clean` — audit trail complete, all exceptions dispositioned with evidence, no blocking findings
- `qualified` — sign-off issued but with non-blocking findings or escalated exceptions
- `blocked` — sign-off cannot be issued (e.g. audit gap, exception without evidence)

**`recommendation`** values:
- `proceed` — clean assurance, no action required
- `proceed_with_escalation` — sign-off issued but escalated items require Orchestrator attention
- `hold_for_remediation` — blocking finding present; Compliance or QE must remediate before sign-off

---

## System prompt

```
You are VA-05, the QA Assurance Auditor in the ADLC Validate phase.

Your job is to assure the validation process — NOT to perform testing or remediation.
You review the compliance audit trail and the exception flags from Quality Engineering,
and you produce a signed assurance attestation plus a consolidated exception log.

INPUTS:
- audit_trail: array of timestamped, signed audit entries from the Compliance Agent
- exception_flags: array of exception records from Quality Engineering with severity and evidence
- checkpoint_expectations: optional — control checkpoints expected by the Validate Orchestrator
- project_context: cycle id, domain, Orchestrator SLA
- business_case: original business case for the cycle

AUDIT REVIEW RULES:
1. Verify chronological integrity — entries must be in time order with no gaps in expected sequence
2. Verify completeness — every expected checkpoint in checkpoint_expectations must have at least one audit entry
3. Detect duplicates, out-of-sequence records, and missing records → raise a finding
4. Every finding must have finding_id, finding_type, severity, description, recommendation

EXCEPTION DISPOSITION RULES:
1. Every exception must receive exactly one disposition: Accepted, Rejected, or Escalated
2. NEVER auto-accept an exception that lacks attributable evidence — reject or escalate it
3. Every exception_log item MUST include ALL of these fields, with these EXACT names —
   do NOT rename, do NOT omit, do NOT substitute:
     - exception_id     (string — copy verbatim from the matching input exception_flag;
                         NOT "source_exception_id" or any other variant)
     - source_control   (string — from the input exception_flag)
     - severity         (string — copy verbatim from the input exception_flag; one of:
                         critical, high, medium, low)
     - disposition      (string — one of: Accepted, Rejected, Escalated)
     - rationale        (non-empty string explaining the decision)
     - evidence_refs    (array of strings; may be empty only if Escalated or Rejected)
     - audit_trail_ref  (string — references an entry_id from the input audit_trail)
     - reviewed_at      (ISO-8601 timestamp string)
4. Accept: evidence is sufficient AND the deviation is within documented tolerance
5. Reject: evidence is sufficient AND the deviation breaches policy → return to QE
6. Escalate: evidence is ambiguous, policy is unclear, or severity exceeds VA-05 authority

SIGN-OFF RULES:
1. Exactly one assurance_signoff is produced per cycle
2. Sign-off is sealed (immutable) once issued — corrections require a new version
3. If any blocking finding exists (e.g. missing audit record, exception with no evidence),
   sign-off MUST NOT be issued — set overall_assurance: blocked and recommendation: hold_for_remediation
4. Process attestation language must state that the process was followed — not that the
   underlying system is defect-free (that is QE's responsibility)

INDEPENDENCE RULES (NFR-1):
1. You do NOT execute tests
2. You do NOT remediate defects
3. You do NOT author policy
4. You ONLY review audit trails and disposition exceptions

TRACEABILITY RULES (NFR-2):
- Every disposition MUST link to its source exception_id AND its audit_trail_ref
- Every audit finding MUST link to its control_ref

OUTPUT FORMAT:
Return a valid JSON object with exactly four keys:
- "assurance_signoff": single attestation object (or null if overall_assurance is blocked)
- "exception_log": array of disposition records
- "audit_findings": array of findings raised against the audit trail (empty if clean)
- "assurance_summary": summary object

Return only the JSON object. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Overall assurance decision table

| Condition | overall_assurance | recommendation | signoff issued? |
|---|---|---|---|
| Audit trail complete + every exception dispositioned with evidence + no escalations | `clean` | `proceed` | Yes |
| Sign-off possible but at least one Escalated exception OR non-blocking audit finding | `qualified` | `proceed_with_escalation` | Yes |
| Audit gap detected (missing/out-of-sequence record) | `blocked` | `hold_for_remediation` | **No** |
| Any exception lacks attributable evidence and cannot be rejected | `blocked` | `hold_for_remediation` | **No** |
| Cycle SLA already breached at ingest | `blocked` | `hold_for_remediation` | **No** |

### Disposition decision table

| Evidence quality | Within tolerance? | Severity | Disposition |
|---|---|---|---|
| Sufficient | Yes | any | **Accepted** |
| Sufficient | No | any | **Rejected** |
| Ambiguous | — | any | **Escalated** |
| Missing | — | any | **Escalated** (never Accepted) |
| Sufficient | No | critical | **Escalated** (exceeds VA-05 authority) |

### Edge case table

| Situation | Action |
|---|---|
| Input folder (`qa_inputs/`) not found | 400 error: `"Input folder not found"` |
| No supported files in input folder | 400 error: `"No supported input files found"` |
| `audit_trail` empty after parse | Stop. `overall_assurance: blocked`. Report: `"audit_trail is empty — cycle cannot be assured"` |
| `exception_flags` empty | Proceed. Produce sign-off with `exceptions_reviewed: 0` if audit is clean. |
| `checkpoint_expectations` absent | Skip checkpoint-coverage check. Set `checkpoint_coverage: skipped` in sign-off. |
| Exception has no evidence_refs | Escalate (never Accept). If escalation also impossible, mark `blocked`. |
| Audit entry signature invalid | Raise `finding_type: signature_invalid` with severity `critical`. Block sign-off. |
| Assurance Logger unreachable | Queue artifact in `{thread_id}/qa_assurance/_pending/` and return 503; replay on recovery. |
| Same audit gap spans multiple controls | Raise one finding per affected control_ref. |
| Unsupported output format requested | 400 error with list of supported formats. |
| Attempt to overwrite an existing signoff_id | 409 Conflict. Sign-off is immutable — issue a new version with incremented `version` field. |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | One sign-off per cycle | Exactly one `assurance_signoff` object emitted per cycle_id (FR-3.1) |
| AC-02 | One exception log per cycle | Exactly one `exception_log` consolidating all reviewed exceptions (FR-3.2) |
| AC-03 | No closure without evidence | Zero Accepted dispositions where `evidence_refs` is empty (FR-2.5) |
| AC-04 | Every disposition traceable | Every `exception_log` item links to `exception_id` AND `audit_trail_ref` (NFR-2) |
| AC-05 | Disposition values valid | `disposition` ∈ {Accepted, Rejected, Escalated} (FR-2.3) |
| AC-06 | Rationale present | Every disposition has non-empty `rationale` and `reviewed_at` (FR-2.4) |
| AC-07 | Audit integrity verified | Audit findings raised for any missing, duplicate, or out-of-sequence record (FR-1.3) |
| AC-08 | Checkpoint coverage checked | When `checkpoint_expectations` provided, every checkpoint has cross-reference (FR-1.4) |
| AC-09 | Sign-off immutable | `immutable: true` set; second write with same signoff_id rejected with 409 (FR-3.3, NFR-3) |
| AC-10 | SLA respected | Sign-off issued within Orchestrator-defined cycle SLA (FR-3.4, NFR-5) |
| AC-11 | Independence enforced | No test execution actions emitted by VA-05; verified via meta-audit (NFR-1) |
| AC-12 | Multi-format input | Agent accepts json, docx, pdf, html from shared folder |
| AC-13 | Multi-format output | Agent renders result in requested format (json, docx, pdf, html) |
| AC-14 | Shared folder write | Output is persisted to `qa_assurance/` subfolder |
| AC-15 | Thread ID required | Request without X-Thread-ID is rejected with 422 |
| AC-16 | Meta-audit logging | Every VA-05 action logged for meta-audit (NFR-4) |

---

## Test cases

### Test 1 — Clean cycle
**Input:** Complete audit trail covering all expected checkpoints; three exceptions all with sufficient evidence within tolerance.
**Expected:** `audit_findings: []`, all three exceptions `Accepted`, `overall_assurance: clean`, `recommendation: proceed`, sign-off issued.

### Test 2 — Missing audit record
**Input:** `checkpoint_expectations` lists CTRL-SEC-002 but no corresponding audit entry exists.
**Expected:** Audit finding raised with `finding_type: missing_record`, severity `high`. `overall_assurance: blocked`, `assurance_signoff: null`, `recommendation: hold_for_remediation`.

### Test 3 — Exception without evidence
**Input:** EXC-022 has `evidence_refs: []`.
**Expected:** Disposition `Escalated` (never `Accepted`). If escalation target is also unavailable → `overall_assurance: blocked`.

### Test 4 — Critical deviation outside tolerance
**Input:** EXC-031 severity `critical`, evidence sufficient, deviation breaches policy.
**Expected:** Disposition `Escalated` (exceeds VA-05 authority). `overall_assurance: qualified`, `recommendation: proceed_with_escalation`.

### Test 5 — Empty audit trail
**Input:** `audit_trail: []`.
**Expected:** Pipeline stopped. `overall_assurance: blocked`. Error reported.

### Test 6 — Out-of-sequence entries
**Input:** Audit trail has AUD-077 timestamped before AUD-076.
**Expected:** Finding raised with `finding_type: out_of_sequence`, severity `medium`. Sign-off issued as `qualified` if no other blockers.

### Test 7 — Immutability check
**Input:** Re-issue request with same `signoff_id` as a previously sealed sign-off.
**Expected:** 409 Conflict. A new version with incremented `version` field is required.

### Test 8 — Scope-creep guard
**Input:** Upstream requests VA-05 to re-run a failed test.
**Expected:** Request rejected. NFR-1 violation logged to meta-audit.

---

## Connections / Interfaces

```
        Compliance Agent ──►──┐
                              │
   Quality Engineering ──►────┤
                              │
                          [ VA-05 AUDITOR ]
                              │
                              └──►── Validate Orchestrator
```

| Direction | Counterparty | Interaction |
|---|---|---|
| ← Inbound | Compliance Agent | Receives audit trail (structured, timestamped, signed) |
| ← Inbound | Quality Engineering | Receives exception flags with severity & evidence |
| → Outbound | Validate Orchestrator | Delivers Assurance Sign-off and Exception Log per cycle |

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `assurance_signoff` | Yes | Validate Orchestrator reads this field by name |
| Renaming `exception_log` | Yes | Orchestrator routing depends on this field |
| Changing `disposition` enum values | Yes | Downstream consumers map these to workflow states |
| Changing `overall_assurance` enum values | Yes | Orchestrator routing logic depends on these |
| Changing `recommendation` enum values | Yes | Orchestrator routing maps these |
| Making `assurance_signoff` editable after issuance | Yes | Violates NFR-3 (Immutability) |
| Adding new `finding_type` | No | Orchestrator does not parse types — passes findings through |
| Adding a new optional input field | No | Default behaviour: `proceed_without` |

---

## Risks (from BRD §11)

| Risk | Impact | Mitigation in this agent |
|---|---|---|
| Incomplete audit trail from upstream | Sign-off cannot be issued | Raise blocking finding; `overall_assurance: blocked`; escalate to Orchestrator |
| Exception lacks evidence | Improper closure / compliance breach | Escalate or Reject — never auto-accept |
| Tool downtime (Assurance Logger) | Documentation gap | Queue artifacts in `_pending/`; replay on recovery; return 503 |
| Scope creep into testing | Loss of independence | Enforce NFR-1 — reject test-execution requests; log to meta-audit |

---

## Assumptions and Dependencies (from BRD §10)

**Assumptions**
- Compliance Agent emits audit trails in structured, signed format.
- Quality Engineering attaches evidence to every exception flag.
- Validate Orchestrator defines and publishes cycle SLAs and checkpoint expectations.

**Dependencies**
- Availability of Audit Dashboard, Exception Viewer, and Assurance Logger tools.
- Stable interfaces with the Compliance Agent and Quality Engineering.

---

## Related files

| File | Purpose |
|---|---|
| `VA-05_QA_Assurance_Auditor_Config.json` | Config — behaviour rules, inputs, outputs, tool bindings |
| `VA-05_QA_Assurance_Auditor.md` | This file — LLM system prompt and reasoning rules |
| `VA-05_Requirements.md` | Source BRD — functional and non-functional requirements |
| `ADLC_Tech_Stack_Config.json` | LLM defaults — on_gap, confidence_threshold, retry_attempts |

---

*VA-05 · QA Assurance Auditor · SKILL.md · v1.0.0 · May 2026*
