"""D13 / S3 — bind Copilot workspaces to ``promotion.Promotion`` authority.

Workspace rows cache ``lifecycle`` as a projection of ``Promotion.state``.
Advances call ``promotion.state_machine.advance`` (D9); they must not grow a
parallel FSM in ``copilot_store``.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def ensure_framework_on_path() -> None:
    """Prefer framework root so ``promotion`` imports resolve in NWI / tests."""
    for candidate in (
        os.getenv("FRAMEWORK_ROOT"),
        "/workspace",
        "/app",
        str(Path(__file__).resolve().parents[3]),
    ):
        if not candidate:
            continue
        root = Path(candidate)
        if (root / "promotion" / "state_machine.py").is_file():
            text = str(root)
            if text not in sys.path:
                sys.path.insert(0, text)
            return


ensure_framework_on_path()

from promotion.models import ApprovalType, Promotion, PromotionState
from promotion.state_machine import advance, get_required_approval_type
from promotion.store import PromotionStore

# Artifact kinds (Copilot) → promotion approval types for UI mid-gates (S6).
_ARTIFACT_TO_APPROVAL = {
    "specification": ApprovalType.SPECIFICATION,
    "strategy_draft": ApprovalType.DRAFT,
    "validation_report": ApprovalType.VALIDATION,
    "candidate_bundle": ApprovalType.CANDIDATE,
}

# Copilot UI advances IDEA → … → CANDIDATE. Paper deploy stays on Supervision /
# command path (not Copilot Advance to APPROVED_FOR_PAPER).
TRANSITIONS_UI: dict[PromotionState, tuple[PromotionState, str]] = {
    PromotionState.IDEA: (PromotionState.SPECIFICATION, "specification"),
    PromotionState.SPECIFICATION: (PromotionState.DRAFT, "strategy_draft"),
    PromotionState.DRAFT: (PromotionState.VALIDATING, "validation_report"),
    PromotionState.VALIDATING: (PromotionState.CANDIDATE, "candidate_bundle"),
}


def promotions_dir() -> Path:
    """Store promotions next to the NWI SQLite DB when possible."""
    env = os.getenv("COPILOT_PROMOTIONS_DIR")
    if env:
        path = Path(env)
    else:
        try:
            import database

            path = Path(database.DB_PATH).resolve().parent / "promotions"
        except Exception:
            path = Path("/tmp/nwi_promotions")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_store() -> PromotionStore:
    return PromotionStore(promotions_dir())


def create_promotion(
    *,
    strategy_name: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> Promotion:
    promotion = Promotion(
        id=f"PROM-{uuid.uuid4().hex.upper()}",
        strategy_name=strategy_name or "unlinked",
        description=description,
        metadata=metadata or {},
    )
    get_store().save(promotion)
    return promotion


def load_promotion(promotion_id: str) -> Promotion:
    return get_store().load(promotion_id)


def project_lifecycle(promotion: Promotion) -> str:
    return promotion.state.value


def transition_eligibility(
    promotion: Promotion,
    *,
    artifact_approved: bool,
    evidence_ok: bool = True,
    evidence_reason: str = "",
) -> dict[str, Any]:
    rule = TRANSITIONS_UI.get(promotion.state)
    if not rule:
        return {
            "eligible": False,
            "target": None,
            "required_artifact_kind": None,
            "reason": (
                "This lifecycle transition is not available from Copilot "
                "(paper deploy and later gates use the Supervision / command path)."
            ),
            "promotion_id": promotion.id,
            "promotion_state": promotion.state.value,
        }
    target, kind = rule
    required = get_required_approval_type(promotion.state, target)
    already = any(a.type == required for a in promotion.approvals) if required else False
    eligible = artifact_approved and evidence_ok and not already
    reason = ""
    if already:
        reason = f"Promotion already has {required.value if required else 'required'} approval."
    elif not artifact_approved:
        reason = f"Approve the current {kind} artifact revision first."
    elif not evidence_ok:
        reason = evidence_reason or f"{kind} evidence is not ready for advance."
    elif target == PromotionState.CANDIDATE:
        reason = ""  # ready — paper deploy remains a separate Supervision step
    return {
        "eligible": eligible,
        "target": target.value,
        "required_artifact_kind": kind,
        "reason": reason,
        "promotion_id": promotion.id,
        "promotion_state": promotion.state.value,
    }


def attach_candidate_bundle(
    promotion: Promotion,
    bundle: dict[str, Any],
) -> Promotion:
    """Persist a review bundle on the Promotion (no git push / paper deploy)."""
    updated = Promotion(
        id=promotion.id,
        strategy_name=promotion.strategy_name,
        description=promotion.description,
        state=promotion.state,
        created_at=promotion.created_at,
        updated_at=datetime.now(UTC).isoformat(),
        approvals=list(promotion.approvals),
        candidate_bundle=bundle,
        paper_observation_start=promotion.paper_observation_start,
        metadata=dict(promotion.metadata),
    )
    get_store().save(updated)
    return updated


def advance_promotion(
    promotion: Promotion,
    *,
    target: PromotionState,
    approver: str,
    payload_hash: str | None,
    notes: str | None = None,
) -> Promotion:
    updated = advance(
        promotion,
        target,
        approver=approver,
        payload_hash=payload_hash,
        notes=notes,
    )
    get_store().save(updated)
    return updated


def reject_promotion(
    promotion: Promotion,
    *,
    approver: str,
    notes: str | None = None,
) -> Promotion:
    updated = advance(
        promotion,
        PromotionState.REJECTED,
        approver=approver,
        approval_type=ApprovalType.SPECIFICATION,
        notes=notes or "Rejected from Copilot workspace",
    )
    get_store().save(updated)
    return updated


__all__ = [
    "TRANSITIONS_UI",
    "_ARTIFACT_TO_APPROVAL",
    "advance_promotion",
    "attach_candidate_bundle",
    "create_promotion",
    "ensure_framework_on_path",
    "get_store",
    "load_promotion",
    "project_lifecycle",
    "promotions_dir",
    "reject_promotion",
    "transition_eligibility",
]
