# ADLC — AI Development Lifecycle

An agent-based pipeline that automates the full software development lifecycle using LLMs. Each phase (Plan, Build, Validate, Operate) is orchestrated by a dedicated phase orchestrator that routes work through a sequence of specialised sub-agents.

---

## Current status

| Phase | Orchestrator | Agents | Status |
|---|---|---|---|
| Plan | PL-00 | PL-01, PL-02, PL-03, PL-06 | Iteration 1 — ready |
| Discovery | DI-00 | DI-01 to DI-04 | Deferred |
| Design | DE-00 | DE-01 to DE-10 | Deferred |
| Build | BU-00 | BU-01 to BU-07 | Deferred |
| Validate | VA-00 | VA-01 to VA-06 | Deferred |
| Operate | OP-00 | OP-01 to OP-05 | Deferred |

---

## Folder structure

```
adlc/
│
├── configs/                              # JSON config files — runtime behaviour
│   ├── ADLC_Tech_Stack_Config.json       # Technology preferences for the whole system
│   ├── ADLC_Main_Orchestrator_Config.json# Top-level controller config
│   ├── PL-00_Plan_Orchestrator_Config.json
│   ├── PL-01_Requirement_Analysis_Config.json
│   ├── PL-02_Business_Analyzer_Config.json
│   ├── PL-03_Backlog_Agent_Config.json
│   └── PL-06_Plan_Review_Config.json
│
├── skills/                               # SKILL.md files — LLM system prompts
│   ├── PL-00_Plan_Orchestrator_SKILL.md
│   ├── PL-01_Requirement_Analysis_SKILL.md
│   ├── PL-02_Business_Analyzer_SKILL.md
│   ├── PL-03_Backlog_Agent_SKILL.md
│   └── PL-06_Plan_Review_SKILL.md
│
├── core/                                 # Shared infrastructure — used by all phases
│   ├── __init__.py
│   ├── config_loader.py                  # Loads and merges all JSON configs
│   ├── skill_loader.py                   # Extracts system prompts from SKILL.md
│   ├── llm_client.py                     # Wraps google-genai (Vertex Gemini) SDK with config params
│   └── state_validator.py               # Pydantic models for LLM output validation
│
├── plan/                                 # Plan phase implementation
│   ├── __init__.py
│   ├── state.py                          # PlanState TypedDict + Pydantic models
│   ├── graph.py                          # StateGraph — nodes, edges, routing
│   ├── nodes/                            # LangGraph node adapters — one per agent
│   │   ├── __init__.py
│   │   ├── pl00.py                       # Plan Orchestrator node
│   │   ├── pl01.py                       # Requirement Analysis node
│   │   ├── pl02.py                       # Business Analyzer node
│   │   ├── pl03.py                       # Backlog Agent node
│   │   └── pl06.py                       # Plan Review Agent node
│   └── agents/                           # Agent implementations — one folder per agent
│       ├── requirement_analysis/
│       │   ├── __init__.py
│       │   ├── agent.py                  # Core logic — orchestrates the three steps
│       │   ├── input_builder.py          # Builds LLM user message from PlanState
│       │   ├── output_parser.py          # Parses and validates LLM JSON response
│       │   └── behaviour.py              # Applies behaviour rules from config
│       ├── business_analyzer/
│       │   ├── __init__.py
│       │   ├── agent.py
│       │   ├── input_builder.py
│       │   ├── output_parser.py
│       │   └── behaviour.py
│       ├── backlog_agent/
│       │   ├── __init__.py
│       │   ├── agent.py
│       │   ├── input_builder.py
│       │   ├── output_parser.py
│       │   └── behaviour.py
│       └── plan_review/
│           ├── __init__.py
│           ├── agent.py
│           ├── input_builder.py
│           ├── output_parser.py
│           ├── behaviour.py
│           └── checklist.py              # Runs CHK-01 to CHK-10
│
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── test_config_loader.py
│   │   ├── test_skill_loader.py
│   │   ├── test_state_validator.py
│   │   └── agents/
│   │       ├── test_pl01_input_builder.py
│   │       ├── test_pl01_output_parser.py
│   │       ├── test_pl01_behaviour.py
│   │       └── test_pl06_checklist.py
│   └── integration/
│       ├── test_plan_graph.py            # Full plan phase end-to-end
│       └── fixtures/
│           ├── sample_prompt.txt
│           └── sample_business_case.txt
│
├── run.py                                # CLI entry point
├── requirements.txt                      # Python dependencies
├── .env.example                          # Environment variable template
├── .python-version                       # Python version pin (3.11)
└── .gitignore
```

---

## How the files relate

```
SKILL.md          → read by LLM at runtime (system prompt only)
Config JSON       → read by Python at startup (behaviour, routing, wiring)
agent.py          → orchestrates: input_builder → llm_client → output_parser
node/pl01.py      → thin LangGraph adapter — calls agent.py, returns state slice
graph.py          → wires nodes into StateGraph with edges and routing
state.py          → PlanState TypedDict that flows through all nodes
config_loader.py  → three-way LLM config merge: ADLC-00 → PL-00 → agent
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A GCP project with Vertex AI Gemini enabled
- A service-account JSON key with the `roles/aiplatform.user` role on that project

### 2. Clone and create virtual environment

```bash
git clone <repo-url>
cd adlc
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set:

```
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/vertex-sa.json
GOOGLE_GENAI_USE_VERTEXAI=true
ENV=dev
```

The service-account JSON file referenced by `GOOGLE_APPLICATION_CREDENTIALS` should never be committed — keep it under a gitignored directory (e.g. `secrets/`).

### 5. Run the plan phase

```bash
python run.py \
  --prompt "Build a secure login system" \
  --business-case "We need to reduce account takeover by 40% in Q2 2026"
```

---

## Architecture overview

```
ADLC-00 Main Orchestrator
        │
        ▼
PL-00 Plan Orchestrator
        │
        ├──► PL-01 Requirement Analysis
        │         │ structured_requirements
        │         ▼
        ├──► PL-02 Business Analyzer
        │         │ epics_and_features
        │         ▼
        ├──► PL-03 Backlog Agent
        │         │ prioritised_backlog
        │         ▼
        └──► PL-06 Plan Review Agent
                  │
                  ├── approved ──────────────► Build phase
                  └── rejected ─────────────► PL-01 (rework loop)
```

**State** flows as a single `PlanState` TypedDict through every node. Each agent reads only what it needs and writes only the fields it owns.

**LLM config** is resolved via a three-way merge per agent:
```
ADLC-00 (model, fallback_model)
    + PL-00 (on_gap, retry_attempts, confidence_threshold)
    + Agent (max_tokens, timeout_seconds)
= resolved config for that agent call
```

---

## Configuration hierarchy

```
ADLC_Tech_Stack_Config.json        → how the system runs (framework, checkpointer, logging)
ADLC_Main_Orchestrator_Config.json → what the system does (phases, gates, constraints)
PL-00_Plan_Orchestrator_Config.json→ how the plan phase behaves (routing, behaviour)
PL-0x_Agent_Config.json            → how each agent behaves (llm override, inputs, skills)
PL-0x_SKILL.md                     → how each agent reasons (system prompt — LLM reads this)
```

---

## LLM model selection

Model is set once in `ADLC_Main_Orchestrator_Config.json`. To override for the entire Plan phase, set `model` in `PL-00_Plan_Orchestrator_Config.json → llm_config_override`. To override for a single agent, set `model` in that agent's config.

```
ADLC-00  model: gemini-2.5-flash        ← default for whole system
PL-00    model: gemini-2.5-pro          ← overrides for Plan phase (optional)
PL-06    model: gemini-2.5-flash-lite   ← overrides for this agent only (optional)
```

---

## Iteration 2 — what's deferred

| Item | Enabled by |
|---|---|
| PL-04 Harness Builder | Add to `agent_routing_config`, enable in config |
| PL-05 FinOps Architect | Add to `agent_routing_config`, enable in config |
| PL-07 Human Governance | Add `interrupt_before: ["pl07"]` to graph compile |
| Parallel fan-out PL-03/04/05 | Change `execution_mode` to `parallel` in PL-00 config |
| PostgresSaver | Set `checkpointer.active: prod` in tech stack config |
| FastAPI layer | Enable `api.enabled: true` in tech stack config |

---

## Maintenance rules

| Change | Files to update |
|---|---|
| New input field | Agent config + SKILL.md inputs section + PL-00 passes_to |
| Output field renamed | SKILL.md breaking changes table + output_parser.py + state.py |
| Behaviour rule changed | Agent config behaviour block + SKILL.md system prompt rules |
| New agent added | New config + SKILL.md + node + agent folder + graph.py + state.py |
| Model changed | ADLC-00 or phase/agent config llm_config_override only |

---

## Running tests

```bash
# Unit tests only (no Vertex credentials needed)
pytest tests/unit/

# Integration tests (requires GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION,
# and valid GOOGLE_APPLICATION_CREDENTIALS pointing at a Vertex-enabled SA)
pytest tests/integration/

# All tests
pytest
```

---

## File ownership — what reads what

| File type | Read by | Purpose |
|---|---|---|
| `SKILL.md` | LLM (via system prompt) | Reasoning and output format |
| `*_Config.json` | Python (config_loader.py) | Behaviour, routing, wiring |
| `ADLC_Tech_Stack_Config.json` | Python (config_loader.py) | Framework, checkpointer, logging |
| `state.py` | Python (all nodes) | Shared state contract |
| `requirements.txt` | pip | Package installation |
| `.env` | Python (python-dotenv) | Secrets and environment |
| `README.md` | Developer | Project navigation |
