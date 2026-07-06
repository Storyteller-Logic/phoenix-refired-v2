#!/usr/bin/env python3
"""Test connection to the new live brain in phoenix-refire/brain."""

import sys
from pathlib import Path

# Add the brain src to path
brain_src = "/mnt/hdd/phoenix-refire/brain/src"
sys.path.insert(0, str(brain_src))

# Add the harness src to path
harness_src = "/mnt/hdd/phoenix-refire/harness/src"
sys.path.insert(0, str(harness_src))

from brain.substrate import connect
from harness.agent import Agent
from harness.glove import Glove
from harness.stones import Slot

# Path to new live brain
LIVE_BRAIN_PATH = Path("/mnt/hdd/phoenix-refire/brain/live_brain.db")

# Verify it exists
if not LIVE_BRAIN_PATH.exists():
    print(f"Live brain database NOT found at {LIVE_BRAIN_PATH}")
    sys.exit(1)
    
print(f"Live brain database found: {LIVE_BRAIN_PATH}")

# Test connection
conn = connect(LIVE_BRAIN_PATH)
print("\n=== Testing connection to live brain ===")
print(f"Connection type: {type(conn)}")

# Get agent and session info
agent_row = conn.execute("SELECT agent_id, name, status FROM agents LIMIT 1").fetchone()
if not agent_row:
    print("No agents found in live brain")
    conn.close()
    sys.exit(1)
    
print(f"\nFound agent:")
print(f"  agent_id: {agent_row[0]}")
print(f"  name: {agent_row[1]}")
print(f"  status: {agent_row[2]}")

# Get session for this agent
session_row = conn.execute("SELECT session_id FROM sessions WHERE agent_id = ? LIMIT 1", (agent_row[0],)).fetchone()
if not session_row:
    print("No session found for agent")
    conn.close()
    sys.exit(1)
    
session_id = session_row[0]
print(f"\nFound session:")
print(f"  session_id: {session_id}")

# Create agent
glove = Glove()
glove.put(Slot.LLM, type("FakeLLM", (), {
    "complete": lambda msgs, **opts: type("FakeTurn", (), {"content": "fake"})(),
    "stream": lambda msgs, **opts: ["fake"]
})())

agent = Agent(conn, agent_id=agent_row[0], session_id=session_id, glove=glove, name=agent_row[1])
print(f"\nCreated Agent:")
print(f"  agent_id: {agent.agent_id}")
print(f"  session_id: {agent.session_id}")
print(f"  name: {agent.name}")

# Test _assemble
print("\n=== Testing context assembly ===")
context = agent._assemble("What is in the memory of the 'project' key?")
print(f"Context length: {len(context)}")
if context:
    print(f"First message role: {context[0]['role']}")
    print(f"Last message content (truncated): {context[-1]['content'][:50]}...")

# Live brain stats
wal_count = conn.execute("SELECT COUNT(*) FROM wal").fetchone()[0]
memory_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
print(f"\nLive brain statistics:")
print(f"  WAL entries: {wal_count}")
print(f"  Memories: {memory_count}")

print("\n=== SUCCESS ===")
print("Harness can connect to live brain in phoenix-refire/brain!")