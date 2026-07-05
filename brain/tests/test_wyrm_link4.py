"""Wyrm chain link 4 — the final fresh re-run (operation.spec §2.1).

Both models reviewed the changed code again, fresh and unsteered. Every
high-severity claim (global_settings upsert bypass, user_id forgery via
my_subagents, cross-agent WAL writes) was REFUTED by live probe, as were
full_dream idempotency, retired-agent dreams, and the _logical_hash claim
(already fixed in iteration 8). One NEW defect was confirmed: a sub-agent
could be born under a RETIRED parent (the harness base-table path). This
proof pins the fix — the same "retired is done" family as the link-1 F2.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.substrate import connect, create_brain


@pytest.fixture()
def brain_path(tmp_path: Path) -> Path:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    return path


def test_no_subagent_under_a_retired_parent(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'parent')", (user_id,)
        )
        parent = cur.lastrowid
        # an active parent CAN take a sub-agent
        conn.execute(
            "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'early')",
            (user_id, parent),
        )
        conn.execute(
            "UPDATE agents SET status = 'retired', retired_reason = 'done' "
            "WHERE agent_id = ?",
            (parent,),
        )
        # once retired, the parent is done — no new sub-agents
        with pytest.raises(sqlite3.IntegrityError, match="retired"):
            conn.execute(
                "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'late')",
                (user_id, parent),
            )


def test_retiring_a_parent_does_not_block_top_level_agents(brain_path: Path) -> None:
    """The guard must be precise: it blocks NEW sub-agents of a retired
    parent, not the creation of unrelated top-level agents."""
    with connect(brain_path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'parent')", (user_id,)
        )
        conn.execute(
            "UPDATE agents SET status = 'retired', retired_reason = 'done' "
            "WHERE agent_id = ?",
            (cur.lastrowid,),
        )
        conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'fresh-top-level')", (user_id,)
        )  # unrelated top-level agent: still allowed
