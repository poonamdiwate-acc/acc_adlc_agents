"""
Trigger script for VA-04 Compliance Agent — no server, no LLM credentials needed.

Patches the LLM client with a synthetic response that simulates a realistic
compliance evaluation, then calls agent.run() directly via asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

# ── 1. Set dummy env vars before any agent module imports ─────────────────────
os.environ.setdefault("GOOGLE_CLOUD_PROJECT",           "test-project")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION",          "us-central1")
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "dummy-path")
os.environ.setdefault("ADLC_API_KEY",                   "test-key")

# ── 2. Patch Vertex SDK before import ─────────────────────────────────────────
from google import genai as _genai  # noqa: E402
_genai.Client = MagicMock(return_value=MagicMock())

# ── 3. Import the agent (safe now — no real GCP calls) ────────────────────────
from agents.va04_compliance import agent  # noqa: E402


# ── 4. Define the test payload ────────────────────────────────────────────────
PAYLOAD = {
    "release_artefacts": [
        {
            "artefact_id":   "SVC-001",
            "type":          "code_change",
            "description":   "Payment service v2.1 — multi-currency support",
            "changed_files": ["PaymentController.java", "CurrencyService.java"],
            "data_fields":   ["amount", "currency_code", "account_id", "email"]
        },
        {
            "artefact_id":    "DB-MIG-007",
            "type":           "database_migration",
            "description":    "Adds currency_code and exchange_rate columns to transactions",
            "changed_tables": ["transactions"],
            "data_fields":    ["currency_code", "exchange_rate", "user_id", "email"]
        }
    ],
    "policy_rules": [
        {
            "rule_id":     "POL-TLS-01",
            "name":        "Transport Security",
            "requirement": "All services must enforce TLS 1.2+ on all connections."
        },
        {
            "rule_id":     "POL-PII-02",
            "name":        "PII Data Encryption",
            "requirement": "All artefacts processing PII fields must apply AES-256 encryption at rest."
        },
        {
            "rule_id":     "POL-LOG-03",
            "name":        "Audit Logging",
            "requirement": "All payment services must log transaction_id, timestamp, amount, status."
        },
        {
            "rule_id":     "POL-MIG-04",
            "name":        "Migration Rollback",
            "requirement": "All database migration scripts must include a reversible rollback."
        }
    ],
    "project_context": {
        "squad":        "payments",
        "domain":       "fintech",
        "market":       "UK",
        "project_name": "PayCore v2"
    },
    "business_case": (
        "Extend PayCore to support multi-currency transactions for UK and EU markets, "
        "meeting PCI-DSS and GDPR requirements for PII data handling."
    ),
    "constraints": {
        "regulatory_scope": ["PCI-DSS", "GDPR"],
        "data_residency":   "EU"
    }
}


# ── 5. Synthetic LLM response (what Gemini would return) ─────────────────────
SYNTHETIC_LLM_RESPONSE = json.dumps({
    "compliance_audit_trail": [
        {
            "check_id":     "CA-001",
            "check_name":   "Transport Security — SVC-001",
            "artefact_ref": "SVC-001",
            "policy_ref":   "POL-TLS-01",
            "status":       "compliant",
            "evidence":     (
                "PaymentController.java enforces TLS 1.2 via API gateway "
                "policy config. Certificate chain verified. No plaintext endpoints found."
            ),
            "description":  "Payment service v2.1 meets TLS 1.2+ transport security requirement.",
            "req_id_refs":  []
        },
        {
            "check_id":     "CA-002",
            "check_name":   "PII Data Encryption — SVC-001",
            "artefact_ref": "SVC-001",
            "policy_ref":   "POL-PII-02",
            "status":       "non_compliant",
            "evidence":     (
                "CurrencyService.java line 88: field 'email' stored in memory cache "
                "without AES-256 encryption. account_id transmitted to downstream "
                "service without encryption-at-rest directive in deployment config."
            ),
            "description":  (
                "SVC-001 processes PII fields (email, account_id) without AES-256 "
                "encryption at rest, violating POL-PII-02."
            ),
            "req_id_refs":  []
        },
        {
            "check_id":     "CA-003",
            "check_name":   "Audit Logging — SVC-001",
            "artefact_ref": "SVC-001",
            "policy_ref":   "POL-LOG-03",
            "status":       "compliant",
            "evidence":     (
                "PaymentController.java lines 112-145 logs transaction_id, "
                "timestamp, amount, status on every transaction. "
                "Log output confirmed in deployment config."
            ),
            "description":  "Payment service v2.1 implements required audit logging for all transactions.",
            "req_id_refs":  []
        },
        {
            "check_id":     "CA-004",
            "check_name":   "PII Data Encryption — DB-MIG-007",
            "artefact_ref": "DB-MIG-007",
            "policy_ref":   "POL-PII-02",
            "status":       "conditionally_compliant",
            "evidence":     (
                "migration_script.sql adds user_id and email columns. "
                "AES-256 encryption declared at column level (ENCRYPTED WITH AES-256). "
                "Condition: key management service (KMS) must be active in target environment."
            ),
            "description":  (
                "DB migration declares column-level encryption for PII fields. "
                "Compliance depends on KMS being active at deployment time."
            ),
            "req_id_refs":  []
        },
        {
            "check_id":     "CA-005",
            "check_name":   "Migration Rollback — DB-MIG-007",
            "artefact_ref": "DB-MIG-007",
            "policy_ref":   "POL-MIG-04",
            "status":       "compliant",
            "evidence":     (
                "migration_script.sql contains DOWN migration block at lines 67-89. "
                "Rollback removes currency_code and exchange_rate columns and "
                "restores original schema. Tested on staging environment."
            ),
            "description":  "DB migration includes a verified reversible rollback procedure.",
            "req_id_refs":  []
        }
    ],
    "policy_signoff": {
        "overall_status":      "non_compliant_findings_present",
        "signoff_authority":   "VA-04",
        "total_checks":        5,
        "compliant_count":     3,
        "non_compliant_count": 1,
        "recommendation":      "blocked"
    }
})


# ── 6. Runner ─────────────────────────────────────────────────────────────────
def _print_separator(title: str) -> None:
    width = 70
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def _status_icon(status: str) -> str:
    return {
        "compliant":               "[PASS]  COMPLIANT",
        "non_compliant":           "[FAIL]  NON-COMPLIANT",
        "conditionally_compliant": "[WARN]  CONDITIONALLY COMPLIANT",
        "not_applicable":          "[N/A ]  NOT APPLICABLE",
    }.get(status, f"[????]  {status.upper()}")


def _recommendation_label(rec: str) -> str:
    return {
        "proceed":   "[PASS]  PROCEED   -- no blocking findings",
        "remediate": "[WARN]  REMEDIATE -- conditional findings present",
        "blocked":   "[FAIL]  BLOCKED   -- non-compliant findings present",
    }.get(rec, rec.upper())


async def trigger() -> None:
    # Patch the LLM call — replace the real Gemini call with our synthetic response
    agent._llm_client.call = AsyncMock(return_value=SYNTHETIC_LLM_RESPONSE)

    print("\n" + "-" * 70)
    print("  VA-04 | Compliance Agent  --  Direct Trigger")
    print("-" * 70)
    print(f"  Agent     : VA-04")
    print(f"  Run ID    : trigger-va04-001")
    print(f"  Artefacts : {len(PAYLOAD['release_artefacts'])}")
    print(f"  Rules     : {len(PAYLOAD['policy_rules'])}")
    print("-" * 70)

    result = await agent.run(PAYLOAD, run_id="trigger-va04-001")

    # ── Print audit trail ──────────────────────────────────────────────────
    _print_separator("COMPLIANCE AUDIT TRAIL")
    trail = result["compliance_audit_trail"]
    for check in trail:
        print(f"\n  [{check['check_id']}]  {check['check_name']}")
        print(f"  {'─' * 60}")
        print(f"  Artefact : {check['artefact_ref']}")
        print(f"  Policy   : {check['policy_ref']}")
        print(f"  Status   : {_status_icon(check['status'])}")
        print(f"  Evidence : {check['evidence'][:120]}...")

    # ── Print policy signoff ───────────────────────────────────────────────
    _print_separator("POLICY SIGN-OFF")
    signoff = result["policy_signoff"]
    print(f"\n  Authority   : {signoff['signoff_authority']}")
    print(f"  Total Checks: {signoff['total_checks']}")
    print(f"  [PASS] Compliant               : {signoff['compliant_count']}")
    print(f"  [FAIL] Non-Compliant           : {signoff['non_compliant_count']}")
    print(f"  [WARN] Conditionally Compliant : {signoff.get('conditionally_compliant_count', 0)}")
    print(f"\n  Recommendation: {_recommendation_label(signoff['recommendation'])}")

    # ── Print full JSON ────────────────────────────────────────────────────
    _print_separator("FULL JSON OUTPUT")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n" + "-" * 70)
    print("  VA-04 run complete.")
    print("-" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(trigger())
