#!/usr/bin/env python3
"""
ADLC Agent Config Validator
Usage: python3 validate_config.py <config_file.json>
       python3 validate_config.py configs/DE-09_Unit_Test_Generation_Config.json
"""

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("✗ jsonschema not installed. Run: pip install jsonschema")
    sys.exit(1)


def validate(config_path: str):
    config_file = Path(config_path)
    schema_file = Path(__file__).parent / "AGENT_CONFIG_SCHEMA.json"

    # ── Load schema ───────────────────────────────────────────────────────────
    if not schema_file.exists():
        print(f"✗ Schema file not found: {schema_file}")
        sys.exit(1)

    with open(schema_file) as f:
        schema = json.load(f)

    # ── Load config ───────────────────────────────────────────────────────────
    if not config_file.exists():
        print(f"✗ Config file not found: {config_file}")
        sys.exit(1)

    with open(config_file) as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON: {e}")
            sys.exit(1)

    # ── Cross-field checks (schema can't do these) ────────────────────────────
    errors = []

    # agent.id must match metadata.id
    agent_id   = config.get("agent", {}).get("id", "")
    metadata_id = config.get("metadata", {}).get("id", "")
    if agent_id and metadata_id and agent_id != metadata_id:
        errors.append(f"agent.id ({agent_id}) does not match metadata.id ({metadata_id})")

    # manifest.returns must match outputs keys
    returns     = set(config.get("manifest", {}).get("returns", []))
    output_keys = set(config.get("outputs", {}).keys())
    missing_in_outputs = returns - output_keys
    missing_in_returns = output_keys - returns
    if missing_in_outputs:
        errors.append(f"manifest.returns has fields not defined in outputs: {missing_in_outputs}")
    if missing_in_returns:
        errors.append(f"outputs has fields not listed in manifest.returns: {missing_in_returns}")

    # manifest.takes_from.fields must match inputs keys
    takes_fields = set(config.get("manifest", {}).get("takes_from", {}).get("fields", []))
    input_keys   = set(config.get("inputs", {}).keys())
    missing_in_inputs  = takes_fields - input_keys
    missing_in_manifest = input_keys - takes_fields
    if missing_in_inputs:
        errors.append(f"manifest.takes_from.fields has fields not defined in inputs: {missing_in_inputs}")
    if missing_in_manifest:
        errors.append(f"inputs has fields not listed in manifest.takes_from.fields: {missing_in_manifest}")

    # skill_file must follow naming convention
    skill_file = config.get("agent", {}).get("skill_file", "")
    expected_prefix = config.get("agent", {}).get("id", "")
    if skill_file and expected_prefix and not skill_file.startswith(expected_prefix):
        errors.append(f"skill_file ({skill_file}) must start with agent ID ({expected_prefix})")

    # ── Schema validation ─────────────────────────────────────────────────────
    schema_errors = []
    validator = jsonschema.Draft7Validator(schema)
    for error in sorted(validator.iter_errors(config), key=lambda e: e.path):
        field = " → ".join(str(p) for p in error.absolute_path) or "root"
        schema_errors.append(f"  [{field}] {error.message}")

    # ── Report results ────────────────────────────────────────────────────────
    print(f"\nValidating: {config_file.name}")
    print(f"Schema:     v{schema.get('version', '?')}")
    print("─" * 50)

    if not schema_errors and not errors:
        print(f"✓ Valid")
        print(f"  Agent:    {config['agent']['id']} — {config['agent']['name']}")
        print(f"  Phase:    {config['agent']['phase']}")
        print(f"  Returns:  {', '.join(config['manifest']['returns'])}")
        print(f"  Status:   {config['metadata']['status']}")
        print()
        return True

    if schema_errors:
        print(f"✗ Schema errors ({len(schema_errors)}):")
        for e in schema_errors:
            print(e)

    if errors:
        print(f"✗ Cross-field errors ({len(errors)}):")
        for e in errors:
            print(f"  {e}")

    print()
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 validate_config.py <config_file.json>")
        print("       python3 validate_config.py configs/DE-09_Unit_Test_Generation_Config.json")
        sys.exit(1)

    success = validate(sys.argv[1])
    sys.exit(0 if success else 1)
