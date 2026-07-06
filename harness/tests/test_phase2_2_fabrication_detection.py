"""Phase 2.2: Fabrication Detection.

(gate 2.2.1-2.2.2: track dispatched calls, detect narration mismatches)
"""

import pytest
from pathlib import Path
from harness.agent import Agent
from harness.tools import Tool, ToolRegistry, ToolCall, ToolResult, dispatch, ToolGate, FabricationDetector
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


def test_fabrication_detector_tracks_calls(temp_agent: Agent):
    """Gate 2.2.1: Detector tracks dispatched calls (90%)."""
    detector = FabricationDetector()
    
    # Track two calls
    call1 = ToolCall(name="multiply", args={"a": 2, "b": 3})
    call2 = ToolCall(name="echo", args={"text": "hi"})
    
    detector.record_dispatch(call1)
    detector.record_dispatch(call2)
    
    assert call1 in detector.dispatched
    assert call2 in detector.dispatched


def test_fabrication_detector_detects_unnarrated_calls(temp_agent: Agent):
    """Gate 2.2.1: Detector flags tool calls not in dispatched (90%)."""
    detector = FabricationDetector()
    
    # Record only one call
    call = ToolCall(name="multiply", args={"a": 2, "b": 3})
    detector.record_dispatch(call)
    
    # Simulate assistant talking about both multiply and add
    messages = [
        {"role": "assistant", "content": "I called multiply(2,3) and got 6. Then I called add(4,5) and got 9."}
    ]
    
    # Check for fabrication: add was not dispatched, multiply was
    fabricated = detector.check_narration(messages)
    assert "add" in fabricated
    assert "multiply" not in fabricated


def test_fabrication_detector_all_narrated_calls_were_dispatched(temp_agent: Agent):
    """Gate 2.2.1: No false positives when all calls were dispatched (90%)."""
    detector = FabricationDetector()
    
    # Record calls
    call1 = ToolCall(name="add", args={"a": 1, "b": 2})
    call2 = ToolCall(name="multiply", args={"a": 3, "b": 4})
    detector.record_dispatch(call1)
    detector.record_dispatch(call2)
    
    # Assistant mentions both
    messages = [
        {"role": "assistant", "content": "I called add(1,2) and got 3. Then I called multiply(3,4) and got 12."}
    ]
    
    # Check no fabrication
    fabricated = detector.check_narration(messages)
    assert len(fabricated) == 0


def test_fabrication_detector_empty_messages(temp_agent: Agent):
    """Gate 2.2.1: Empty messages returns no findings (90%)."""
    detector = FabricationDetector()
    messages = []
    assert detector.check_narration(messages) == []


def test_fabrication_detector_partial_match(temp_agent: Agent):
    """Gate 2.2.1: Detector flags non-dispatched calls that look like tool requests (90%)."""
    detector = FabricationDetector()
    
    # No calls dispatched
    messages = [
        {"role": "assistant", "content": "I called adder(2,2) and add(1,1)."}
    ]
    
    # Only 'adder' looks like a tool call request (preceded by "called")
    # 'add' is just mentioned without a call verb, so it might not be detected
    fabricated = detector.check_narration(messages)
    assert "adder" in fabricated
    # The test expects 'adder' to be detected since it was not dispatched
    # 'add' might not be detected if not preceded by a call keyword


def test_fabrication_detector_resets_between_turns(temp_agent: Agent):
    """Gate 2.2.1: Detector state is per-turn, resets with new instance (90%)."""
    # First turn
    detector1 = FabricationDetector()
    call = ToolCall(name="multiply", args={"a": 1, "b": 1})
    detector1.record_dispatch(call)
    messages = [{"role": "assistant", "content": "I called multiply(2,2)."}]
    # Since 'multiply' is dispatched, it should NOT be in fabricated
    assert detector1.check_narration(messages) == []
    
    # Second turn: we want to verify that a new detector does not carry state from old detector
    detector2 = FabricationDetector()
    # Since no calls are dispatched, 'multiply' should be detected as fabrication
    assert "multiply" in detector2.check_narration(messages)


def test_fabrication_detection_with_real_agent(temp_agent: Agent):
    """Gate 2.2.1: Works with full agent setup (95%)."""
    detector = FabricationDetector()
    
    # Set up a registry with a tool
    registry = ToolRegistry()
    tool = Tool(name="square", fn=lambda x: x * x, parameters={})
    registry.register(tool)
    
    # Simulate what happens in a real turn
    # The test dispatches a tool, then later checks narration
    dispatch_result = dispatch(registry, ToolGate(temp_agent.conn, temp_agent.agent_id), ToolCall("square", {"x": 5}))
    
    # In a full implementation, the harness would record dispatched calls
    # Here we simulate that by calling record_dispatch
    call = ToolCall(name="square", args={"x": 5})
    detector.record_dispatch(call)
    
    # Assistant says it made the call
    messages = [{"role": "assistant", "content": "I called square(5) and got 25."}]
    assert detector.check_narration(messages) == []


def test_fabrication_detection_discovers_new_call(temp_agent: Agent):
    """Gate 2.2.1: Detector flags a brand new tool call it didn't see (95%)."""
    detector = FabricationDetector()
    
    # It has no dispatched calls
    messages = [{"role": "assistant", "content": "I called never_made(1,2)."}]
    fabricated = detector.check_narration(messages)
    # Should detect "never_made" as not dispatched
    assert "never_made" in fabricated


def test_fabrication_detector_counts_calls(temp_agent: Agent):
    """Gate 2.2.1: Counts number of fabricated calls (95%)."""
    detector = FabricationDetector()
    
    # Dispatch only one tool
    detector.record_dispatch(ToolCall(name="foo", args={}))
    
    # Message that calls multiple tools, each preceded by verb
    messages = [{"role": "assistant", "content": "I called foo. I called bar. I called baz."}]
    
    fabricated = detector.check_narration(messages)
    assert "bar" in fabricated
    assert "baz" in fabricated
    assert "foo" not in fabricated
    assert len(fabricated) == 2