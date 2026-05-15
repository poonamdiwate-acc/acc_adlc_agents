# AGENT_CONFIG_TEMPLATE — Changelog

---

## v2.0.0 — 2026-05-15 ← CURRENT

**Removed**
- `agent.description`, `agent.standalone`, `agent.mcp_tool_name`, `agent.step_number`, `agent.previous_step`
- `behaviour` block
- `skills` block
- `git_reader` block
- `git_writer` block

**Added**
- `agent.skill_file`
- `manifest.takes_from.source: "shared_folder"`
- `manifest.takes_from.formats`
- `manifest.thread_id_required: true`
- `manifest.thread_id_source: "X-Thread-ID header"`
- `supported_formats` block
- `shared_io` block

**Changed**
- `manifest.takes_from` — git + phase_input → shared_folder
- `metadata.status` — "ready" → "draft"
- `inputs` — removed git_path, json_field, source fields

---

## v1.1.0 — 2026-05-13

**Removed**
- `agent.iteration`
- `llm_config_override.on_gap`, `confidence_threshold`, `retry_attempts` — inherited from tech stack
- `inputs.source` field — redundant with manifest
- `git_reader.reads` array — redundant with inputs
- `git_reader.enabled` — if block exists it is active

---

## v1.0.0 — 2026-05-07

Initial template.
