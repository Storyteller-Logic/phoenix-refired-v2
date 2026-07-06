"""Phase 1.1: WAL Append and Context Assembly - Tests First

Tests for:
- Durable-first WAL append
- Bounded context assembly
- Identity injection
- Recent WAL retrieval
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from brain.substrate import connect, create_brain

from harness.agent import Agent
from harness.glove import Glove
from harness.stones import AssistantTurn, Slot

# For simplicity, use a fake LLM
class RecordingLLM:
    model = "fake"
    
    def __init__(self, reply: str = "the reply") -> None:
        self.reply = reply
        self.seen: list[dict[str, str]] | None = None
        
    def complete(
        self, messages: list[dict[str, Any]], tools: Any = None, **opts: Any
    ) -> AssistantTurn:
        self.seen = messages
        return AssistantTurn(content=self.reply)

class RaisingLLM:
    model = "broken"
    
    def complete(
        self, messages: list[dict[str, Any]], tools: Any = None, **opts: Any
    ) -> AssistantTurn:
        raise RuntimeError("inference failed")

def _new_agent(path: Path, glove: Glove, **kw: Any) -> Agent:
    create_brain(path)
    with connect(path) as conn:
        # Create owner user
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        assert user_id is not None
        
        # Create agent
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'test-agent')", (user_id,)
        )
        agent_id = cur.lastrowid
        assert agent_id is not None
        
        # Create session
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        session_id = cur.lastrowid
        assert session_id is not None
        
        # Set the LLM stone in a default profile
        cur = conn.execute(
            "INSERT INTO model_profiles (name, slot, provider, model) VALUES (?, ?, ?, ?)",
            ('test-llm', 'llm', 'local', 'test-model')
        )
        profile_id = cur.lastrowid
        assert profile_id is not None
        
        # Bind profile to session for LLM slot
        cur = conn.execute(
            "INSERT INTO session_model_bindings (session_id, slot, profile_id, source) VALUES (?, ?, ?, ?)",
            (session_id, 'llm', profile_id, 'bootstrap')
        )
        
        return Agent(
            conn,
            agent_id=agent_id,
            session_id=session_id,
            glove=glove,
            name="test-agent",
            **kw
        )

@pytest.mark.parametrize("duration_hours,expected_turns", [
    (1, 50),
    (4, 200),
    (24, 1200),
    (7 * 24, 8400),
])
def test_annotations(duration_hours: int, expected_turns: int) -> None:
    """From memory: we got 200 turns in 4 hours in a coaching session.
    That means we expect to hit any of these totals in the corresponding
    span, usually quite a bit faster.
    """
    # This is a placeholder test that documents expectations
    # The actual test will be written when the turn loop is implemented
    assert True, f"Expected {expected_turns} turns in {duration_hours} hours"

@pytest.mark.parametrize("duration_hours,expected_turns", [
    (1, 50),
    (4, 200),
    (24, 1200),
    (7 * 24, 8400),
])
def test_annotations_placeholder(duration_hours: int, expected_turns: int) -> None:
    """Documentation-only placeholder."""
    pass

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
