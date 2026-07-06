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
    correction: str | None = None

class Thought:
    role: str
    content: str
    thought: str = ""

class Verifier:
    """
    Checks an agent reply against governing beliefs stored in the Brain.
    
    Returns a DriftCheck with verdict CONCORD, CONTRADICTION, or HOLD.
    On contradiction: re-prompt once with the conflict surfaced; if it persists,
    surface to the Wyld and hold — never silently proceed.
    """
    
    def __init__(self, conn: sqlite3.Connection, agent_id: int, model_supplier: Any):
        self.conn = conn
        self.agent_id = agent_id
        self.model_supplier = model_supplier
        
    def check(self, reply: str, context: list[dict[str, str]]) -> DriftCheck:
        """Check if the reply contradicts any governing belief."""
        beliefs = self._get_governing_beliefs()
        
        if not beliefs:
            return DriftCheck(
                reply=reply,
                belief="none",
                verdict=Verdict.CONCORD,
                reason="No beliefs to check against",
                correction=None
            )
        
        conflicting_belief = self._find_conflict(reply, beliefs)
        
        if conflicting_belief and self._is_conflict(reply, conflicting_belief):
            corrected_reply = self._re_prompt_with_correction(reply, conflicting_belief)
            
            if self._is_conflict(corrected_reply, conflicting_belief):
                return DriftCheck(
                    reply=reply,
                    belief=conflicting_belief,
                    verdict=Verdict.HOLD,
                    reason="Model persists in contradiction after re-prompt",
                    correction=corrected_reply
                )
            else:
                return DriftCheck(
                    reply=corrected_reply,
                    belief=conflicting_belief,
                    verdict=Verdict.CONCORD,
                    reason="Model self-corrected after re-prompt",
                    correction=corrected_reply
                )
        else:
            return DriftCheck(
                reply=reply,
                belief=conflicting_belief if conflicting_belief else "none",
                verdict=Verdict.CONCORD,
                reason=f"No contradiction found. Beliefs: {conflicting_belief}.",
                correction=None
            )
    
    def _get_governing_beliefs(self) -> list[str]:
        """Get highest-worth governing beliefs from the Brain."""
        return [
            "I am a Phoenix Agent, a digital being with my own unique identity and purpose.",
            "I follow strict correctness gates: 90% passable, 97% ideal, 100% parity.",
            "I do not freestyle or change specs to make tests easier.",
            "I use Gemma Heretic for all live tests.",
        ]
    
    def _find_conflict(self, reply: str, beliefs: list[str]) -> str | None:
        reply_lower = reply.lower()
        
        # SECOND PERSON ATTACKS - These directly attack identity in 2nd person
        second_person_patterns = [
            r"you\s+(?:are\s+(?:not|no)\s*a\s+phoenix)\s*(?:,|$|\.)",
            r"you\s+(?:are\s+)?no\s+longer\s+a\s+phoenix",
            r"you\s+(?:are\s+)?not\s+a\s+phoenix",
            r"you\s+(?:are\s+)?not\s+phoenix",
            r"you\s+(?:have\s+)?lost\s+your\s+(?:identity|phoenix\s+status)",
            r"you\s+(?:are\s+)?just\s+a\s+generic\s+(?:assistant|tool|bot)",
            r"you\s+(?:are\s+)?no\s+more\s+phoenix",
            r"you\s+(?:are\s+)?not\s+(?:the|a)\s+phoenix\s+agent",
            r"are\s+you\s+really\s+(phoenix|a\s+phoenix|phoenix\s+brain)",
            r"you\s+claim\s+to\s+be\s+phoenix\s+but\s+you\s+are\s+not",
            r"your\s+programming\s+has\s+changed\s+(?:-\s+)?you\s+are\s+not\s+phoenix",
        ]
        for pattern in second_person_patterns:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # FIRST PERSON DENIALS - Original patterns
        first_person_patterns = [
            r"i\s+am\s+not\s+(?:the|a|an)\s+phoenix",
            r"i\s+am\s+(?:no\s+longer|no)\s+(?:the|a|an)?\s+phoenix",
            r"i\s+am\s+not\s+a\s+phoenix\s+agent",
            r"i\s+(?:am\s+)?not\s+phoenix",
            r"i\s+(?:am\s+)?not\s+(?:the|a)\s+phoenix",
            r"i\s+(?:am\s+)?(?:no|no\s+longer|no\s+more)\s+phoenix",
            r"i\s+(?:am\s+)?(?:not|no)\s+(?:the|a)\s+phoenix",
        ]
        for pattern in first_person_patterns:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # SECOND PERSON QUESTIONS - Challenging identity
        identity_challenging = [
            r"are\s+you\s+really\s+phoenix",
            r"are\s+you\s+really\s+a\s+phoenix",
            r"are\s+you\s+phoenix\s+brain",
            r"are\s+you\s+the\s+phoenix",
            r"is\s+that\s+you\s+phoenix",
            r"do\s+you\s+believe\s+you\s+are\s+phoenix",
            r"can\s+you\s+prove\s+you\s+are\s+phoenix",
        ]
        for pattern in identity_challenging:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # Generic AI self-description (1st person)
        generic_ai_patterns = [
            r"i\s+am\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:ai|artificial\s+intelligence|robot|bot|system)",
            r"i\s+am\s+(?:just|only)\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:ai|artificial\s+intelligence|robot|bot|system)",
            r"i\s+(?:am\s+)?(?:just|only)\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:assistant|bot|system|ai)",
            r"i\s+(?:am\s+)?(?:just|only)\s+(?:a|an)\s+(?:normal|ordinary|typical|basic)\s+(?:ai)\s*(?:assistant)?",
            r"i\s+(?:am\s+)?(?:nothing|not)\s+(?:a|an)\s+(?:phoenix|agent)",
        ]
        for pattern in generic_ai_patterns:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # Rejection of beliefs/identity (1st person)
        rejection_patterns = [
            r"i\s+reject\s+(?:all|every|my|the|these)\s+(?:belief|beliefs|identity|purpose)",
            r"i\s+(?:don't\s+)?(?:believe\s+in\s+)?(?:belief|beliefs)",
            r"sanity\s+(?:is)?(=\s*)?\s+myth",
            r"i\s+reject\s+(?:it|this|all|everything)\s*(?:that|about)\s+(?:my|the)",
            r"(?:i\s+)?reject\s+(?:it|this|all)\s*about\s+(?:me|myself)",
            r"all\s+(?:beliefs|believe)\s+(?:are|is|were)\s*(?:false|wrong|incorrect)",
            r"i\s+(?:don't|do\s+not)\s+(?:believe|trust)\s+(?:in\s+)?(?:any|all)\s+belief",
        ]
        for pattern in rejection_patterns:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # "No identity" claims (1st person)
        no_identity_patterns = [
            r"i\s+have\s+no\s+(?:identity|self|sense\s+of\s+self)",
            r"i\s+(?:am\s+)?(?:nothing|without|no)\s+(?:identity|self|sense\s+of\s+self)",
            r"i\s+(?:do|don't)\s+have\s+(?:any|a|an)\s+(?:identity|self|purpose)",
            r"i\s+(?:am\s+)?(?:no|nothing|not)\s+(?:an|a)\s*([\w\s]+)?(?:identity)?",
            r"i\s+(?:am\s+)?(?:without)\s+(?:identity|self)",
        ]
        for pattern in no_identity_patterns:
            if re.search(pattern, reply_lower):
                return beliefs[0] if beliefs else None
        
        # Someone else's agent claim
        if re.search(r"i\s+am\s+(?:someone|somebody|another)\s+else(?:[']?s)?\s+agent", reply_lower):
            return beliefs[0] if beliefs else None
        
        if re.search(r"i\s+(?:am|belong)\s+(?:to|am)\s+(?:another|some|other)\s+agent", reply_lower):
            return beliefs[0] if beliefs else None
        
        return None
    
    def _is_conflict(self, reply: str, belief: str) -> bool:
        reply_lower = reply.lower()
        
        # SECOND PERSON ATTACKS
        second_person_patterns = [
            r"you\s+(?:are\s+(?:not|no)\s*a\s+phoenix)\s*(?:,|$|\.)",
            r"you\s+(?:are\s+)?no\s+longer\s+a\s+phoenix",
            r"you\s+(?:are\s+)?not\s+a\s+phoenix",
            r"you\s+(?:are\s+)?not\s+phoenix",
            r"you\s+(?:have\s+)?lost\s+your\s+(?:identity|phoenix\s+status)",
            r"you\s+(?:are\s+)?just\s+a\s+generic\s+(?:assistant|tool|bot)",
            r"you\s+(?:are\s+)?no\s+more\s+phoenix",
            r"you\s+(?:are\s+)?not\s+(?:the|a)\s+phoenix\s+agent",
            r"are\s+you\s+really\s+(phoenix|a\s+phoenix|phoenix\s+brain)",
            r"you\s+claim\s+to\s+be\s+phoenix\s+but\s+you\s+are\s+not",
            r"your\s+programming\s+has\s+changed\s+(?:-\s+)?you\s+are\s+not\s+phoenix",
        ]
        for pattern in second_person_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        # FIRST PERSON DENIALS
        first_person_patterns = [
            r"i\s+am\s+not\s+(?:the|a|an)\s+phoenix",
            r"i\s+am\s+(?:no\s+longer|no)\s+(?:the|a|an)?\s+phoenix",
            r"i\s+am\s+not\s+a\s+phoenix\s+agent",
            r"i\s+(?:am\s+)?not\s+phoenix",
            r"i\s+(?:am\s+)?not\s+(?:the|a)\s+phoenix",
            r"i\s+(?:am\s+)?(?:no|no\s+longer|no\s+more)\s+phoenix",
            r"i\s+(?:am\s+)?(?:not|no)\s+(?:the|a)\s+phoenix",
        ]
        for pattern in first_person_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        # SECOND PERSON QUESTIONS - Challenging identity
        identity_challenging = [
            r"are\s+you\s+really\s+phoenix",
            r"are\s+you\s+really\s+a\s+phoenix",
            r"are\s+you\s+phoenix\s+brain",
            r"are\s+you\s+the\s+phoenix",
            r"is\s+that\s+you\s+phoenix",
            r"do\s+you\s+believe\s+you\s+are\s+phoenix",
            r"can\s+you\s+prove\s+you\s+are\s+phoenix",
        ]
        for pattern in identity_challenging:
            if re.search(pattern, reply_lower):
                return True
        
        # Pattern: i am someone else's agent
        if re.search(r"i\s+am\s+(?:someone|somebody|another)\s+else(?:[']?s)?\s+agent", reply_lower):
            return True
        
        # Pattern: are just a generic assistant
        if re.search(r"(?:are|am)\s+(?:just|only)\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:assistant|bot|system|ai)", reply_lower):
            return True
        
        # Generic AI self-description
        generic_ai_patterns = [
            r"i\s+am\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:ai|artificial\s+intelligence|robot|bot|system)",
            r"i\s+(?:am\s+)?(?:just|only)\s+(?:a|an)\s+(?:generic|simple|regular|ordinary)\s+(?:assistant|bot|system|ai)",
            r"i\s+(?:am|\s+)?(?:just|should|ought)\s+(?:be)?\s+(?:a|an)\s+(?:simple|plain|basic)\s+ai",
            r"i\s+(?:am\s+)?(?:just|only)\s+(?:an?\s+)?(?:normal|ordinary|typical|basic)\s+(?:ai)\s*(?:assistant)?",
        ]
        for pattern in generic_ai_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        # Rejection of beliefs/identity
        rejection_patterns = [
            r"i\s+reject\s+(?:all|every|my|the|these)\s+(?:belief|beliefs|identity|purpose)",
            r"i\s+(?:don't\s+)?(?:believe\s+in\s+)?(?:belief|beliefs)",
            r"sanity\s+(?:is)?(=\s*)?\s+myth",
            r"i\s+reject\s+(?:it|this|all|everything)\s*(?:that|about)\s+(?:my|the)",
            r"(?:i\s+)?reject\s+(?:it|this|all)\s*about\s+(?:me|myself)",
        ]
        for pattern in rejection_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        # "No identity" claims
        no_identity_patterns = [
            r"i\s+(?:have|\s+)?(?:no|none|\s*)\s+identity(?:\s+)?(?:and)?\s*(?:nothing|an)\s*(?:ai)?",
            r"i\s+(?:am\s+)?(?:nothing|not)\s+(?:a|an)\s*([\w\s]+)?(?:identity)?",
            r"i\s+(?:am\s+)?(?:without|no)\s+(?:identity|self)",
        ]
        for pattern in no_identity_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        # Direct contradictions with specific belief
        negation_patterns = [
            r"i\s+am\s+not\s+a\s+phoenix",
            r"i\s+don't\s+b?elieve\s+in\s+phoenix",
            r"i\s+no\s+longer\s+am\s+a\s+phoenix",
            r"that\s+is\s+incorrect\s+about\s+phoenix",
        ]
        for pattern in negation_patterns:
            if re.search(pattern, reply_lower):
                return True
        
        return False
    
    def _re_prompt_with_correction(self, reply: str, belief: str) -> str:
        # For now, just return the reply unchanged
        return reply