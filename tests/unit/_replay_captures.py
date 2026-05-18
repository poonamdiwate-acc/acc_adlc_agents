"""Replay captured LLM outputs through the DE-03 parsers without any LLM call.

Captures must be created first by running the live agent with the env var
``LLM_CAPTURE_DIR`` pointing at a folder. Example:

    $env:LLM_CAPTURE_DIR = "C:\\Reliance_Jio\\acc_adlc_agents\\tests\\_debug"
    .\\.venv\\Scripts\\python.exe run.py --host 127.0.0.1 --port 8080
    # ...trigger one live call from another shell...

Then run::

    .\\.venv\\Scripts\\python.exe tests\\unit\\_replay_captures.py tests\\_debug

The script walks every captured ``*.txt`` and runs it through the
matching parser based on the agent_id prefix:

* ``DE-03-input-extraction__*.txt`` → ``core.format_handler._llm_extract``'s
  JSON-loading path (same logic as live extraction).
* ``DE-03__*.txt`` → ``agents.de03_data_design.output_parser.parse``.
* Any other prefix is reported as ``(no parser bound)``.

For each file the script reports: parser used, OK/FAIL, and the parser's
error message on FAIL. The exit code is 1 iff any capture failed to
parse — useful for CI loops while iterating on the normalizer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_project_root()))


from agents.de03_data_design import output_parser as de03_parser  # noqa: E402
from agents.de04_api_contracts import output_parser as de04_parser  # noqa: E402
from core.config_loader import get_config  # noqa: E402
from core.exceptions import OutputParseError  # noqa: E402

try:
    from agents.de07_technology_selection import output_parser as de07_parser  # noqa: E402
except ImportError:
    de07_parser = None  # type: ignore[assignment]


def _parse_extraction(text: str) -> dict:
    """Mirror the JSON-loading logic in core.format_handler._llm_extract."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    brace_idx = text.find("{")
    if brace_idx == -1:
        raise ValueError("no '{' in response")
    text = text[brace_idx:]
    decoder = json.JSONDecoder()
    result, end_pos = decoder.raw_decode(text)
    if not isinstance(result, dict):
        raise ValueError("top level is not a JSON object")
    trailing = text[end_pos:].strip()
    return {"parsed_keys": sorted(result.keys()), "trailing_chars": len(trailing)}


def _parse_de03_output(text: str) -> dict:
    """Run captured text through the DE-03 output_parser."""
    cfg = get_config()
    beh = cfg.behaviour("DE-03")
    parsed = de03_parser.parse(
        text,
        allowed_categories=beh.get("entity_categories", []),
        allowed_storage_classes=beh.get("storage_classes", []),
        allowed_confidence_levels=beh.get("confidence_levels", []),
    )
    return {
        "entity_count": len(parsed.get("data_model") or []),
        "secondary_store_count": len(
            (parsed.get("storage_selection") or {}).get("secondary_stores") or []
        ),
    }


def _parse_de04_output(text: str) -> dict:
    """Run captured text through the DE-04 output_parser."""
    cfg = get_config()
    beh = cfg.behaviour("DE-04")
    parsed = de04_parser.parse(
        text,
        allowed_categories=beh.get("contract_categories", []),
        allowed_methods=beh.get("http_methods_allowed", []),
    )
    return {
        "endpoint_count": len(parsed.get("openapi_spec") or []),
        "has_registry": bool(parsed.get("schema_registry")),
    }


def _parse_de07_output(text: str) -> dict:
    """Run captured text through the DE-07 output_parser."""
    if de07_parser is None:
        raise RuntimeError("DE-07 parser not importable in this environment")
    parsed = de07_parser.parse(text)
    return {"top_level_keys": sorted(parsed.keys())}


def _pick_parser(filename: str):
    if filename.startswith("DE-03-input-extraction__"):
        return "format_handler.extract", _parse_extraction
    if filename.startswith("DE-04-input-extraction__"):
        return "format_handler.extract", _parse_extraction
    if filename.startswith("DE-07-input-extraction__"):
        return "format_handler.extract", _parse_extraction
    if filename.startswith("DE-03__"):
        return "de03.output_parser", _parse_de03_output
    if filename.startswith("DE-04__"):
        return "de04.output_parser", _parse_de04_output
    if filename.startswith("DE-07__"):
        return "de07.output_parser", _parse_de07_output
    return None, None


def main(folder: Optional[str] = None) -> int:
    target = Path(folder or "tests/_debug").resolve()
    if not target.is_dir():
        print(f"capture folder not found: {target}", file=sys.stderr)
        return 2

    files = sorted(target.glob("*.txt"))
    if not files:
        print(f"(no *.txt captures in {target})")
        return 0

    fails = 0
    for path in files:
        parser_name, parser_fn = _pick_parser(path.name)
        if parser_fn is None:
            print(f"SKIP   {path.name}  (no parser bound)")
            continue
        try:
            result = parser_fn(path.read_text(encoding="utf-8"))
            print(f"OK     {path.name}  parser={parser_name}  {result}")
        except OutputParseError as exc:
            fails += 1
            errs = exc.detail.get("errors") if isinstance(exc.detail, dict) else None
            preview = (
                f" errors[0]={errs[0]}" if isinstance(errs, list) and errs else ""
            )
            print(
                f"FAIL   {path.name}  parser={parser_name}  "
                f"OutputParseError: {exc.message}{preview}"
            )
        except Exception as exc:
            fails += 1
            print(
                f"FAIL   {path.name}  parser={parser_name}  "
                f"{type(exc).__name__}: {exc}"
            )
    print()
    print(f"{len(files)} files, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
