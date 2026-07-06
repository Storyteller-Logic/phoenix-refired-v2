"""Interruption system (harness.spec §4)."""

from typing import Any

class HaltSignal:
    """A flag that can be checked and set."""
    
    def __init__(self) -> None:
        self._set = False
        
    def is_set(self) -> bool:
        return self._set
        
    def set(self) -> None:
        self._set = True
        
    def clear(self) -> None:
        self._set = False

class TurnOutcome:
    """Result of a turn."""
    
    def __init__(
        self,
        reply: str | None = None,
        halted: bool = False,
        held: bool = False,
        halt_point: str | None = None,
        error: str | None = None,
        drift_reason: str | None = None,
    ) -> None:
        self.reply = reply
        self.halted = halted
        self.held = held
        self.halt_point = halt_point
        self.error = error
        self.drift_reason = drift_reason
