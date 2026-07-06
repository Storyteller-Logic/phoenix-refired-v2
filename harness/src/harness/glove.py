"""The Infinity Glove (harness.spec §2)."""

from typing import Any

class Glove:
    """The glove holds stones that can be swapped."""
    
    def __init__(self) -> None:
        self.stones: dict[str, Any] = {}
        
    def put(self, slot: str, stone: Any) -> None:
        """Put a stone in a slot."""
        self.stones[slot] = stone
        
    def require(self, slot: str) -> Any:
        """Get a required stone, raise if missing."""
        if slot not in self.stones:
            raise MissingStone(f"Missing {slot} stone")
        return self.stones[slot]
        
    def swap(self, slot: str, stone: Any) -> None:
        """Swap a stone in a slot."""
        self.stones[slot] = stone

class MissingStone(RuntimeError):
    """A required stone is missing."""
    pass
