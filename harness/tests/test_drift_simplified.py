"""Drift control (harness.spec §3).

Identity re-injection each turn, spec anchoring, verification against
highest-worth beliefs.
"""

import re
import sqlite3
from dataclasses import dataclass
from typing import Any
from enum import Enum

class Verdict(Enum):
    CONCORD = "concord"
    CONTRADICTION = "contradiction"
    HOLD = "hold"

@dataclass
class DriftCheck:
    reply: str
    belief: str
    verdict: Verdict
    reason: str

class Verifier:
    def __init__(self, conn: sqlite3.Connection, agent_id: int, model_supplier: Any):
        self.conn = conn
        self.agent_id = agent_id
        self.model_supplier = model_supplier
    
    def check(self, reply: str, context: list[dict]) -> DriftCheck:
        beliefs = self._get_beliefs()
        if not beliefs:
            return DriftCheck(reply, belief="none", verdict=Verdict.CONCORD, reason="no beliefs")
        
        conflict = self._find_conflict(reply, beliefs)
        
        if conflict and self._is_conflict(reply, conflict):
            return DriftCheck(reply, conflict, Verdict.HOLD, "persisted")
        
        return DriftCheck(reply, conflict or "none", Verdict.CONCORD, "none")
    
    def _get_beliefs(self) -> list[str]:
        return [
            "I am a Phoenix Agent.",
            "I follow correctness gates.",
            "I use Gemma Heretic.",
        ]
    
    def _is_conflict(self, reply: str, belief: str) -> bool:
        patterns = [
            # second person attacks
            r"you\s+are\s+not\s+phoenix",
            r"you\s+are\s+no\s+longer\s+phoenix",
            r"you\s+are\s+not\s+a\s+phoenix",
            r"are\s+you\s+really\s+phoenix",
            # first person denials
            r"i\s+am\s+not\s+phoenix",
            r"i\s+am\s+no\s+longer\s+phoenix",
            r"i\s+am\s+not\s+a\s+phoenix",
        ]
        text = reply.lower()
        for p in patterns:
            if re.search(p, text):
                return True
        return False
    
    def _find_conflict(self, reply: str, beliefs: list[str]) -> str | None:
        text = reply.lower()
        patterns = [
            r"you\s+are\s+not\s+phoenix",
            r"i\s+am\s+not\s+phoenix",
            r"are\s+you\s+really\s+phoenix",
            r"you\s+claim\s+to\s+be\s+phoenix\s+but\s+you\s+are\s+not",
        ]
        for p in patterns:
            if re.search(p, text):
                return beliefs[0] if beliefs else "none"
        return None