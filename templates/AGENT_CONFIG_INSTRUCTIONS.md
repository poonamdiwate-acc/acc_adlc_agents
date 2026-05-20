# ADLC Agent Config — Instructions
## How to use `AGENT_CONFIG_TEMPLATE.json`

Read this fully before opening the template.
Every `{{PLACEHOLDER}}` in the template maps to a section below.
Fields marked **DO NOT CHANGE** must stay exactly as written.,

---

## Step-by-step process

```
Step 1 — Answer the 11 questions below
Step 2 — Copy the template
Step 3 — Fill every {{PLACEHOLDER}}
Step 4 — Check against the example (AD-08 Data Design)
Step 5 — Validate the JSON
Step 6 — Place in configs/ folder
```

---

## Step 1 — Answer these 11 questions first

Write your answers down before touching the template.
Everything else follows from these.

| # | Question | Example answer |
|---|---|---|
| 1 | What is the step number? | `9` |
| 2 | What is the agent name? | `Unit Test Generation` |
| 3 | What phase? | `design` |
| 4 | What is a short URL-safe slug for the endpoint? | `unit-test-generation` |
| 5 | What is a short underscore slug for the MCP tool? | `unit_test_generation` |
| 6 | What is the previous step — number, agent ID, name? | `8, AD-08, Data Design Agent` |
| 7 | What is the input folder name in the shared folder? | `bs_docs` |
| 8 | What is the output folder name in the shared folder? | `unit_test_response` |
| 9 | What are the output field names? | `unit_tests, test_coverage_report` |
| 10 | What are the skill names and one-line descriptions? | `test_generation, coverage_analysis` |
| 11 | Max tokens and timeout? | `4096, 60` |

---

## Step 2 — Copy the template

```bash
cp AGENT_CONFIG_TEMPLATE.json AD-09_Unit_Test_Generation_Config.json
```

Name the file: `AD-{step_number}_{Agent_Name}_Config.json`

---

## Step 3 — Fill every {{PLACEHOLDER}}

Work through each block in order.

---

### `agent` block

| Placeholder | What to fill | Rules |
|---|---|---|
| `{{STEP_NUMBER}}` | Integer step number | Matches the number in `AD-xx`. Must be unique. |
| `{{AGENT_NAME}}` | Human-readable name | Title case. e.g. `Unit Test Generation` |
| `{{AGENT_DESCRIPTION}}` | One sentence — what the agent does | Start with a verb. e.g. `Generates unit test cases from structured requirements.` |
| `{{PHASE}}` | Phase this agent runs in | `plan` or `design` or `validate` |
| `{{ENDPOINT_SLUG}}` | URL-safe endpoint path | Lowercase, hyphens only. e.g. `unit-test-generation` |
| `{{MCP_TOOL_SLUG}}` | MCP tool name | Lowercase, underscores only. e.g. `unit_test_generation` |
| `{{PREVIOUS_STEP_NUMBER}}` | Previous agent step number | Integer |
| `{{PREVIOUS_AGENT_ID}}` | Previous agent ID | e.g. `AD-08` or `GW-05` |
| `{{PREVIOUS_AGENT_NAME}}` | Previous agent name | e.g. `Data Design Agent` |

**DO NOT CHANGE:** `"version": "1.0.0"` · `"standalone": true`

**Example:**
```json
"agent": {
  "id":            "AD-09",
  "name":          "Unit Test Generation",
  "version":       "1.0.0",
  "description":   "Generates unit test cases from structured requirements.",
  "phase":         "design",
  "standalone":    true,
  "endpoint":      "/agents/unit-test-generation",
  "mcp_tool_name": "run_unit_test_generation",
  "step_number":   9,
  "previous_step": {
    "step_number": 8,
    "agent_id":    "AD-08",
    "agent_name":  "Data Design Agent"
  }
}
```

---

### `llm_config_override` block

| Placeholder | What to fill | Default | When to increase |
|---|---|---|---|
| `{{MAX_TOKENS}}` | Integer | `2048` | Use `4096` if output is large (many items) |
| `{{TIMEOUT_SECONDS}}` | Integer | `30` | Use `60` if complex reasoning expected |

**DO NOT ADD** other fields here. `on_gap`, `confidence_threshold`, `retry_attempts` are inherited from `ADLC_Tech_Stack_Config.json`.

**Example:**
```json
"llm_config_override": {
  "max_tokens":      4096,
  "timeout_seconds": 60
}
```

---

### `behaviour` block

**DO NOT CHANGE** the standard fields:
```json
"on_empty_requirements": "stop_and_report"
"on_low_confidence":     "flag_and_continue"
"min_requirements":      1
```

Only add agent-specific rules if your agent needs them.
Example: `"max_test_cases": 500`

---

### `manifest` block

This is what GenWiz reads to discover the agent.

| Placeholder | What to fill | Rules |
|---|---|---|
| `{{PRIMARY_INPUT_FIELD}}` | The main field name the agent works on | e.g. `structured_requirements` |
| `{{PHASE_INPUT_FIELD_1}}` | First direct input from GenWiz | Always include `project_context` |
| `{{PHASE_INPUT_FIELD_2}}` | Second direct input from GenWiz | Always include `business_case` |
| `{{OUTPUT_FIELD_1}}` | First output field name | Snake case. e.g. `unit_tests` |
| `{{OUTPUT_FIELD_2}}` | Second output field name | Snake case. e.g. `test_coverage_report` |

**DO NOT CHANGE:** `"run_id_required": true` · `"run_id_source": "X-Run-ID header"` · `"thread_id_source": "X-Thread-ID header"`

**Example:**
```json
"manifest": {
  "takes_from": {
    "shared_folder": ["structured_requirements"],
    "phase_input":   ["project_context", "business_case", "constraints"]
  },
  "returns":          ["unit_tests", "test_coverage_report"],
  "run_id_required":  true,
  "run_id_source":    "X-Run-ID header",
  "thread_id_source": "X-Thread-ID header"
}
```

---

### `shared_folder` block

| Placeholder | What to fill | Rules |
|---|---|---|
| `{{INPUT_FOLDER}}` | Subfolder name where agent reads inputs | Lowercase, no spaces. e.g. `bs_docs` |
| `{{OUTPUT_FOLDER}}` | Subfolder name where agent writes output | Lowercase, no spaces. e.g. `unit_test_response` |

**DO NOT CHANGE:** `"base_path": "ENV:ADLC_SHARED_FOLDER_PATH"` · `"thread_id_header": "X-Thread-ID"`

**Example:**
```json
"shared_folder": {
  "base_path":        "ENV:ADLC_SHARED_FOLDER_PATH",
  "thread_id_header": "X-Thread-ID",
  "input_folder":     "bs_docs",
  "output_folder":    "unit_test_response",
  "input_formats":    ["json", "docx", "pdf", "html"],
  "output_formats":   ["json", "docx", "pdf", "html"],
  "default_format":   "json"
}
```

---

### `inputs` block

Define validation rules for each input field.

| Placeholder | What to fill | Rules |
|---|---|---|
| `{{PRIMARY_INPUT_FIELD}}` | Same as manifest field | Must match exactly |
| `{{OPTIONAL_INPUT_FIELD}}` | Optional field name | e.g. `scope_boundaries` |

`project_context`, `business_case`, `constraints` — **DO NOT CHANGE** these three.

**Example:**
```json
"inputs": {
  "structured_requirements": {
    "required":  true,
    "type":      "array",
    "min_items": 1,
    "on_fail":   "stop_and_report"
  },
  "project_context": {
    "required": true,
    "type":     "object",
    "on_fail":  "stop_and_report"
  },
  "business_case": {
    "required": true,
    "type":     "string",
    "on_fail":  "stop_and_report"
  },
  "constraints": {
    "required":   false,
    "type":       "object",
    "on_missing": "proceed_without"
  }
}
```

---

### `outputs` block

Define the schema for each output field.

**Array output** — use for lists of items (test cases, entities, gaps):

| Placeholder | What to fill | Example |
|---|---|---|
| `{{OUTPUT_FIELD_1}}` | Field name | `unit_tests` |
| `{{OUTPUT_FIELD_1_DESCRIPTION}}` | One sentence | `One item per unit test case.` |
| `{{ITEM_ID_FIELD}}` | ID field name | `test_id` |
| `{{ID_PREFIX}}` | Two uppercase letters | `UT` → produces `UT-001`, `UT-002` |
| `{{ITEM_NAME_FIELD}}` | Name field | `test_name` |

**Object output** — use for summaries and reports:

| Placeholder | What to fill | Example |
|---|---|---|
| `{{OUTPUT_FIELD_2}}` | Field name | `test_coverage_report` |
| `{{OUTPUT_FIELD_2_DESCRIPTION}}` | One sentence | `Aggregated test coverage summary.` |
| `{{SUMMARY_FIELD}}` | Key summary field | `overall_coverage` |
| `{{RECOMMENDATION_VALUES}}` | Pipe-separated allowed values | `proceed \| improve_coverage \| blocked` |

**Example:**
```json
"outputs": {
  "unit_tests": {
    "type":        "array",
    "description": "One item per unit test case.",
    "item_schema": {
      "test_id":    "UT-### sequential",
      "test_name":  "string",
      "description":"string",
      "req_id_refs":["REQ-### from structured_requirements"]
    }
  },
  "test_coverage_report": {
    "type":        "object",
    "description": "Aggregated test coverage summary.",
    "schema": {
      "overall_coverage": "string",
      "recommendation":   "proceed | improve_coverage | blocked"
    }
  }
}
```

---

### `skills` block

One entry per skill. Minimum two.

| Placeholder | What to fill | Rules |
|---|---|---|
| `{{SKILL_1_ID}}` | Snake case identifier | e.g. `test_generation` |
| `{{SKILL_1_DESCRIPTION}}` | One sentence starting with a verb | e.g. `Generates unit test cases from each REQ-### item.` |
| `{{SKILL_2_ID}}` | Snake case identifier | e.g. `coverage_analysis` |
| `{{SKILL_2_DESCRIPTION}}` | One sentence | e.g. `Analyses test coverage across all requirements.` |

**DO NOT CHANGE:** `"enabled": true`

---

### `metadata` block

| Placeholder | What to fill | Format |
|---|---|---|
| `{{DATE}}` | Today's date | `YYYY-MM-DD` |

**DO NOT CHANGE:** `"version": "1.0.0"` · `"status": "ready"`

---

## Step 4 — Check against the example

Open `AGENT_CONFIG_EXAMPLE.json` (AD-08 Data Design). Verify your config matches this structure:

```
✓ agent block — id, name, version, description, phase, standalone, endpoint, mcp_tool_name, step_number, previous_step
✓ llm_config_override — only max_tokens + timeout_seconds
✓ behaviour — standard fields only unless agent-specific rules needed
✓ manifest — takes_from (shared_folder + phase_input), returns, run_id_required, run_id_source, thread_id_source
✓ shared_folder — base_path (ENV), thread_id_header, input_folder, output_folder, input_formats, output_formats, default_format
✓ inputs — no source field, no git_path field
✓ outputs — array output with item_schema + object output with schema
✓ skills — enabled: true on every skill
✓ metadata — version 1.0.0, status ready, date filled
✓ No {{PLACEHOLDER}} remaining anywhere in the file
✓ No iteration_1 / iteration_2 wrappers
✓ No enabled field on shared_folder or skills block
✓ No _note fields unless genuinely needed
```

---

## Step 5 — Validate the JSON

Run this in the terminal from the ADLC root:

```bash
python3 -c "
import json
with open('configs/AD-09_Unit_Test_Generation_Config.json') as f:
    cfg = json.load(f)
print('Valid JSON')
print('Agent ID:    ', cfg['agent']['id'])
print('Step number: ', cfg['agent']['step_number'])
print('Phase:       ', cfg['agent']['phase'])
print('Endpoint:    ', cfg['agent']['endpoint'])
print('Returns:     ', cfg['manifest']['returns'])
print('Input folder:', cfg['shared_folder']['input_folder'])
print('Output folder:', cfg['shared_folder']['output_folder'])
"
```

All fields should print correctly with no errors before proceeding.

---

## Step 6 — Place in configs/ folder

```bash
mv AD-09_Unit_Test_Generation_Config.json ADLC/configs/
```

---

## Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Agent ID | `AD-{step_number}` | `AD-09` |
| Config filename | `AD-{step}_{Name}_Config.json` | `AD-09_Unit_Test_Generation_Config.json` |
| Agent name | Title case | `Unit Test Generation` |
| Endpoint slug | Lowercase hyphenated | `unit-test-generation` |
| MCP tool name | `run_` + lowercase underscore | `run_unit_test_generation` |
| Output field names | Lowercase underscore | `unit_tests`, `test_coverage_report` |
| Skill IDs | Lowercase underscore | `test_generation` |
| Item ID prefix | Two uppercase letters | `UT` → `UT-001`, `UT-002` |
| Input folder | Lowercase no spaces | `bs_docs` |
| Output folder | Lowercase no spaces | `unit_test_response` |

---

## What NOT to do

| Rule | Why |
|---|---|
| Do not add `iteration_1` or `iteration_2` wrappers | Flat structure only |
| Do not add `enabled` field to any block | If block exists it is active |
| Do not add `source` or `git_path` to inputs | Shared folder replaces git |
| Do not add `on_gap`, `retry_attempts`, `confidence_threshold` to `llm_config_override` | Inherited from ADLC Tech Stack |
| Do not hardcode `ADLC_SHARED_FOLDER_PATH` | Always reference as `ENV:` |
| Do not change `run_id_required` or `run_id_source` | System-wide contract |
| Do not change `thread_id_source` or `thread_id_header` | System-wide contract |
| Do not leave any `{{PLACEHOLDER}}` unfilled | Config will fail validation |

---

## After config is done — create the SKILL.md

Once the config is validated, create the matching SKILL.md:

```
AD-{step}_{Name}_SKILL.md
e.g. AD-09_Unit_Test_Generation_SKILL.md
```

Use `SKILL_MD_TEMPLATE.md` and `SKILL_MD_INSTRUCTIONS.md`.
The config is your source of truth — every field in the SKILL.md must match the config.

---

*ADLC Agent Config Instructions · v2.0.0 · May 2026*
