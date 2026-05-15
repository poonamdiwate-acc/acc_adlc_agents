#!/usr/bin/env python3
"""
ADLC Agent Config Generator
Asks a series of questions and produces a validated config JSON file.
Usage: python3 generate_agent_config.py
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

# ── Try to import jsonschema for validation ───────────────────────────────────
try:
    import jsonschema
    SCHEMA_VALIDATION = True
except ImportError:
    SCHEMA_VALIDATION = False
    print("⚠  jsonschema not installed — schema validation skipped.")
    print("   Install with: pip install jsonschema\n")

# ── Helpers ───────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("  ✗ Required. Please enter a value.")

def ask_choice(prompt: str, choices: list, default: str = None) -> str:
    options = " | ".join(choices)
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {prompt} ({options}){suffix}: ").strip().lower()
        if not value and default:
            return default
        if value in choices:
            return value
        print(f"  ✗ Must be one of: {options}")

def ask_int(prompt: str, default: int = None, min_val: int = None, max_val: int = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            val = int(raw)
            if min_val is not None and val < min_val:
                print(f"  ✗ Minimum is {min_val}")
                continue
            if max_val is not None and val > max_val:
                print(f"  ✗ Maximum is {max_val}")
                continue
            return val
        except ValueError:
            print("  ✗ Must be an integer.")

def ask_list(prompt: str) -> list:
    print(f"  {prompt}")
    print("  (enter one per line, blank line to finish)")
    items = []
    while True:
        val = input("    > ").strip()
        if not val:
            if items:
                return items
            print("  ✗ At least one item required.")
        else:
            items.append(val)

def ask_yn(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        val = input(f"  {prompt} {suffix}: ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        print("  ✗ Enter y or n.")

def to_slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def to_snake(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')

def to_file_slug(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')

def print_section(title: str):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")

def validate_id(agent_id: str) -> bool:
    return bool(re.match(r'^(PL|DE|BU|VA|GW|OP)-[0-9]{2}$', agent_id))

# ── Main generator ────────────────────────────────────────────────────────────

def generate():
    print("\n" + "=" * 60)
    print("  ADLC Agent Config Generator  v1.0.0")
    print("=" * 60)
    print("""
  Before you start — have these answers ready:
  ─────────────────────────────────────────────
   1. Agent name          e.g. Unit Test Generation
   2. Phase               plan / design / build / validate / operate
   3. Step number         e.g. 09
   4. Previous agent      step number + ID + name
   5. Input fields        fields the agent reads from shared folder
   6. Output fields       fields the agent returns (2 or more)
   7. Output field types  array or object per field
   8. Input subfolder     e.g. bs_docs
   9. Output subfolder    e.g. unit_test_response
  10. Skill names         2 or more, one-line description each
  11. LLM settings        max_tokens (default 2048) + timeout (default 30)
  ─────────────────────────────────────────────
  Press Enter to accept any default shown in [brackets].
""")
    input("  Ready? Press Enter to begin...")
    print()

    config = {}

    # ── AGENT block ──────────────────────────────────────────────────────────
    print_section("1 / 7  —  Agent identity")

    # Ask name and phase first so we can derive the correct ID prefix
    agent_name  = ask("Agent name (Title Case, e.g. Unit Test Generation)")
    phase       = ask_choice("Phase", ["plan", "design", "build", "validate", "operate"])

    # Derive prefix from phase
    phase_prefix = {
        "plan":     "PL",
        "design":   "DE",
        "build":    "BU",
        "validate": "VA",
        "operate":  "OP"
    }.get(phase, "AD")

    print(f"\n  ID prefix for {phase} phase: {phase_prefix}")
    print(f"  (Use GW-NN for GenWiz-owned agents)\n")
    while True:
        agent_id = ask(f"Agent ID ({phase_prefix}-NN, e.g. {phase_prefix}-04)").upper()
        if validate_id(agent_id):
            break
        print(f"  ✗ Format must be {phase_prefix}-NN or GW-NN (e.g. {phase_prefix}-04, GW-05)")

    endpoint_default   = f"/agents/{to_slug(agent_name)}"
    skill_default      = f"{agent_id}_{to_file_slug(agent_name)}_SKILL.md"

    endpoint   = ask("REST endpoint", default=endpoint_default)
    skill_file = ask("SKILL.md filename", default=skill_default)

    config["agent"] = {
        "id":         agent_id,
        "name":       agent_name,
        "version":    "1.0.0",
        "phase":      phase,
        "endpoint":   endpoint,
        "skill_file": skill_file
    }

    # ── LLM block ────────────────────────────────────────────────────────────
    print_section("2 / 7  —  LLM config override")
    print("  Only override what differs from system defaults.")
    print("  Defaults: max_tokens=2048, timeout_seconds=30\n")

    max_tokens       = ask_int("max_tokens",       default=2048, min_val=256,  max_val=16384)
    timeout_seconds  = ask_int("timeout_seconds",  default=30,   min_val=10,   max_val=300)

    config["llm_config_override"] = {
        "max_tokens":      max_tokens,
        "timeout_seconds": timeout_seconds
    }

    # ── MANIFEST block ───────────────────────────────────────────────────────
    print_section("3 / 7  —  Manifest (what GenWiz sees)")
    print("  Fields the agent reads from the shared folder input.\n")

    manifest_fields = ask_list("What fields does this agent read from input files?")
    returns         = ask_list("What fields does this agent return?")

    config["manifest"] = {
        "takes_from": {
            "source":  "shared_folder",
            "formats": ["json", "docx", "pdf", "html"],
            "fields":  manifest_fields
        },
        "returns":           returns,
        "run_id_required":   True,
        "run_id_source":     "X-Run-ID header",
        "thread_id_required":True,
        "thread_id_source":  "X-Thread-ID header"
    }

    # ── INPUTS block ─────────────────────────────────────────────────────────
    print_section("4 / 7  —  Inputs (validation rules)")
    print("  Define validation rules for each input field.\n")

    inputs = {}
    for field in manifest_fields:
        print(f"\n  Field: {field}")
        required = ask_yn(f"  Is '{field}' required?", default=True)
        ftype    = ask_choice(
            f"  Type of '{field}'",
            ["string", "array", "object", "boolean", "integer"],
            default="array" if "requirements" in field or "items" in field else "string"
        )
        on_fail  = ask_choice(
            f"  On validation failure",
            ["stop_and_report", "flag_and_continue"],
            default="stop_and_report" if required else "flag_and_continue"
        )
        entry = {"required": required, "type": ftype, "on_fail": on_fail}

        if not required:
            on_missing = ask_choice(
                f"  On missing (not required)",
                ["proceed_without", "flag_and_continue"],
                default="proceed_without"
            )
            entry["on_missing"] = on_missing

        if ftype == "array":
            min_items = ask_int(f"  Minimum items in '{field}'", default=1, min_val=1)
            entry["min_items"] = min_items

        inputs[field] = entry

    config["inputs"] = inputs

    # ── OUTPUTS block ────────────────────────────────────────────────────────
    print_section("5 / 7  —  Outputs (schemas)")
    print("  Define the schema for each output field.\n")

    outputs = {}
    for field in returns:
        print(f"\n  Output field: {field}")
        otype       = ask_choice(f"  Type of '{field}'", ["array", "object", "string"], default="array")
        description = ask(f"  One-sentence description of '{field}'")

        entry = {"type": otype, "description": description}

        if otype == "array":
            print(f"  Define item_schema for '{field}' array items:")
            id_field   = ask("    Item ID field name",   default=f"{to_snake(field[:-1])}_id" if field.endswith('s') else f"{to_snake(field)}_id")
            id_prefix  = ask("    Item ID prefix (2 uppercase letters, e.g. UT, DM, GP)", default="".join(w[0].upper() for w in field.split('_'))[:2])
            name_field = ask("    Item name field",      default=f"{to_snake(field[:-1])}_name" if field.endswith('s') else f"{to_snake(field)}_name")

            entry["item_schema"] = {
                id_field:   f"{id_prefix.upper()}-### sequential",
                name_field: "string",
                "description": "string",
                "req_id_refs": ["REQ-### from structured_requirements"]
            }

        elif otype == "object":
            print(f"  Define schema for '{field}' object:")
            summary_field    = ask("    Key summary field name", default="summary")
            rec_values       = ask("    Recommendation enum values (pipe-separated)", default="proceed | review | blocked")
            entry["schema"]  = {
                summary_field:   "string",
                "recommendation": rec_values
            }

        outputs[field] = entry

    config["outputs"] = outputs

    # ── SUPPORTED FORMATS block ───────────────────────────────────────────────
    print_section("6 / 7  —  Supported formats")
    print("  All formats enabled by default.\n")

    default_output = ask_choice("Default output format", ["json", "docx", "pdf", "html"], default="json")

    config["supported_formats"] = {
        "input":          ["json", "docx", "pdf", "html"],
        "output":         ["json", "docx", "pdf", "html"],
        "default_output": default_output
    }

    # ── SHARED IO block ───────────────────────────────────────────────────────
    print_section("7 / 7  —  Shared folder")
    print("  Subfolder names inside {base_path}/{thread_id}/\n")

    input_sub_default  = "bs_docs"
    output_sub_default = f"{to_snake(agent_name)}_response"

    input_subfolder  = ask("Input subfolder name",  default=input_sub_default)
    output_subfolder = ask("Output subfolder name", default=output_sub_default)

    config["shared_io"] = {
        "input_subfolder":  input_subfolder,
        "output_subfolder": output_subfolder
    }

    # ── METADATA block ───────────────────────────────────────────────────────
    config["metadata"] = {
        "id":           agent_id,
        "version":      "1.0.0",
        "status":       "draft",
        "last_updated": str(date.today())
    }

    # ── Write file ───────────────────────────────────────────────────────────
    output_dir      = Path("adlc") / "configs"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename        = f"{agent_id}_{to_file_slug(agent_name)}_Config.json"
    output_path     = output_dir / filename

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n{'=' * 50}")
    print(f"  ✓ Generated: {output_path}")

    # ── Validate ─────────────────────────────────────────────────────────────
    schema_path = Path("AGENT_CONFIG_SCHEMA.json")
    if SCHEMA_VALIDATION and schema_path.exists():
        with open(schema_path) as f:
            schema = json.load(f)
        try:
            jsonschema.validate(config, schema)
            print(f"  ✓ Validated against schema v{schema.get('version', '?')}")
        except jsonschema.ValidationError as e:
            print(f"\n  ✗ Schema validation failed:")
            print(f"    Field:   {' → '.join(str(p) for p in e.absolute_path)}")
            print(f"    Problem: {e.message}")
            print(f"    Fix the config file and re-run validation.")
            sys.exit(1)
    elif not schema_path.exists():
        print("  ⚠  AGENT_CONFIG_SCHEMA.json not found — skipping validation")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n  Summary:")
    print(f"    Agent ID:       {config['agent']['id']}")
    print(f"    Name:           {config['agent']['name']}")
    print(f"    Phase:          {config['agent']['phase']}")
    print(f"    Endpoint:       {config['agent']['endpoint']}")
    print(f"    Reads fields:   {', '.join(config['manifest']['takes_from']['fields'])}")
    print(f"    Returns:        {', '.join(config['manifest']['returns'])}")
    print(f"    Input folder:   {config['shared_io']['input_subfolder']}")
    print(f"    Output folder:  {config['shared_io']['output_subfolder']}")
    print(f"    Skill file:     {config['agent']['skill_file']}")
    print(f"\n  Next step: create {config['agent']['skill_file']} in skills/")
    print(f"{'=' * 50}\n")

    return output_path


if __name__ == "__main__":
    try:
        generate()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.\n")
        sys.exit(0)
