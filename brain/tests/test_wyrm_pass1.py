"""Fail-loud proofs for the Wyrm chain, link 1 — the builder's adversarial
pass (operation.spec §2.1). Four findings, each CONFIRMED by live probe
before being fixed; these proofs pin the fixes so the cracks never reopen.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

import brain.substrate
from brain.dreams import dream_pass1, full_dream, full_dream_due
from brain.substrate import connect, connect_agent, create_brain


class ToyDistiller:
    def distill(self, transcript: Sequence[tuple[str, str]]) -> list[str]:
        return [c for _, c in transcript]


@pytest.fixture()
def world(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    conn = connect(path)
    conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
    return conn, path


def _agent(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO agents (user_id, name) SELECT user_id, ? FROM users", (name,)
    )
    agent_id = cur.lastrowid
    assert agent_id is not None
    return agent_id


# --- F1: retirement requires a reason, for everyone (L6) ------------------------


def test_users_and_agents_cannot_retire_without_reason(
    world: tuple[sqlite3.Connection, Path],
) -> None:
    conn, _ = world
    agent_id = _agent(conn, "a1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE agents SET status = 'retired' WHERE agent_id = ?", (agent_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE users SET status = 'retired'")
    conn.execute(
        "UPDATE agents SET status = 'retired', retired_reason = 'with cause' "
        "WHERE agent_id = ?",
        (agent_id,),
    )
    conn.execute("UPDATE users SET status = 'retired', retired_reason = 'with cause'")


def test_subagent_retire_through_view_needs_reason_too(
    world: tuple[sqlite3.Connection, Path],
) -> None:
    conn, path = world
    parent = _agent(conn, "parent")
    conn.commit()
    scoped = connect_agent(path, parent)
    scoped.execute("INSERT INTO my_subagents (name) VALUES ('child')")
    with pytest.raises(sqlite3.IntegrityError):
        scoped.execute("UPDATE my_subagents SET status = 'retired' WHERE name = 'child'")
    scoped.execute(
        "UPDATE my_subagents SET status = 'retired', retired_reason = 'done' "
        "WHERE name = 'child'"
    )
    scoped.commit()
    scoped.close()


# --- F2: a retired agent's transcript is closed ---------------------------------


def test_retired_agents_sessions_take_no_new_wal(
    world: tuple[sqlite3.Connection, Path],
) -> None:
    conn, _ = world
    agent_id = _agent(conn, "a1")
    cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
    session_id = cur.lastrowid
    conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'alive')",
        (session_id,),
    )
    conn.execute(
        "UPDATE agents SET status = 'retired', retired_reason = 'proof' "
        "WHERE agent_id = ?",
        (agent_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="retired"):
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 2, 'owner', 'ghost')",
            (session_id,),
        )
    row = conn.execute(
        "SELECT COUNT(wal_id) FROM wal WHERE session_id = ?", (session_id,)
    ).fetchone()
    assert row[0] == 1  # history intact, door closed


def test_dream_pass1_skips_retired_agents_silently(
    world: tuple[sqlite3.Connection, Path],
) -> None:
    conn, _ = world
    agent_id = _agent(conn, "a1")
    cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
    conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'leftover')",
        (cur.lastrowid,),
    )
    conn.execute(
        "UPDATE agents SET status = 'retired', retired_reason = 'proof' "
        "WHERE agent_id = ?",
        (agent_id,),
    )
    assert dream_pass1(conn, ToyDistiller(), agent_id) == 0  # no crash, no memories
    assert conn.execute("SELECT COUNT(memory_id) FROM memories").fetchone()[0] == 0


# --- F3: the global full dream advances every marker -----------------------------


def test_global_full_dream_advances_all_markers(
    world: tuple[sqlite3.Connection, Path],
) -> None:
    conn, _ = world
    agents = [_agent(conn, name) for name in ("a1", "a2")]
    for agent_id in agents:
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        for turn in range(1, 16):
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, 'owner', 'x')",
                (cur.lastrowid, turn),
            )
    assert all(full_dream_due(conn, a) for a in agents)
    full_dream(conn)  # the global pass, no agent named
    assert not any(full_dream_due(conn, a) for a in agents)
    rows = conn.execute("SELECT COUNT(agent_id) FROM agent_dream_state").fetchone()
    assert rows[0] == 2


# --- F4: a failed creation leaves nothing behind ----------------------------------


def test_failed_creation_leaves_no_debris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "stillborn.db"
    monkeypatch.setattr(brain.substrate, "_SCHEMA_SQL", "CREATE TABLE broken (")
    with pytest.raises(sqlite3.OperationalError):
        create_brain(path)
    assert not path.exists()  # no debris posing as a Brain
    monkeypatch.undo()
    create_brain(path)  # the same path works cleanly afterward
    connect(path).close()
