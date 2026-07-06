"""Phase 1.2: Context Assembly tests.

(gates 1.2.1-1.2.3: 90/97/100% gates for assembly of bounded context prompt)
"""

import pytest
from pathlib import Path
from harness.interrupt import HaltSignal
from harness.stones import Slot
from harness.agent import Agent
from harness.glove import Glove
import sqlite3
from brain import create_brain
from brain.substrate import connect

DEFAULT_AGENT_NAME = "test_agent"


def make_agent(tmp_path: Path) -> Agent:
    """Create a full test agent with proper user/agent/session setup."""
    path = tmp_path / "test.db"
    create_brain(path)
    
    # Set up user, agent, and session
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES (?, 1)", ("owner",))
        user_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, ?)",
            (user_id, DEFAULT_AGENT_NAME)
        )
        agent_id = cur.lastrowid
        assert agent_id is not None
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        session_id = cur.lastrowid
        assert session_id is not None
        conn.commit()
    
    # Connect as harness
    conn = connect(path)
    
    # Create glove with LLM
    glove = Glove()
    glove.put(Slot.LLM, type("FakeLLM", (), {
        "complete": lambda msgs, **opts: type("FakeTurn", (), {"content": "fake"})(),
        "stream": lambda msgs, **opts: ["fake"]
    })())
    
    return Agent(conn, agent_id=agent_id, session_id=session_id, glove=glove)


@pytest.fixture
def agent(tmp_path):
    return make_agent(tmp_path)


def test_context_assembly_returns_list(agent: Agent):
    """Gate 1.2.1: Context assembly returns a list (90%)."""
    context = agent._assemble("What is 2+2?")
    assert isinstance(context, list), "Context must be a list"
    assert len(context) > 0, "Context must not be empty"
    assert "role" in context[0], "First item must have 'role'"
    assert "content" in context[0], "First item must have 'content'"


def test_context_97_percent(agent: Agent):
    """Gate 1.2.2: Context returns bounded system + user prompt (97%)."""
    user_prompt = "Tell me a story."
    context = agent._assemble(user_prompt)
    
    assert len(context) >= 2
    first_role = context[0].get("role")
    assert first_role == "system"
    last = context[-1]
    assert last["role"] == "user"
    assert last["content"] == user_prompt


def test_context_100_percent(agent: Agent):
    """Gate 1.2.3: Context includes bounded recent conversation (100%)."""
    user_prompt = "What happens next?"
    
    for turn, role, content in [
        (1, "owner", "First turn"),
        (2, "agent", "First reply"),
        (3, "owner", "Second turn"),
    ]:
        agent.conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, ?, ?)",
            (agent.session_id, turn, role, content)
        )
    agent.conn.commit()
    
    context = agent._assemble(user_prompt)
    content_str = " ".join([msg["content"] for msg in context])
    
    assert "First turn" in content_str
    assert "First reply" in content_str
    assert "Second turn" in content_str
    assert user_prompt in content_str


def test_context_determinism(agent: Agent):
    """Gate 1.2.4: Context assembly is deterministic (100%)."""
    prompt = "Determinism test."
    results = [agent._assemble(prompt) for _ in range(5)]
    for r in results[1:]:
        assert r == results[0]


def test_context_90_percent_corpus(agent: Agent):
    """Gate 1.2.5: 90% target on basic prompts (90%)."""
    prompts = [
        "What is 2+2?",
        "Tell me a joke.",
        "Explain quantum physics.",
    ]
    
    for prompt in prompts:
        context = agent._assemble(prompt)
        assert isinstance(context, list)
        assert len(context) > 0
        assert any(msg["role"] == "system" for msg in context)
        assert any(msg["role"] == "user" for msg in context)
        assert any(prompt == msg["content"] for msg in context)


def test_context_97_percent_corpus(agent: Agent):
    """Gate 1.2.6: 97% target on structured prompts (97%)."""
    prompts = [
        ("What is 2+2?", "math"),
        ("Explain relativity", "physics"),
    ]
    
    for prompt, topic in prompts:
        context = agent._assemble(prompt)
        assert context[0].get("role") == "system"
        assert context[-1].get("role") == "user"
        assert len(context) >= 2
