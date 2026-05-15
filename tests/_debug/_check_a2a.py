"""Quick A2A discovery check across all registered agents.

Hits each /.well-known/agent-card.json endpoint and prints the resolved
URL, transport, skill, modes, and input/output schemas. Used as a
one-shot diagnostic; safe to delete after.
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8080"
TOKEN = "replace-me-bearer-token-genwiz-uses"
ENDPOINTS = ["/agents/gap-detection", "/agents/data-design", "/agents/api-contracts"]


def fetch(endpoint: str):
    req = Request(
        f"{BASE}{endpoint}/.well-known/agent-card.json",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urlopen(req) as r:
        return r.status, json.loads(r.read())


for ep in ENDPOINTS:
    try:
        status, card = fetch(ep)
    except Exception as e:
        print(f"--- {ep} --- ERROR: {e}")
        continue
    skill = card["skills"][0]
    inp_schema = skill["inputSchema"]
    out_schema = skill["outputSchema"]
    print(f"--- {ep} ---")
    print(f"  status        : HTTP {status}")
    print(f"  name          : {card['name']}")
    print(f"  version       : {card['version']}")
    print(f"  url           : {card['url']}")
    print(f"  transport     : {card['preferredTransport']}")
    print(f"  auth          : {card['authentication']['schemes']}")
    print(f"  skill.id      : {skill['id']}")
    print(f"  skill.tags    : {skill['tags']}")
    print(f"  inputModes    : {skill['inputModes']}")
    print(f"  outputModes   : {skill['outputModes']}")
    print(f"  inputs        : {list(inp_schema['properties'].keys())}")
    print(f"  required      : {inp_schema.get('required', [])}")
    print(f"  outputs       : {list(out_schema['properties'].keys())}")
    print()
