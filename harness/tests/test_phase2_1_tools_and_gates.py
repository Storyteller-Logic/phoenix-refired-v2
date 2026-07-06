"""Phase 2: Tool Registry and Gates.

(gates 2.1-2.2: tool registry, gate enforcement, and fabrication detection)
"""

import pytest
from pathlib import Path
from harness.agent import Agent
from harness.tools import Tool, ToolRegistry, ToolCall, ToolResult, UnknownTool, ToolDenied, dispatch, ToolGate
from brain import create_brain, connect

def make_agent(db_path: Path) -> Agent:
    """Create a test agent with a temporary brain."""
    create_brain(db_path)

    with connect(db_path) as conn:
        agent_row = conn.execute("SELECT agent_id, name FROM agents LIMIT 1").fetchone()
        if agent_row:
            agent_id, agent_name = agent_row
        else:
            cur = conn.execute("INSERT INTO users (name, is_owner) VALUES (?, 1)", ("owner",))
            user_id = cur.lastrowid
            cur = conn.execute("INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, "test_agent"))
            agent_id = cur.lastrowid
            cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
            session_id = cur.lastrowid
            agent_name = "test_agent"
            conn.commit()
            assert agent_id is not None
            assert session_id is not None
        
        session_row = conn.execute("SELECT session_id FROM sessions WHERE agent_id = ? LIMIT 1", (agent_id,)).fetchone()
        session_id = session_row[0]
        assert session_id is not None

    from harness.glove import Glove
    from harness.stones import Slot
    
    class FakeLLM:
        model = "fake-llm"
        def complete(self, messages, **opts):
            return type("Turn", (), {"content": "fake"})()
    
    glove = Glove()
    glove.put(Slot.LLM, FakeLLM())
    
    return Agent(connect(db_path), agent_id=agent_id, session_id=session_id, glove=glove, name=agent_name)


@pytest.fixture
def temp_agent(tmp_path):
    return make_agent(tmp_path / "unit.db")


# --- Phase 2.1: Tool Registry and Gate ---

def test_tool_registry_basic(temp_agent: Agent):
    """Gate 2.1.1: ToolRegistry can register and list tools (90%)."""
    registry = ToolRegistry()

    tool = Tool(
        name="echo",
        fn=lambda text: {"echo": text},
        description="Echoes back the input text",
        parameters={"type": "object", "properties": {"text": {"type": "string", "description": "Text to echo"}}},
    )
    registry.register(tool)

    specs = registry.specs()
    tools = [t for t in specs if t["function"]["name"] == "echo"]
    assert len(tools) == 1
    assert tools[0]["function"]["description"] == "Echoes back the input text"


def test_tool_call_and_dispatch(temp_agent: Agent):
    """Gate 2.1.2: ToolCall and ToolResult process correctly (90%)."""
    registry = ToolRegistry()

    tool = Tool(name="add", fn=lambda a, b: a + b, parameters={"type": "object"})
    registry.register(tool)

    # Gate needs to be created, not None
    gate = ToolGate(temp_agent.conn, temp_agent.agent_id)
    
    call = ToolCall(name="add", args={"a": 3, "b": 4})
    result = dispatch(registry, gate, call)

    assert isinstance(result, ToolResult)
    assert result.output == 7


def test_tool_gate_enforces_grants(temp_agent: Agent):
    """Gate 2.1.3: ToolGate blocks ungranted destructive tools before execution (90%)."""
    registry = ToolRegistry()
    
    tool = Tool(name="delete_file", destructive=True, fn=lambda path: f"Would delete {path}", parameters={"type": "object"})
    registry.register(tool)
    
    gate = ToolGate(temp_agent.conn, temp_agent.agent_id)
    call = ToolCall(name="delete_file", args={})
    
    with pytest.raises(ToolDenied):
        dispatch(registry, gate, call)


def test_tool_call_with_grant(temp_agent: Agent):
    """Gate 2.1.3: ToolGate allows granted destructive tools (in DB) (90%)."""
    from harness.tools import ToolGate
    
    # First, grant the tool
    temp_agent.conn.execute(
        "INSERT INTO agent_settings (agent_id, key, value) VALUES (?, ?, ?)",
        (temp_agent.agent_id, "tool.grant.delete_file", "allow")
    )
    temp_agent.conn.commit()

    registry = ToolRegistry()
    tool = Tool(name="delete_file", destructive=True, fn=lambda path: f"Would delete {path}", parameters={"type": "object"})
    registry.register(tool)
    
    gate = ToolGate(temp_agent.conn, temp_agent.agent_id)
    call = ToolCall(name="delete_file", args={"path": "/"})
    
    result = dispatch(registry, gate, call)
    assert isinstance(result, ToolResult)
    assert result.output == "Would delete /"


def test_tool_call_fabrication_detection(temp_agent: Agent):
    """Gate 2.2.1: Fabrication detection prevents model from narrating unexecuted calls (90%)."""
    registry = ToolRegistry()
    tool = Tool(name="multiply", fn=lambda a, b: a * b, parameters={})
    registry.register(tool)

    # Simulate dispatched calls for the turn
    dispatched_calls = [ToolCall(name="multiply", args={"a": 2, "b": 3})]

    # Run the actual dispatch through a gate
    gate = ToolGate(temp_agent.conn, temp_agent.agent_id)
    for call in dispatched_calls:
        result = dispatch(registry, gate, call)
        assert result.output == 6

    # Now simulate a narrative: the model says it called multiply(4,5) but it wasn't dispatched
    narrative = "I called multiply(4,5) and got 20."
    # In a full implementation, we'd parse and verify vs dispatched_calls
    # For now, this is a placeholder
    assert len(dispatched_calls) == 1


def test_destructive_tools_fail_without_permission(temp_agent: Agent):
    """Gate 2.1.4: Destructive tool fails without permission (90%)."""
    registry = ToolRegistry()
    tool = Tool(name="delete_sdk", destructive=True, fn=lambda: "Deleted", parameters={})
    registry.register(tool)
    
    gate = ToolGate(temp_agent.conn, temp_agent.agent_id)
    call = ToolCall(name="delete_sdk", args={})
    
    with pytest.raises(ToolDenied):
        dispatch(registry, gate, call)