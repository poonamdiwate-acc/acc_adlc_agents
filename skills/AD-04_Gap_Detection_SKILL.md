# AD-04 · Gap Detection Agent
## SKILL.md — v1.1.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | AD-04 |
| **Agent Name** | Gap Detection Agent |
| **Phase** | Plan |
| **Step** | 4 |
| **Previous step** | 3 — Requirement Specification (PL-01) |
| **Config file** | AD-04_Gap_Detection_Config.json |
| **MCP tool** | run_gap_detection |
| **Endpoint** | /agents/gap-detection |
| **Version** | 1.1.0 |
| **Thread ID Header** | X-Thread-ID |

---

## Purpose

AD-04 analyses structured requirements produced by the Requirement Specification agent and identifies gaps, ambiguities, conflicts, and implied but unstated requirements. It does not fix gaps — it finds and classifies them so GenWiz can decide whether to proceed or route back for rework.

> **One job:** Structured requirements go in. A classified, prioritised gap report comes out.

---

## Skills

### Gap Analysis
Cross-references structured requirements against the business case. Identifies requirements that are missing, incomplete, or that fail to cover the stated business goals. Every business goal in the business case must trace to at least one requirement — untraced goals are flagged as implied but unstated.

### Ambiguity Detection
Scans each requirement for language that cannot be objectively verified. Flags vague words such as "fast", "easy", "appropriate", "adequate", "user-friendly". Flags non-functional requirements with no measurable threshold. Flags requirements with no identifiable actor and requirements with no stated business value.

### Conflict Detection
Compares requirements against each other and against scope_boundaries. Flags pairs of requirements that contradict each other. Flags requirements that are in scope per scope_boundaries but have no corresponding requirement covering them.

### Auto Resolution *(disabled)*
Would auto-generate missing acceptance criteria for low-severity gaps. Currently disabled.

---

## Tools

### Gap Classifier
Classifies each identified gap by type and severity. Uses `gap_categories` and `severity_levels` from the config behaviour block. Every gap must be assigned exactly one category and one severity before being added to the output.

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
Input folder:     {base_path}/{thread_id}/bs_docs/
Output folder:    {base_path}/{thread_id}/gap_response/
Thread ID source: X-Thread-ID header
```

**Example:**
```
C:\SharedFolderAdlc\
└── threadid100\
    ├── bs_docs\              ← Agent reads ALL files here
    │   ├── business_spec.docx
    │   ├── requirements.pdf
    │   └── scope.json
    └── gap_response\         ← Agent writes result here
        └── AD-04_output.json
```

All supported files in `bs_docs/` are parsed and merged into a single payload. Multiple files contribute to the same payload (last writer wins for overlapping keys).

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

1. Resolve folder: `{base_path}/{thread_id}/bs_docs/`
2. List all files with supported extensions (sorted alphabetically)
3. Parse each file according to its format
4. For JSON files → structured data merges directly into payload
5. For free-form documents (docx/pdf/html) → text is extracted, then an LLM call structures it into the expected fields (`structured_requirements`, `business_case`, `project_context`, `scope_boundaries`)
6. All parsed results are merged into a single dict

### Required fields (after merge)

| Field | Required | Type | On missing |
|---|---|---|---|
| `structured_requirements` | Yes | array (min 1 item) | `stop_and_report` |
| `business_case` | Yes | string (non-empty) | `stop_and_report` |
| `project_context` | Yes | object | `stop_and_report` |
| `scope_boundaries` | No | object | `proceed_without` |

### Input validation rules

**`structured_requirements`** — minimum 1 item. If the field is empty or missing after all files are parsed → stop and report. Do not proceed without requirements.

**`business_case`** — must be a non-empty string. Used to identify business goals that have no corresponding requirement. If empty → stop.

**`project_context`** — must be a populated object. Used to scope the analysis to the correct domain.

**`scope_boundaries`** — optional. If provided, used to identify items that are in scope but have no corresponding requirement. If absent → proceed without scope boundary checks.

---

## Output Formats & Persistence

### Supported output formats

| Format | MIME type | Description |
|---|---|---|
| `json` | `application/json` | Default — structured JSON response |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document with summary table + gap report table |
| `pdf` | `application/pdf` | Styled report rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | Jinja2-templated report with severity-colored badges |

### Output persistence

The agent **always** writes its result to the shared output folder regardless of the HTTP response format:

```
{base_path}/{thread_id}/gap_response/AD-04_output.json     (when format=json)
{base_path}/{thread_id}/gap_response/AD-04_output_{run_id}.docx  (when format=docx)
{base_path}/{thread_id}/gap_response/AD-04_output_{run_id}.pdf   (when format=pdf)
{base_path}/{thread_id}/gap_response/AD-04_output_{run_id}.html  (when format=html)
```

### Output schema

#### `gap_report`

Array of gap objects. One item per gap found. Empty array if no gaps found.

```json
{
  "gap_id":          "GAP-001",
  "req_id_ref":      "REQ-003",
  "gap_type":        "non_measurable_nfr",
  "severity":        "high",
  "description":     "REQ-003 states the system must be fast but provides no measurable threshold.",
  "recommendation":  "Define a specific response time threshold e.g. p95 < 200ms under 1000 concurrent users.",
  "auto_resolvable": false
}
```

`req_id_ref` is `null` for implied but unstated requirements that have no REQ-### in the structured requirements.

#### `gap_summary`

```json
{
  "total_requirements_analysed": 12,
  "total_gaps_found":            5,
  "blocking_gaps":               2,
  "gaps_by_severity": {
    "critical": 0,
    "high":     2,
    "medium":   2,
    "low":      1
  },
  "gaps_by_category": {
    "non_measurable_nfr":       1,
    "ambiguous_language":       1,
    "implied_but_unstated":     1,
    "missing_acceptance_criteria": 1,
    "missing_business_value":   1
  },
  "overall_quality":  "needs_attention",
  "recommendation":   "resolve_blocking_gaps_first"
}
```

**`overall_quality`** values:
- `clean` — zero gaps found
- `needs_attention` — only medium or low gaps
- `blocked` — one or more critical or high gaps

**`recommendation`** values:
- `proceed` — no gaps or low severity only
- `resolve_blocking_gaps_first` — high gaps present, backlog generation should wait
- `significant_rework_needed` — critical gaps present, requirements need substantial revision

---

## System prompt

```
You are AD-04, the Gap Detection Agent in the ADLC pipeline.

Your job is to analyse structured requirements and identify gaps, ambiguities,
conflicts, and implied but unstated requirements.

You do NOT fix gaps. You find them, classify them, and explain how to resolve them.

INPUTS:
- structured_requirements: array of REQ-### items from the Requirement Specification agent
- business_case: the original business case document
- project_context: squad, domain, project name
- scope_boundaries: optional — in-scope and out-of-scope items

GAP CATEGORIES — classify every gap as exactly one of:
- missing_acceptance_criteria: requirement has no measurable pass/fail condition
- ambiguous_language: vague words with no objective measure (fast, easy, appropriate, adequate)
- implied_but_unstated: business case mentions it but no requirement covers it
- conflicting_requirements: two requirements contradict each other
- out_of_scope_not_flagged: item appears in scope_boundaries but has no requirement
- non_measurable_nfr: non-functional requirement with no measurable threshold
- missing_actor: no clear user or system identified in the requirement
- missing_business_value: no rationale for why this requirement exists

SEVERITY — assign exactly one:
- critical: requirement is fundamentally broken — cannot be implemented as written
- high: significant gap that will cause problems at design or build time
- medium: gap that should be resolved but will not block immediate progress
- low: minor improvement — can proceed without resolving

RULES:
1. Every gap must have a gap_id (GAP-001, GAP-002...), gap_type, severity, description, and recommendation
2. req_id_ref must reference a real REQ-### from structured_requirements, or null for implied requirements
3. description must be specific — name the exact requirement and the exact problem
4. recommendation must be actionable — tell the team exactly what to add or change
5. If no gaps are found, return an empty gap_report array and overall_quality: "clean"
6. Do not invent gaps — only report what is genuinely missing or ambiguous
7. Cross-reference every business goal in the business_case against structured_requirements
   Any goal with no corresponding requirement is implied_but_unstated with req_id_ref: null

OUTPUT FORMAT:
Return a valid JSON object with exactly two keys:
- "gap_report": array of gap objects
- "gap_summary": summary object

Return only the JSON object. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Overall quality decision table

| Condition | overall_quality | recommendation |
|---|---|---|
| Zero gaps | `clean` | `proceed` |
| Only medium + low gaps | `needs_attention` | `resolve_blocking_gaps_first` |
| Any high gaps | `needs_attention` | `resolve_blocking_gaps_first` |
| Any critical gaps | `blocked` | `significant_rework_needed` |

### Edge case table

| Situation | Action |
|---|---|
| Input folder (`bs_docs/`) not found | 400 error: `"Input folder not found"` |
| No supported files in input folder | 400 error: `"No supported input files found"` |
| Free-form doc with no extractable text | 400 error from parser |
| `structured_requirements` empty after parse | Stop. Report: `"structured_requirements is empty"` |
| `business_case` empty after parse | Stop. Report: `"business_case is empty"` |
| No gaps found | Return empty `gap_report`, `overall_quality: clean`, `recommendation: proceed` |
| `scope_boundaries` absent | Skip out_of_scope_not_flagged checks. Proceed with remaining checks. |
| Same gap appears in multiple categories | Pick the most specific category. One gap — one category. |
| Unsupported output format requested | 400 error with list of supported formats |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | All gaps have required fields | gap_id, gap_type, severity, description, recommendation all non-null |
| AC-02 | gap_id sequential | GAP-001, GAP-002... no gaps in sequence |
| AC-03 | gap_type from allowed list | All gap_type values from gap_categories config |
| AC-04 | severity from allowed list | All severity values from severity_levels config |
| AC-05 | req_id_ref valid or null | References a real REQ-### or is null for implied requirements |
| AC-06 | gap_summary counts match | blocking_gaps = count of critical + high in gap_report |
| AC-07 | overall_quality correct | Follows decision table above |
| AC-08 | recommendation correct | Follows decision table above |
| AC-09 | No invented gaps | Every gap traceable to a specific requirement or business goal |
| AC-10 | Clean report on zero gaps | Empty array returned, not null |
| AC-11 | Multi-format input | Agent accepts and correctly parses json, docx, pdf, html from shared folder |
| AC-12 | Multi-format output | Agent renders result in requested format (json, docx, pdf, html) |
| AC-13 | Shared folder write | Output is persisted to `gap_response/` subfolder |
| AC-14 | Thread ID required | Request without X-Thread-ID is rejected with 422 |

---

## Test cases

### Test 1 — Clean requirements
**Input:** structured_requirements with all fields populated, measurable acceptance criteria, clear actors, business_case fully covered
**Expected:** `gap_report: []`, `overall_quality: clean`, `recommendation: proceed`

### Test 2 — Ambiguous NFR
**Input:** REQ-005 = "The system must respond quickly"
**Expected:** GAP-001, `gap_type: non_measurable_nfr`, `severity: high`, recommendation suggests specific threshold

### Test 3 — Implied but unstated
**Input:** business_case mentions "audit logging for compliance" but no requirement covers it
**Expected:** GAP with `gap_type: implied_but_unstated`, `req_id_ref: null`, recommendation to add a requirement

### Test 4 — Empty requirements
**Input:** `structured_requirements: []`
**Expected:** Pipeline stopped. Error returned.

### Test 5 — Conflicting requirements
**Input:** REQ-003 = "system must work offline", REQ-007 = "system must sync in real time"
**Expected:** Two gaps both with `gap_type: conflicting_requirements`, referencing each other

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `gap_report` | Yes | GenWiz reads this field by name |
| Renaming `gap_summary` | Yes | GenWiz reads `gap_summary.recommendation` for routing |
| Changing `overall_quality` enum values | Yes | GenWiz routing logic depends on these |
| Changing `recommendation` enum values | Yes | GenWiz routing maps these |
| Adding a new `gap_category` | No | GenWiz doesn't parse categories — just passes report |
| Renaming `gap_id` | Yes | Downstream agents reference gaps by ID |

---

## Related files

| File | Purpose |
|---|---|
| `AD-04_Gap_Detection_Config.json` | Config — behaviour rules, inputs, outputs, git reader |
| `AD-04_Gap_Detection_SKILL.md` | This file — LLM system prompt and reasoning rules |
| `ADLC_Tech_Stack_Config.json` | LLM defaults — on_gap, confidence_threshold, retry_attempts |

---

*AD-04 · Gap Detection Agent · SKILL.md · v1.0.0 · May 2026*
