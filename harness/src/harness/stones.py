"""Stones and slots (harness.spec §2)."""

from typing import Any

class Slot:
    LLM = "llm"
    EMBEDDING = "embeddings"
    
class AssistantTurn:
    """An assistant turn from the LLM."""
    
    def __init__(self, content: str, tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        
    @classmethod
    def empty(cls) -> "AssistantTurn":
        return cls(content="")
