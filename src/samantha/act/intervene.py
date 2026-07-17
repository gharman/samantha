"""One-at-a-time in-memory intervention lifecycle."""
from __future__ import annotations
import uuid
from typing import Any
class InterventionManager:
    def __init__(self, resident: Any, exposures: dict[str, Any]) -> None:
        self.resident, self.exposures, self.active = resident, exposures, None
    def apply(self, knob: str, value: Any, prediction: dict[str, Any]) -> str | None:
        if self.active or not prediction or not self.resident.ledger.authorized(knob, value):
            self.resident.journal.write("proposal", id=uuid.uuid4().hex[:8], kind="config", body={"knob": knob, "value": value}, rationale="not currently authorized")
            return None
        exposed = self.exposures[knob]; old = exposed.get(); exposed.set(value)
        ident = uuid.uuid4().hex[:8]; self.active = {"id": ident, "knob": knob, "old": old, "new": value, "prediction": prediction}
        self.resident.journal.write("intervention", id=ident, knob=knob, old=old, new=value, prediction=prediction, status="active"); return ident
    def evaluate(self, measured: dict[str, Any], success: bool) -> None:
        if not self.active: return
        item = self.active
        if not success:
            self.exposures[item["knob"]].set(item["old"]); self.resident.journal.write("revert", intervention_id=item["id"], reason="prediction failed")
        self.resident.journal.write("outcome", intervention_id=item["id"], verdict="kept" if success else "reverted", measured=measured)
        self.resident.ledger.outcome(item["knob"], success); self.active = None
    def revert_all(self) -> None:
        if self.active: self.exposures[self.active["knob"]].set(self.active["old"]); self.resident.journal.write("revert", intervention_id=self.active["id"], reason="operator revert-all"); self.active = None

