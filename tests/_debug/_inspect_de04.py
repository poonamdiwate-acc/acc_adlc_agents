"""Profile http_methods + contract_categories in the latest DE-04 capture."""
import json
from pathlib import Path

data = json.loads(
    Path("tests/_debug/DE-04__20260515T072007_726076.txt").read_text(encoding="utf-8")
)
specs = data.get("openapi_spec") or []

methods, cats = {}, {}
for s in specs:
    m = s.get("http_method", "MISSING")
    c = s.get("contract_category", "MISSING")
    methods[m] = methods.get(m, 0) + 1
    cats[c] = cats.get(c, 0) + 1

print("http_method distribution:")
for k, v in sorted(methods.items()):
    print(f"  {k}: {v}")
print()
print("contract_category distribution:")
for k, v in sorted(cats.items()):
    print(f"  {k}: {v}")
print()
print(f"--- Spec #6 (the failing one) ---")
if len(specs) >= 6:
    s = specs[5]
    for k in ("spec_id", "endpoint_name", "http_method", "contract_category", "path"):
        print(f"  {k}: {s.get(k)!r}")
