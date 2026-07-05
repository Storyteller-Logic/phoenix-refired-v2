"""Fail-loud proofs for the knowledge graph + learning ledger
(brain.spec §2.2/§2.4/§3 — L3, L4, L6).

The Learning Law is enforced by the schema, not by request: these proofs
try to break each clause and must see the substrate ABORT.
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


@pytest.fixture()
def conn(brain_path: Path) -> sqlite3.Connection:
    return connect(brain_path)


def _agent(conn: sqlite3.Connection, user: str = "owner", name: str = "a1") -> int:
    row = conn.execute("SELECT user_id FROM users WHERE name = ?", (user,)).fetchone()
    if row is None:
        is_owner = 1 if user == "owner" else 0
        cur = conn.execute(
            "INSERT INTO users (name, is_owner) VALUES (?, ?)", (user, is_owner)
        )
        user_id = cur.lastrowid
    else:
        user_id = row[0]
    cur = conn.execute("INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, name))
    agent_id = cur.lastrowid
    assert agent_id is not None
    return agent_id


def _memory(conn: sqlite3.Connection, agent_id: int, content: str = "a fact") -> int:
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content) VALUES (?, ?)", (agent_id, content)
    )
    memory_id = cur.lastrowid
    assert memory_id is not None
    return memory_id


# --- §3.1 born provisional ----------------------------------------------------


def test_knowledge_is_born_provisional(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    with pytest.raises(sqlite3.IntegrityError, match="provisional"):
        conn.execute(
            "INSERT INTO memories (agent_id, content, status) VALUES (?, 'x', 'durable')",
            (agent_id,),
        )
    memory_id = _memory(conn, agent_id)
    row = conn.execute(
        "SELECT status, worth FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "provisional"
    assert row[1] <= 0.5  # born at low worth


def test_failures_are_first_class(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "INSERT INTO memories (agent_id, content, is_failure) VALUES (?, 'tactic X broke', 1)",
        (agent_id,),
    )
    row = conn.execute("SELECT status FROM memories WHERE is_failure = 1").fetchone()
    assert row[0] == "provisional"  # same machinery as success knowledge


# --- content canonical, owner immutable ---------------------------------------


def test_memory_content_and_owner_are_immutable(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    other = _agent(conn, name="a2")
    memory_id = _memory(conn, agent_id)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE memories SET content = 'rewritten' WHERE memory_id = ?", (memory_id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE memories SET agent_id = ? WHERE memory_id = ?", (other, memory_id)
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE memories SET is_failure = 1 WHERE memory_id = ?", (memory_id,))


# --- retire, never delete (L6) -------------------------------------------------


def test_memories_retire_never_delete(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    with pytest.raises(sqlite3.IntegrityError, match="retire"):
        conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
    with pytest.raises(sqlite3.IntegrityError):  # retired without a reason: CHECK fails
        conn.execute(
            "UPDATE memories SET status = 'retired' WHERE memory_id = ?", (memory_id,)
        )
    conn.execute(
        "UPDATE memories SET status = 'retired', retired_reason = 'proof' "
        "WHERE memory_id = ?",
        (memory_id,),
    )


def test_status_transitions_follow_the_law(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    # provisional -> durable: lawful promotion
    conn.execute("UPDATE memories SET status = 'durable' WHERE memory_id = ?", (memory_id,))
    # durable -> provisional: silent demotion is no transition the law knows
    with pytest.raises(sqlite3.IntegrityError, match="transition"):
        conn.execute(
            "UPDATE memories SET status = 'provisional' WHERE memory_id = ?", (memory_id,)
        )
    # durable -> retired with reason and successor, in one update: lawful
    successor = _memory(conn, agent_id, "the better fact")
    conn.execute(
        "UPDATE memories SET status = 'retired', retired_reason = 'superseded', "
        "superseded_by = ? WHERE memory_id = ?",
        (successor, memory_id),
    )


def test_retired_memory_is_frozen(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    conn.execute(
        "UPDATE memories SET status = 'retired', retired_reason = 'proof' "
        "WHERE memory_id = ?",
        (memory_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="frozen"):
        conn.execute("UPDATE memories SET worth = 9 WHERE memory_id = ?", (memory_id,))
    # un-retiring is stopped by two walls (frozen + lawful-transition);
    # either message proves the abort — the act itself is what's forbidden.
    with pytest.raises(sqlite3.IntegrityError, match="frozen|transition"):
        conn.execute(
            "UPDATE memories SET status = 'provisional' WHERE memory_id = ?", (memory_id,)
        )


def test_supersede_cannot_cross_agents(conn: sqlite3.Connection) -> None:
    a1 = _agent(conn, name="a1")
    a2 = _agent(conn, name="a2")
    m1 = _memory(conn, a1)
    m2 = _memory(conn, a2)
    with pytest.raises(sqlite3.IntegrityError, match="agent"):
        conn.execute(
            "UPDATE memories SET status = 'retired', retired_reason = 'x', "
            "superseded_by = ? WHERE memory_id = ?",
            (m2, m1),
        )


# --- the learning ledger (§2.4) -------------------------------------------------


def test_birth_writes_exactly_one_ledger_row(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    rows = conn.execute(
        "SELECT event, cause FROM learning_ledger WHERE memory_id = ?", (memory_id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "birth"
    assert rows[0][1]  # cause is non-empty


def test_ledger_is_append_only(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    conn.execute(
        "INSERT INTO learning_ledger (memory_id, event, cause) "
        "VALUES (?, 'reinforce', 'recalled and confirmed useful')",
        (memory_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE learning_ledger SET cause = 'rewritten' WHERE event = 'birth'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM learning_ledger WHERE event = 'birth'")


def test_ledger_requires_a_cause(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    memory_id = _memory(conn, agent_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO learning_ledger (memory_id, event, cause) VALUES (?, 'recall', '')",
            (memory_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO learning_ledger (memory_id, event, cause) "
            "VALUES (?, 'invented_event', 'x')",
            (memory_id,),
        )


# --- links and tags: the graph stays inside the agent (L4) ----------------------


def test_links_cannot_cross_agents_or_self(conn: sqlite3.Connection) -> None:
    a1 = _agent(conn, name="a1")
    a2 = _agent(conn, name="a2")
    m1 = _memory(conn, a1)
    m2 = _memory(conn, a1, "related fact")
    foreign = _memory(conn, a2)
    conn.execute(
        "INSERT INTO memory_links (from_memory, to_memory, weight) VALUES (?, ?, 0.5)",
        (m1, m2),
    )
    with pytest.raises(sqlite3.IntegrityError, match="agent"):
        conn.execute(
            "INSERT INTO memory_links (from_memory, to_memory) VALUES (?, ?)", (m1, foreign)
        )
    with pytest.raises(sqlite3.IntegrityError):  # self-link: CHECK fails
        conn.execute(
            "INSERT INTO memory_links (from_memory, to_memory) VALUES (?, ?)", (m1, m1)
        )


def test_tags_are_per_agent_hubs(conn: sqlite3.Connection) -> None:
    a1 = _agent(conn, name="a1")
    a2 = _agent(conn, name="a2")
    m1 = _memory(conn, a1)
    cur = conn.execute("INSERT INTO tags (agent_id, name) VALUES (?, 'sqlite')", (a1,))
    tag_a1 = cur.lastrowid
    cur = conn.execute("INSERT INTO tags (agent_id, name) VALUES (?, 'sqlite')", (a2,))
    tag_a2 = cur.lastrowid
    conn.execute("INSERT INTO memory_tags (memory_id, tag_id) VALUES (?, ?)", (m1, tag_a1))
    with pytest.raises(sqlite3.IntegrityError, match="agent"):
        conn.execute(
            "INSERT INTO memory_tags (memory_id, tag_id) VALUES (?, ?)", (m1, tag_a2)
        )


# --- a retired agent is done -----------------------------------------------------


def test_retired_agent_gets_no_new_memories_or_sessions(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "UPDATE agents SET status = 'retired', retired_reason = 'proof' WHERE agent_id = ?",
        (agent_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="retired"):
        _memory(conn, agent_id)
    with pytest.raises(sqlite3.IntegrityError, match="retired"):
        conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
