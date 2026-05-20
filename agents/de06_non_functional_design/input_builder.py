"""DE-06 input builder — assemble the LLM user message.

The system prompt comes from the SKILL.md (loaded by ``skill_loader``).
This module owns the user message — the JSON-serialised inputs the LLM
needs to do its job. Layout matches what the SKILL's ``INPUTS:`` section
describes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Union


_NFR_PREFIX_RE = re.compile(r"^NFR[-_]", re.IGNORECASE)
_NON_NFR_PREFIX_RE = re.compile(r"^(FR|IR|UC|BR|AR)[-_]", re.IGNORECASE)

_NFR_TYPE_KEYWORDS = {"non_functional", "non-functional", "nonfunctional", "nfr"}

_NFR_SUBTYPE_KEYWORDS = {
    "performance", "availability", "scalability", "security",
    "observability", "resilience", "reliability", "latency",
    "throughput", "uptime", "sla", "compliance", "audit",
    "encryption", "authentication", "monitoring",
}


def _is_nfr_requirement(req: Dict[str, Any]) -> bool:
    """Determine if a requirement is non-functional by ID prefix, type, or subtype."""
    req_id = req.get("req_id", "")
    if _NON_NFR_PREFIX_RE.match(req_id):
        return False
    if _NFR_PREFIX_RE.match(req_id):
        return True

    req_type = (req.get("type") or "").lower().replace(" ", "_").replace("-", "_")
    if req_type in _NFR_TYPE_KEYWORDS:
        return True

    subtype = (req.get("subtype") or "").lower()
    if subtype in _NFR_SUBTYPE_KEYWORDS:
        return True

    category = (req.get("category") or "").lower()
    if category in _NFR_SUBTYPE_KEYWORDS:
        return True

    perf_metric = (req.get("performance_metric") or req.get("Performance / Usability Metric") or "").lower()
    if perf_metric in _NFR_SUBTYPE_KEYWORDS:
        return True

    return False


def _classify_requirements(
    requirements: Any,
) -> Dict[str, List[Dict[str, Any]]]:
    """Partition requirements into NFR vs non-NFR."""
    if not isinstance(requirements, list):
        return {"nfr": []}

    nfr_items: List[Dict[str, Any]] = []

    for req in requirements:
        if not isinstance(req, dict):
            continue
        if _is_nfr_requirement(req):
            nfr_items.append(req)

    return {"nfr": nfr_items}


def build_user_message(payload: Dict[str, Any]) -> str:
    """Render the agent payload into the user message body.

    The structured_requirements are pre-filtered to only NFR items.
    FR/IR items have been excluded upstream — the LLM receives only
    non-functional requirements as input to avoid repackaging functional
    or integration logic as NFRs.

    The LLM is expected to:
    1. Cover every NFR requirement in the input (performance, availability, etc.)
    2. Analyse agent_network_html to derive ADDITIONAL NFRs for scalability,
       resilience, observability based on architecture topology
    3. Design security controls from trust boundaries in the architecture
    """
    all_requirements = payload.get("structured_requirements") or []
    classified = _classify_requirements(all_requirements)

    body: Dict[str, Any] = {
        "structured_requirements": classified["nfr"],
        "agent_network_html": payload.get("agent_network_html") or "",
    }

    nfr_items = classified["nfr"]

    if nfr_items:
        preamble = (
            "INPUT CONTEXT:\n"
            "- 'structured_requirements' below contains ONLY non-functional requirements "
            "(NFR-prefixed). FR and IR items have been pre-filtered out.\n"
            "- You MUST cover ALL items listed in 'structured_requirements' — each one must "
            "produce at least one NFR specification.\n"
            "- You MUST ALSO analyse 'agent_network_html' to derive ADDITIONAL NFRs for:\n"
            "  * Scalability: per-service scaling targets based on topology and load patterns\n"
            "  * Resilience: fault tolerance, circuit breakers, RPO/RTO for identified SPOFs\n"
            "  * Observability: SLOs/SLIs, alerting thresholds, distributed tracing needs\n"
            "- For security_controls: extract trust boundaries and external interfaces from "
            "'agent_network_html' to identify threat surfaces and define controls.\n"
            "- req_id_refs MUST reference INPUT IDs present in 'structured_requirements' above "
            "(e.g. NFR-001, NFR-UC2-003 — the exact IDs in the input JSON). CRITICAL: do NOT "
            "use output NFR-### sequence numbers you are generating — those are your output IDs, "
            "not input IDs. For architecture-derived NFRs, always reference the most relevant "
            "input NFR ID from 'structured_requirements' (never leave req_id_refs empty when "
            "structured_requirements is non-empty).\n\n"
        )
    else:
        preamble = (
            "INPUT CONTEXT:\n"
            "- No structured NFR requirements were provided as input.\n"
            "- You MUST derive ALL NFRs entirely from 'agent_network_html' architecture analysis.\n"
            "- Analyse the architecture to design NFR specifications for:\n"
            "  * Scalability: per-service scaling targets based on topology and load patterns\n"
            "  * Performance: latency targets, throughput requirements per service\n"
            "  * Availability: uptime SLAs, redundancy requirements, failover strategies\n"
            "  * Resilience: fault tolerance, circuit breakers, RPO/RTO for identified SPOFs\n"
            "  * Observability: SLOs/SLIs, alerting thresholds, distributed tracing needs\n"
            "  * Security: authentication, authorization, encryption for each trust boundary\n"
            "- For security_controls: extract trust boundaries and external interfaces from "
            "'agent_network_html' to identify threat surfaces and define controls.\n"
            "- Set req_id_refs to an empty array [] for all NFRs since no source requirements exist.\n\n"
        )

    return preamble + json.dumps(body, ensure_ascii=False, indent=2)
