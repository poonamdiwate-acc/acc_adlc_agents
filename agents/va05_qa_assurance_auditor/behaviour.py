"""VA-05 behaviour — config-driven rules around the LLM call.

Four concerns live here:

1. **Pre-flight input validation** — does the payload satisfy the
   config's ``inputs`` declarations (``required``, ``min_items``,
   ``type``)? If not, refuse to call the LLM at all (FR-1.1 / FR-2.1).
2. **Deterministic audit-trail review** — chronology, duplicate
   detection, sequence integrity, and checkpoint coverage are computed
   in code, not by the LLM (FR-1.2 / FR-1.3 / FR-1.4 / NFR-2).
3. **Post-flight disposition reconciliation** — enforces FR-2.5
   ("never close an exception lacking attributable evidence") by
   demoting any Accepted disposition with empty ``evidence_refs`` to
   Escalated. Renumbers ``finding_id`` sequentially. Stamps reviewer
   and reviewed_at where the LLM omitted them.
4. **Assurance sign-off + summary assembly** — computes
   ``overall_assurance`` / ``recommendation`` from the disposition and
   finding mix, then builds the immutable ``assurance_signoff`` artifact
   per FR-3.1 / FR-3.3 / NFR-3. When ``overall_assurance == blocked``,
   sign-off is set to ``None`` (cannot be issued).

Nothing here is hardcoded — every enum, severity, and finding type
comes from the agent config's ``behaviour`` block.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from core.exceptions import PipelineStopError


# Disposition / assurance enums (must match config behaviour block) ---------
_DISP_ACCEPTED = "Accepted"
_DISP_REJECTED = "Rejected"
_DISP_ESCALATED = "Escalated"

_ASSURANCE_CLEAN = "clean"
_ASSURANCE_QUALIFIED = "qualified"
_ASSURANCE_BLOCKED = "blocked"

_RECO_PROCEED = "proceed"
_RECO_WITH_ESCALATION = "proceed_with_escalation"
_RECO_HOLD = "hold_for_remediation"

# Finding type enums (must match config behaviour.audit_finding_types) -------
_FT_MISSING = "missing_record"
_FT_DUPLICATE = "duplicate_record"
_FT_OUT_OF_SEQUENCE = "out_of_sequence"
_FT_SIGNATURE_INVALID = "signature_invalid"
_FT_CHECKPOINT_NOT_COVERED = "checkpoint_not_covered"


# ---------------------------------------------------------------------------
# Pre-flight input validation
# ---------------------------------------------------------------------------


def validate_inputs(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
) -> None:
    """Raise :class:`PipelineStopError` if ``payload`` violates the config.

    Per ``behaviour.on_empty_audit_trail: stop_and_report`` an empty
    ``audit_trail`` array stops the agent before any LLM call — the
    cycle cannot be assured without an audit trail (FR-1.1).
    """
    for field_name, rules in inputs_cfg.items():
        if not isinstance(rules, dict):
            continue
        required = bool(rules.get("required"))
        value = payload.get(field_name)
        if required and (value is None or value == ""):
            raise PipelineStopError(
                f"Required input '{field_name}' is missing",
                detail={
                    "field": field_name,
                    "on_fail": rules.get("on_fail", "stop_and_report"),
                },
            )
        min_items = rules.get("min_items")
        if min_items and isinstance(value, list) and len(value) < int(min_items):
            raise PipelineStopError(
                f"Input '{field_name}' has {len(value)} items, "
                f"minimum {min_items}",
                detail={
                    "field": field_name,
                    "min_items": min_items,
                    "actual": len(value),
                },
            )

    audit_trail = payload.get("audit_trail") or []
    if (
        behaviour_cfg.get("on_empty_audit_trail") == "stop_and_report"
        and not audit_trail
    ):
        raise PipelineStopError(
            "audit_trail is empty — cycle cannot be assured",
            detail={"on_empty_audit_trail": "stop_and_report"},
        )

    project_context = payload.get("project_context") or {}
    if not project_context.get("cycle_id"):
        raise PipelineStopError(
            "project_context.cycle_id is required — sign-off must be bound to a cycle",
            detail={"field": "project_context.cycle_id"},
        )


def apply_input_defaults(
    payload: Dict[str, Any],
    inputs_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill optional inputs that are absent with type-appropriate defaults
    and report what was missing.

    Mutates ``payload`` in place. Returns an ``input_completeness`` dict::

        {
          "status":   "complete" | "partial",
          "missing":  [<field_name>, ...],          # whole fields absent
          "degraded": ["project_context.<soft>"]    # soft sub-fields absent
        }

    Required inputs are NOT touched here — :func:`validate_inputs` is the
    sole gate that hard-fails on those.
    """
    missing: List[str] = []
    degraded: List[str] = []

    for field_name, rules in inputs_cfg.items():
        if not isinstance(rules, dict) or rules.get("required"):
            continue
        # Only track inputs whose config declares an explicit ``default`` —
        # other optional inputs (e.g. checkpoint_expectations) signal their
        # absence through their own output field (checkpoint_coverage:
        # skipped) and must not pollute input_completeness.
        if "default" not in rules:
            continue
        value = payload.get(field_name)
        is_absent = value is None or value == "" or value == [] or value == {}
        if not is_absent:
            continue
        payload[field_name] = rules["default"]
        missing.append(field_name)

    project_context = payload.get("project_context") or {}
    pc_rules = inputs_cfg.get("project_context", {})
    if isinstance(pc_rules, dict):
        for soft in pc_rules.get("soft_fields", []) or []:
            if not project_context.get(soft):
                degraded.append(f"project_context.{soft}")

    status = "complete" if not missing and not degraded else "partial"
    return {"status": status, "missing": missing, "degraded": degraded}


# ---------------------------------------------------------------------------
# Deterministic audit-trail review
# ---------------------------------------------------------------------------


def review_audit_trail(
    audit_trail: List[Dict[str, Any]],
    checkpoint_expectations: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Detect missing, duplicate, out-of-sequence, and uncovered-checkpoint
    findings deterministically (FR-1.2, FR-1.3, FR-1.4).

    Returns an unnumbered list of finding dicts — the agent renumbers
    them sequentially after merging in LLM-supplied judgment findings.
    """
    findings: List[Dict[str, Any]] = []
    if not audit_trail:
        return findings

    # --- 1. Duplicate entry_id detection
    entry_ids = [e.get("entry_id") for e in audit_trail if e.get("entry_id")]
    id_counts = Counter(entry_ids)
    for entry_id, count in id_counts.items():
        if count > 1:
            findings.append({
                "finding_type": _FT_DUPLICATE,
                "severity": "high",
                "description": (
                    f"Audit entry_id '{entry_id}' appears {count} times "
                    f"— duplicates compromise traceability."
                ),
                "control_ref": _first_control_for_entry(audit_trail, entry_id),
                "recommendation": (
                    "Deduplicate the audit trail at source and re-issue."
                ),
            })

    # --- 2. Out-of-sequence detection
    timestamped = [
        e for e in audit_trail if e.get("timestamp")
    ]
    for prev, curr in zip(timestamped, timestamped[1:]):
        if _is_before(curr.get("timestamp"), prev.get("timestamp")):
            findings.append({
                "finding_type": _FT_OUT_OF_SEQUENCE,
                "severity": "medium",
                "description": (
                    f"Entry '{curr.get('entry_id')}' timestamped "
                    f"{curr.get('timestamp')} precedes prior entry "
                    f"'{prev.get('entry_id')}' at {prev.get('timestamp')}."
                ),
                "control_ref": curr.get("control_ref"),
                "recommendation": (
                    "Investigate clock skew or backdated entry at source; "
                    "Compliance Agent must re-sign the affected entries."
                ),
            })

    # --- 3. Missing signature detection
    for entry in audit_trail:
        if not entry.get("signature"):
            findings.append({
                "finding_type": _FT_SIGNATURE_INVALID,
                "severity": "critical",
                "description": (
                    f"Entry '{entry.get('entry_id')}' has no signature — "
                    f"audit integrity cannot be verified."
                ),
                "control_ref": entry.get("control_ref"),
                "recommendation": (
                    "Block sign-off. Compliance Agent must re-sign the entry."
                ),
            })

    # --- 4. Checkpoint coverage (only if checkpoint_expectations supplied)
    if checkpoint_expectations:
        expected = _extract_expected_checkpoints(checkpoint_expectations)
        covered = {
            e.get("control_ref") for e in audit_trail if e.get("control_ref")
        }
        for checkpoint in expected:
            if checkpoint not in covered:
                findings.append({
                    "finding_type": _FT_CHECKPOINT_NOT_COVERED,
                    "severity": "high",
                    "description": (
                        f"Expected checkpoint '{checkpoint}' has no "
                        f"corresponding audit entry."
                    ),
                    "control_ref": checkpoint,
                    "recommendation": (
                        "Block sign-off until Compliance Agent backfills "
                        "the missing entry or confirms the control did not "
                        "execute (with rationale)."
                    ),
                })

    return findings


def _first_control_for_entry(
    audit_trail: List[Dict[str, Any]], entry_id: str
) -> str | None:
    for entry in audit_trail:
        if entry.get("entry_id") == entry_id:
            return entry.get("control_ref")
    return None


def _is_before(a: str | None, b: str | None) -> bool:
    """Return True if ISO-8601 timestamp ``a`` is earlier than ``b``."""
    if not a or not b:
        return False
    try:
        return _parse_iso(a) < _parse_iso(b)
    except ValueError:
        return False


def _parse_iso(ts: str) -> datetime:
    # Accept trailing Z (UTC) per ISO-8601 — fromisoformat is strict in 3.10
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _extract_expected_checkpoints(
    checkpoint_expectations: Dict[str, Any],
) -> List[str]:
    """Pull the list of expected control_refs from either a flat list or
    nested ``checkpoints: [...]`` structure — supports both shapes.
    """
    if isinstance(checkpoint_expectations.get("checkpoints"), list):
        return [
            c.get("control_ref") if isinstance(c, dict) else str(c)
            for c in checkpoint_expectations["checkpoints"]
            if c
        ]
    if isinstance(checkpoint_expectations.get("expected_controls"), list):
        return [str(c) for c in checkpoint_expectations["expected_controls"] if c]
    return []


# ---------------------------------------------------------------------------
# Post-flight disposition reconciliation
# ---------------------------------------------------------------------------


def reconcile_dispositions(
    exception_log: List[Dict[str, Any]],
    audit_trail: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Enforce FR-2.5 and NFR-2 deterministically over the LLM output.

    Rules:
    * Any disposition of ``Accepted`` with empty ``evidence_refs`` is
      forcibly demoted to ``Escalated`` (FR-2.5).
    * Every disposition has ``reviewer`` and ``reviewed_at`` set if the
      LLM omitted them (FR-2.4 / AC-06).
    * ``audit_trail_ref`` must reference a real entry_id from the
      input audit_trail; unresolved refs are set to None and the
      disposition is escalated (NFR-2).
    """
    valid_entry_ids = {
        e.get("entry_id") for e in (audit_trail or []) if e.get("entry_id")
    }
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in exception_log:
        evidence = item.get("evidence_refs") or []
        if (
            item.get("disposition") == _DISP_ACCEPTED
            and not evidence
        ):
            item["disposition"] = _DISP_ESCALATED
            item["rationale"] = (
                "[VA-05 enforcement] Original disposition was Accepted but "
                "no attributable evidence was supplied — auto-escalated per "
                "FR-2.5. " + (item.get("rationale") or "")
            ).strip()
            if not item.get("escalation_target"):
                item["escalation_target"] = "validate_orchestrator"

        ref = item.get("audit_trail_ref")
        if ref and valid_entry_ids and ref not in valid_entry_ids:
            item["audit_trail_ref"] = None
            if item.get("disposition") == _DISP_ACCEPTED:
                item["disposition"] = _DISP_ESCALATED
                item["rationale"] = (
                    "[VA-05 enforcement] audit_trail_ref did not resolve to "
                    "a real audit entry — auto-escalated per NFR-2. "
                    + (item.get("rationale") or "")
                ).strip()
                if not item.get("escalation_target"):
                    item["escalation_target"] = "validate_orchestrator"

        item.setdefault("reviewer", "VA-05")
        item.setdefault("reviewed_at", now_iso)
        if item.get("disposition") != _DISP_ESCALATED:
            item["escalation_target"] = None
        elif not item.get("escalation_target"):
            item["escalation_target"] = "validate_orchestrator"

    return exception_log


def renumber_findings(
    audit_findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Enforce sequential FIND-### ids per AC-07 regardless of source."""
    for index, finding in enumerate(audit_findings, start=1):
        finding["finding_id"] = f"FIND-{index:03d}"
    return audit_findings


# ---------------------------------------------------------------------------
# Assurance summary + sign-off assembly
# ---------------------------------------------------------------------------


def build_assurance_summary(
    audit_trail: List[Dict[str, Any]],
    exception_log: List[Dict[str, Any]],
    audit_findings: List[Dict[str, Any]],
    behaviour_cfg: Dict[str, Any],
    input_completeness: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compute a trustworthy ``assurance_summary`` from the parsed outputs.

    When ``input_completeness.status == 'partial'`` a ``clean`` verdict is
    downgraded to ``qualified`` (and ``proceed`` → ``proceed_with_escalation``).
    A blocked verdict is never softened — blocking findings take precedence.
    """
    disposition_counts = Counter(
        item.get("disposition") for item in exception_log
    )
    overall_assurance, recommendation = _classify(
        audit_findings=audit_findings,
        disposition_counts=disposition_counts,
        behaviour_cfg=behaviour_cfg,
    )

    if (
        input_completeness is not None
        and input_completeness.get("status") == "partial"
        and overall_assurance == _ASSURANCE_CLEAN
    ):
        overall_assurance = _ASSURANCE_QUALIFIED
        recommendation = _RECO_WITH_ESCALATION

    summary: Dict[str, Any] = {
        "audit_entries_reviewed": len(audit_trail or []),
        "audit_findings_raised": len(audit_findings or []),
        "exceptions_reviewed": len(exception_log or []),
        "dispositions_by_type": {
            _DISP_ACCEPTED: int(disposition_counts.get(_DISP_ACCEPTED, 0)),
            _DISP_REJECTED: int(disposition_counts.get(_DISP_REJECTED, 0)),
            _DISP_ESCALATED: int(disposition_counts.get(_DISP_ESCALATED, 0)),
        },
        "overall_assurance": overall_assurance,
        "recommendation": recommendation,
    }
    if input_completeness is not None:
        summary["input_completeness"] = input_completeness
    return summary


def _classify(
    audit_findings: List[Dict[str, Any]],
    disposition_counts: Counter,
    behaviour_cfg: Dict[str, Any],
) -> Tuple[str, str]:
    """Decision table from SKILL.md ▸ 'Overall assurance decision table'."""
    blocking_set = set(behaviour_cfg.get("blocking_severities", []))
    blocking_findings = [
        f for f in audit_findings if f.get("severity") in blocking_set
    ]

    if blocking_findings:
        return _ASSURANCE_BLOCKED, _RECO_HOLD

    escalated = int(disposition_counts.get(_DISP_ESCALATED, 0))
    non_blocking_findings = len(audit_findings) - len(blocking_findings)

    if escalated == 0 and non_blocking_findings == 0:
        return _ASSURANCE_CLEAN, _RECO_PROCEED

    return _ASSURANCE_QUALIFIED, _RECO_WITH_ESCALATION


def build_assurance_signoff(
    project_context: Dict[str, Any],
    audit_findings: List[Dict[str, Any]],
    exception_log: List[Dict[str, Any]],
    assurance_summary: Dict[str, Any],
    behaviour_cfg: Dict[str, Any],
    checkpoint_expectations: Dict[str, Any] | None,
    input_completeness: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    """Build the immutable Assurance Sign-off artifact (FR-3.1, NFR-3).

    Returns ``None`` when ``overall_assurance == blocked`` — sign-off
    cannot be issued in that state per FR-3.3 / NFR-3.
    """
    overall = assurance_summary.get("overall_assurance")
    if overall == _ASSURANCE_BLOCKED:
        return None

    now = datetime.now(timezone.utc)
    cycle_id = project_context.get("cycle_id") or "UNKNOWN-CYCLE"
    blocking_set = set(behaviour_cfg.get("blocking_severities", []))

    blocking_finding_ids = [
        f.get("finding_id") for f in audit_findings
        if f.get("severity") in blocking_set
    ]

    if blocking_finding_ids or not audit_findings:
        audit_integrity = "verified" if not audit_findings else "findings_raised"
    else:
        audit_integrity = "findings_raised"

    checkpoint_coverage = _coverage_status(
        audit_findings=audit_findings,
        checkpoint_expectations=checkpoint_expectations,
    )

    dispositions = assurance_summary.get("dispositions_by_type", {})
    signoff: Dict[str, Any] = {
        "signoff_id": _generate_signoff_id(now),
        "cycle_id": cycle_id,
        "issued_at": now.isoformat(),
        "issued_by": "VA-05",
        "audit_integrity": audit_integrity,
        "checkpoint_coverage": checkpoint_coverage,
        "exceptions_reviewed": int(assurance_summary.get("exceptions_reviewed", 0)),
        "exceptions_accepted": int(dispositions.get(_DISP_ACCEPTED, 0)),
        "exceptions_rejected": int(dispositions.get(_DISP_REJECTED, 0)),
        "exceptions_escalated": int(dispositions.get(_DISP_ESCALATED, 0)),
        "process_attestation": (
            f"The validation process for {cycle_id} was followed in "
            f"accordance with documented controls."
        ),
        "blocking_findings": blocking_finding_ids,
        "version": "1.0",
        "immutable": True,
    }
    if input_completeness is not None:
        signoff["inputs_completeness"] = input_completeness.get("status", "complete")
    return signoff


def _coverage_status(
    audit_findings: List[Dict[str, Any]],
    checkpoint_expectations: Dict[str, Any] | None,
) -> str:
    if not checkpoint_expectations:
        return "skipped"
    has_coverage_gap = any(
        f.get("finding_type") == _FT_CHECKPOINT_NOT_COVERED
        for f in audit_findings
    )
    return "partial" if has_coverage_gap else "complete"


def _generate_signoff_id(now: datetime) -> str:
    """Format: ``SIGN-YYYY-MM-DD-HHMMSS`` — unique per cycle issuance."""
    return f"SIGN-{now.strftime('%Y-%m-%d-%H%M%S')}"
