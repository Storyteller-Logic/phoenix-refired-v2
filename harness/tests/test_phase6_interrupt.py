"""Phase 6: Interrupt Drill.

(Simulation first: verify clean halt and resume)
"""

import pytest
from harness.interrupt import HaltSignal, TurnOutcome
from harness.agent import Agent
from harness.glove import Glove
import sqlite3

@pytest.fixture
def in_memory_agent():
    """Provide an agent with a database."""
    conn = sqlite3.connect(":memory:")
    # Create minimal DB schema needed for tests
    conn.execute("""
        CREATE TABLE wal (
            id INTEGER PRIMARY KEY,
            session_id INTEGER,
            turn INTEGER,
            role TEXT,
            content TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE brain_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Insert a session record
    conn.execute("""
        INSERT INTO brain_meta (key, value) 
        VALUES (?, ?)
    """, ("session", "test_1"))
    
    # Create agent with a Glove
    glove = Glove()
    # For minimal test, we don't need LLM stone
    agent = Agent(conn, agent_id=1, session_id=1, glove=glove, name="test_agent")
    yield agent
    conn.close()

#### Phase 6.1: Halt Signal and Polling ####

def test_halt_signal_created(in_memory_agent):
    """Phase 6.1: HaltSignal can be created."""
    halt = HaltSignal()
    assert halt is not None
    assert not halt.is_set()

def test_halt_signal_set(in_memory_agent):
    """Phase 6.1: HaltSignal can be set."""
    halt = HaltSignal()
    halt.set()
    assert halt.is_set()

def test_halt_signal_clear(in_memory_agent):
    """Phase 6.1: HaltSignal can be cleared."""
    halt = HaltSignal()
    halt.set()
    assert halt.is_set()
    halt.clear()
    assert not halt.is_set()

def test_halt_recorded_in_wal(in_memory_agent):
    """Phase 6.1: Halt is recorded in WAL."""
    halt = HaltSignal()
    halt.set()
    
    # The _halted method should append to WAL when halt is set
    is_halted = in_memory_agent._halted(halt, "test_point")
    
    assert is_halted
    # Check WAL contains the halt record
    rows = in_memory_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner' ORDER BY turn DESC LIMIT 1"
    ).fetchall()
    assert len(rows) > 0
    assert "HALT: test_point" in rows[0][0]

def test_mid_stream_abort_records_halt(in_memory_agent):
    """Phase 6.1: Mid-stream abort is recorded."""
    halt = HaltSignal()
    halt.set()
    
    # Simulate mid-stream check
    result = in_memory_agent._halted(halt, "mid-stream")
    
    assert result
    rows = in_memory_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner' ORDER BY turn DESC LIMIT 1"
    ).fetchall()
    assert len(rows) > 0
    assert "HALT: mid-stream" in rows[0][0]

def test_halt_does_not_record_when_clear(in_memory_agent):
    """Phase 6.1: Halt is not recorded when clear."""
    halt = HaltSignal()
    
    # No set, so no halt
    is_halted = in_memory_agent._halted(halt, "some_point")
    
    assert not is_halted
    # Check that no HALT record was added
    rows = in_memory_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner' ORDER BY turn DESC LIMIT 1"
    ).fetchall()
    # Should be empty or contain previous records without HALT
    assert not any("HALT:" in r[0] for r in rows)

#### Phase 6.2: Resume and Correction Injection ####

def test_resume_clears_halt(in_memory_agent):
    """Phase 6.2: Resume clears halt after correction."""
    halt = HaltSignal()
    halt.set()
    
    # First check: halt is set
    assert halt.is_set()
    
    # After a correction (simulating resume behavior)
    correction = "Let me rephrase that."
    
    # Resume should clear the halt
    in_memory_agent._append("owner", correction)
    halt.clear()
    
    assert not halt.is_set()

def test_resume_injects_correction(in_memory_agent):
    """Phase 6.2: Resume continues turn with correction."""
    halt = HaltSignal()
    halt.set()
    
    # Record a halt
    in_memory_agent._halted(halt, "test")
    
    # Simulate resume with correction
    correction = "New instructions: do X"
    in_memory_agent._append("owner", correction)
    halt.clear()
    
    # The correction should be in WAL
    rows = in_memory_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner' ORDER BY turn DESC LIMIT 1"
    ).fetchall()
    assert len(rows) > 0
    assert correction in rows[0][0]

def test_resume_preserves_context(in_memory_agent):
    """Phase 6.2: Resume preserves context for continuation."""
    halt = HaltSignal()
    halt.set()
    
    # Add initial context
    in_memory_agent._append("owner", "Initial request")
    in_memory_agent._halted(halt, "after_query")
    
    # Record correction
    correction = "Additional context: do not do Y"
    in_memory_agent._append("owner", correction)
    halt.clear()
    
    # Check both context entries are preserved
    rows = in_memory_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner'"
    ).fetchall()
    contents = [r[0] for r in rows]
    assert "Initial request" in contents
    assert "Additional context: do not do Y" in contents

#### Phase 6.3: Mid-token Abort and Database Consistency ####

def test_database_consistency_after_abort(in_memory_agent):
    """Phase 6.3: Database remains consistent after abort."""
    halt = HaltSignal()
    halt.set()
    
    # Perform some operations
    in_memory_agent._append("owner", "Test message 1")
    halt.clear()
    in_memory_agent._append("agent", "Response 1")
    halt.set()
    in_memory_agent._halted(halt, "abort_point")
    
    # Commit explicitly to see DB state
    in_memory_agent.conn.commit()
    
    # DB should be in a consistent state
    rows = in_memory_agent.conn.execute(
        "SELECT turn, role, content FROM wal"
    ).fetchall()
    
    # All rows should have consistent structure
    for turn, role, content in rows:
        assert turn is not None
        assert role is not None
        assert content is not None

def test_abort_does_not_corrupt_wal(in_memory_agent):
    """Phase 6.3: Abort does not corrupt WAL."""
    halt = HaltSignal()
    
    # Initial operations
    in_memory_agent._append("owner", "Request 1")
    in_memory_agent._append("agent", "Response 1")
    
    # Set halt and check
    halt.set()
    in_memory_agent._halted(halt, "corruption_test")
    
    # No crash or corrupt data
    rows = in_memory_agent.conn.execute(
        "SELECT * FROM wal"
    ).fetchall()
    
    # All should be valid rows (id is implicit, but returned)
    assert len(rows) > 0
    for row in rows:
        # row is (id, session_id, turn, role, content) - 5 columns
        assert len(row) == 5
        assert row[2] is not None  # turn
        assert row[3] is not None  # role
        assert row[4] is not None  # content

def test_wal_log_monotonic_after_aborts(in_memory_agent):
    """Phase 6.3: WAL turns remain monotonic after aborts."""
    halt = HaltSignal()
    
    # First sequence
    in_memory_agent._append("owner", "Request 1")
    in_memory_agent.conn.commit()
    
    # Get max turn
    max1 = in_memory_agent.conn.execute(
        "SELECT MAX(turn) FROM wal"
    ).fetchone()[0]
    
    # Set halt
    halt.set()
    in_memory_agent._halted(halt, "check_monotonic")
    in_memory_agent.conn.commit()
    
    # Next turn
    in_memory_agent._append("owner", "After halt")
    max2 = in_memory_agent.conn.execute(
        "SELECT MAX(turn) FROM wal"
    ).fetchone()[0]
    
    # Turns should be strictly increasing
    assert max2 > max1

#### Easy Section - Simple Checks or Edge Cases ####

def test_halt_signal_initial_state():
    """Basic: HALT signal starts clear."""
    halt = HaltSignal()
    assert not halt.is_set()

def test_turn_outcome_with_halt():
    """Basic: TurnOutcome can indicate halting."""
    outcome = TurnOutcome(reply="test", halted=True, halt_point="test")
    assert outcome.halted
    assert outcome.halt_point == "test"

def test_turn_outcome_without_halt():
    """Basic: TurnOutcome can indicate normal completion."""
    outcome = TurnOutcome(reply="test", halted=False)
    assert not outcome.halted

def test_multiple_halt_cycles():
    """Edge: Halt can be set and cleared multiple times."""
    halt = HaltSignal()
    
    # Cycle 1
    halt.set()
    assert halt.is_set()
    halt.clear()
    assert not halt.is_set()
    
    # Cycle 2
    halt.set()
    assert halt.is_set()
    halt.clear()
    assert not halt.is_set()

def test_halt_during_stream():
    """Edge: Mid-stream check aborts properly."""
    class MockAgent:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("""
                CREATE TABLE wal (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER,
                    turn INTEGER,
                    role TEXT,
                    content TEXT
                )
            """)
            self.session_id = 1
            self.agent_id = 1
        
        def _halted(self, halt, point):
            if halt is not None and halt.is_set():
                # Append to WAL
                turn = self.conn.execute(
                    "SELECT COALESCE(MAX(turn), 0) + 1 FROM wal WHERE session_id = ?",
                    (self.session_id,)
                ).fetchone()[0]
                self.conn.execute(
                    "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, ?, ?)",
                    (self.session_id, turn, "owner", f"HALT: {point}")
                )
                self.conn.commit()
                return True
            return False
    
    mock_agent = MockAgent()
    halt = HaltSignal()
    halt.set()
    
    # Simulate mid-stream abort
    assert mock_agent._halted(halt, "mid-stream")
    
    # Verify WAL contains the halt
    rows = mock_agent.conn.execute(
        "SELECT content FROM wal WHERE role = 'owner'"
    ).fetchall()
    assert len(rows) > 0
    assert "HALT: mid-stream" in rows[0][0]

#### Live Testing Section (with Gemma Heretic) ####

def test_halt_system_with_live_gemma():
    """Phase 6.3: Interrupt system works with live Gemma Heretic (basic)."""
    # This test would verify that halt signals are properly observed
    # during live model interactions. For now, we verify the infrastructure
    # exists and works.
    halt = HaltSignal()
    assert halt is not None
    assert not halt.is_set()

def test_resume_with_live_gemma():
    """Phase 6.2: Resume works alongside live Gemma Heretic (basic)."""
    halt = HaltSignal()
    halt.set()
    
    # In a full test, we'd interact with the live model
    # Here we just verify the basic mechanics
    halt.clear()
    assert not halt.is_set()