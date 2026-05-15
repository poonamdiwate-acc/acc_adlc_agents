# {{AGENT_ID}} · {{AGENT_NAME}}
## SKILL.md — v1.0.0

---

## Overview

| Field | Value |
|---|---|
| **Agent ID** | {{AGENT_ID}} |
| **Agent Name** | {{AGENT_NAME}} |
| **Phase** | {{PHASE}} |
| **Step** | {{STEP_NUMBER}} |
| **Previous step** | {{PREVIOUS_STEP_NUMBER}} — {{PREVIOUS_AGENT_NAME}} ({{PREVIOUS_AGENT_ID}}) |
| **Config file** | {{AGENT_ID}}_{{AGENT_NAME_SLUG}}_Config.json |
| **MCP tool** | {{MCP_TOOL_NAME}} |
| **Endpoint** | {{ENDPOINT}} |
| **Version** | 1.0.0 |
| **Thread ID Header** | X-Thread-ID |

---

## Purpose

{{AGENT_PURPOSE}}

> **One job:** {{ONE_JOB_STATEMENT}}

---

## Skills

### {{SKILL_1_NAME}}
{{SKILL_1_DESCRIPTION}}

### {{SKILL_2_NAME}}
{{SKILL_2_DESCRIPTION}}

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
Input folder:     {base_path}/{thread_id}/{{INPUT_FOLDER}}/
Output folder:    {base_path}/{thread_id}/{{OUTPUT_FOLDER}}/
Thread ID source: X-Thread-ID header
```

**Example:**
```
{shared_folder.base_path}\
└── threadid100\
    ├── {{INPUT_FOLDER}}\              ← Agent reads ALL files here
    │   ├── document.docx
    │   ├── requirements.pdf
    │   └── data.json
    └── {{OUTPUT_FOLDER}}\            ← Agent writes result here
        └── {{AGENT_ID}}_output.json
```

All supported files in `{{INPUT_FOLDER}}/` are parsed and merged into a single payload.

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

1. Resolve folder: `{base_path}/{thread_id}/{{INPUT_FOLDER}}/`
2. List all files with supported extensions (sorted alphabetically)
3. Parse each file according to its format
4. For JSON files → structured data merges directly into payload
5. For free-form documents (docx/pdf/html) → text is extracted, then an LLM call structures it into the expected fields
6. All parsed results are merged into a single dict

### Required fields (after merge)

| Field | Required | Type | On missing |
|---|---|---|---|
| `{{PRIMARY_INPUT_FIELD}}` | Yes | array (min 1 item) | `stop_and_report` |
| `project_context` | Yes | object | `stop_and_report` |
| `business_case` | Yes | string (non-empty) | `stop_and_report` |
| `{{OPTIONAL_INPUT_FIELD}}` | No | object | `proceed_without` |

### Input validation rules

**`{{PRIMARY_INPUT_FIELD}}`** — minimum 1 item. If empty or missing after all files parsed → stop and report.

**`project_context`** — must be a populated object. Used to scope the output to the correct domain.

**`business_case`** — must be a non-empty string. Used to validate output coverage against business goals.

**`{{OPTIONAL_INPUT_FIELD}}`** — optional. If provided, used to {{OPTIONAL_INPUT_PURPOSE}}. If absent → proceed without.

---

## Output Formats & Persistence

### Supported output formats

| Format | MIME type | Description |
|---|---|---|
| `json` | `application/json` | Default — structured JSON response |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word document with summary table + detail table |
| `pdf` | `application/pdf` | Styled report rendered via weasyprint (HTML → PDF) |
| `html` | `text/html` | Jinja2-templated report |

### Output persistence

The agent **always** writes its result to the shared output folder regardless of HTTP response format:

```
{base_path}/{thread_id}/{{OUTPUT_FOLDER}}/{{AGENT_ID}}_output.json           (format=json)
{base_path}/{thread_id}/{{OUTPUT_FOLDER}}/{{AGENT_ID}}_output_{run_id}.docx  (format=docx)
{base_path}/{thread_id}/{{OUTPUT_FOLDER}}/{{AGENT_ID}}_output_{run_id}.pdf   (format=pdf)
{base_path}/{thread_id}/{{OUTPUT_FOLDER}}/{{AGENT_ID}}_output_{run_id}.html  (format=html)
```

### Output schema

#### `{{OUTPUT_FIELD_1}}`

{{OUTPUT_FIELD_1_DESCRIPTION}}

```json
{
  "{{ITEM_ID_FIELD}}":   "{{ID_PREFIX}}-001",
  "{{ITEM_NAME_FIELD}}": "string",
  "description":         "string",
  "req_id_refs":         ["REQ-001"]
}
```

#### `{{OUTPUT_FIELD_2}}`

{{OUTPUT_FIELD_2_DESCRIPTION}}

```json
{
  "{{SUMMARY_FIELD}}": "string",
  "recommendation":    "{{RECOMMENDATION_VALUES}}"
}
```

---

## System prompt

```
You are {{AGENT_ID}}, the {{AGENT_NAME}} in the ADLC pipeline.

Your job is to {{SYSTEM_PROMPT_JOB_DESCRIPTION}}.

INPUTS:
- {{PRIMARY_INPUT_FIELD}}: {{PRIMARY_INPUT_DESCRIPTION}}
- project_context: squad, domain, project name
- business_case: the original business case document
- {{OPTIONAL_INPUT_FIELD}}: optional — {{OPTIONAL_INPUT_PURPOSE}}

RULES:
1. {{RULE_1}}
2. {{RULE_2}}
3. {{RULE_3}}
4. {{RULE_4}}
5. {{RULE_5}}
6. Every output item must trace back to at least one REQ-### from {{PRIMARY_INPUT_FIELD}}
7. Do not invent items that have no basis in the inputs
8. If {{PRIMARY_INPUT_FIELD}} is empty → stop. Return error: "{{PRIMARY_INPUT_FIELD}} is empty"

OUTPUT FORMAT:
Return a valid JSON object with exactly these keys:
- "{{OUTPUT_FIELD_1}}": {{OUTPUT_FIELD_1_FORMAT_DESCRIPTION}}
- "{{OUTPUT_FIELD_2}}": {{OUTPUT_FIELD_2_FORMAT_DESCRIPTION}}

Return only the JSON object. No explanation, no markdown, no preamble.
```

---

## Behaviour reference

### Decision table

| Condition | Action |
|---|---|
| Input folder (`{{INPUT_FOLDER}}/`) not found | 400 error: `"Input folder not found"` |
| No supported files in input folder | 400 error: `"No supported input files found"` |
| Free-form doc with no extractable text | 400 error from parser |
| `{{PRIMARY_INPUT_FIELD}}` empty after parse | Stop. Report: `"{{PRIMARY_INPUT_FIELD}} is empty"` |
| `business_case` empty after parse | Stop. Report: `"business_case is required"` |
| `project_context` missing | Stop. Report: `"project_context is required"` |
| `{{OPTIONAL_INPUT_FIELD}}` absent | Proceed without |
| Low confidence on any item | Flag item. Continue. |
| No output items produced | Stop. Report: `"no {{OUTPUT_FIELD_1}} could be derived from inputs"` |
| Unsupported output format requested | 400 error with list of supported formats |

---

## Acceptance criteria

| # | Criterion | Pass condition |
|---|---|---|
| AC-01 | All {{OUTPUT_FIELD_1}} items have required fields | `{{ITEM_ID_FIELD}}`, `{{ITEM_NAME_FIELD}}`, `description`, `req_id_refs` all non-null |
| AC-02 | `{{ITEM_ID_FIELD}}` sequential | {{ID_PREFIX}}-001, {{ID_PREFIX}}-002... no gaps |
| AC-03 | All `req_id_refs` valid | Reference real REQ-### from `{{PRIMARY_INPUT_FIELD}}` |
| AC-04 | `{{OUTPUT_FIELD_2}}` present | Object with all required fields |
| AC-05 | `recommendation` from allowed values | One of: `{{RECOMMENDATION_VALUES}}` |
| AC-06 | No invented items | Every item traceable to at least one REQ-### |
| AC-07 | Output is valid JSON | Parseable, no trailing commas, no markdown fences |
| AC-08 | Multi-format input | Agent accepts and correctly parses json, docx, pdf, html from shared folder |
| AC-09 | Multi-format output | Agent renders result in requested format (json, docx, pdf, html) |
| AC-10 | Shared folder write | Output persisted to `{{OUTPUT_FOLDER}}/` subfolder |
| AC-11 | Thread ID required | Request without X-Thread-ID rejected with 422 |

---

## Breaking changes

| Change | Breaking? | Why |
|---|---|---|
| Renaming `{{OUTPUT_FIELD_1}}` | Yes | GenWiz reads this field by name |
| Renaming `{{OUTPUT_FIELD_2}}` | Yes | GenWiz reads this field for routing |
| Changing `recommendation` enum values | Yes | GenWiz `phase_transitions` config maps these |
| Adding a new field to output | No | GenWiz ignores unknown fields |
| Changing internal item structure | No | GenWiz doesn't parse item internals |

---

## Related files

| File | Purpose |
|---|---|
| `{{AGENT_ID}}_{{AGENT_NAME_SLUG}}_Config.json` | Config — behaviour rules, inputs, outputs, shared folder |
| `{{AGENT_ID}}_{{AGENT_NAME_SLUG}}_SKILL.md` | This file — LLM system prompt and reasoning rules |
| `ADLC_Tech_Stack_Config.json` | LLM defaults — on_gap, confidence_threshold, retry_attempts |

---

*{{AGENT_ID}} · {{AGENT_NAME}} · SKILL.md · v1.0.0 · {{DATE}}*
