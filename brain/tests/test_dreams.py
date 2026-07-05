"""Fail-loud proofs for dreams (brain.spec §5.3 — R14, A8 crash-mid-dream).

The distiller here is a deterministic toy: the Brain's criteria measure the
dream MECHANICS — markers, idempotency, crash-atomicity, reconciliation —
not any particular model. The model-backed distiller arrives with the
Harness.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.dreams import dream_pass1, full_dream, full_dream_due, pass1_due
from brain.learning import reinforce
from brain.substrate import connect, connect_agent, create_brain


class ToyDistiller:
    """Distills every line containing 'remember:' into a memory."""

    def distill(self, transcript: Sequence[tuple[str, str]]) -> list[str]:
        return [
            content.split("remember:", 1)[1].strip()
            for _, content in transcript
            if "remember:" in content
        ]


DISTILLER = ToyDistiller()


@pytest.fixture()
def world(tmp_path: Path) -> tuple[sqlite3.Connection, int, int]:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    conn = connect(path)
    cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
    cur = conn.execute(
        "INSERT INTO agents (user_id, name) VALUES (?, 'dreamer')", (cur.lastrowid,)
    )
    agent_id = cur.lastrowid
    assert agent_id is not None
    cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
    session_id = cur.lastrowid
    assert session_id is not None
    return conn, agent_id, session_id


def _say(conn: sqlite3.Connection, session_id: int, turn: int, content: str) -> None:
    conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, 'owner', ?)",
        (session_id, turn, content),
    )


def test_pass1_distills_new_rows_into_provisional_memories(
    world: tuple[sqlite3.Connection, int, int],
) -> None:
    conn, agent_id, session_id = world
    _say(conn, session_id, 1, "hello there")
    _say(conn, session_id, 2, "remember: the gate fails closed")
    _say(conn, session_id, 3, "remember: markers live in the file")
    created = dream_pass1(conn, DISTILLER, agent_id)
    assert created == 2
    rows = conn.execute(
        "SELECT content, status, agent_id FROM memories ORDER BY memory_id"
    ).fetchall()
    assert rows == [
        ("the gate fails closed", "provisional", agent_id),
        ("markers live in the file", "provisional", agent_id),
    ]
    births = conn.execute(
        "SELECT COUNT(event_id) FROM learning_ledger WHERE event = 'birth'"
    ).fetchone()[0]
    assert births == 2  # the substrate recorded each birth mechanically
    provenance = conn.execute(
        "SELECT COUNT(*) FROM memory_sources"
    ).fetchone()[0]
    assert provenance == 6  # both memories cite all three source WAL rows


def test_pass1_is_idempotent_and_incremental(
    world: tuple[sqlite3.Connection, int, int],
) -> None:
    conn, agent_id, session_id = world
    _say(conn, session_id, 1, "remember: first fact")
    assert dream_pass1(conn, DISTILLER, agent_id) == 1
    assert dream_pass1(conn, DISTILLER, agent_id) == 0  # idempotent: nothing new
    _say(conn, session_id, 2, "remember: second fact")
    _say(conn, session_id, 3, "noise line")
    assert dream_pass1(conn, DISTILLER, agent_id) == 1  # only the new rows
    assert dream_pass1(conn, DISTILLER, agent_id) == 0


def test_pass1_with_no_work_is_silent(world: tuple[sqlite3.Connection, int, int]) -> None:
    conn, agent_id, _ = world
    assert dream_pass1(conn, DISTILLER, agent_id) == 0  # no error, no noise


def test_crash_mid_pass1_no_duplicates_no_loss(
    world: tuple[sqlite3.Connection, int, int],
) -> None:
    """A8 second half: a dream that dies mid-flight leaves nothing behind,
    and the next dream does the work exactly once."""
    conn, agent_id, session_id = world
    _say(conn, session_id, 1, "remember: survives the crash")
    conn.commit()
    assert dream_pass1(conn, DISTILLER, agent_id) == 1
    conn.rollback()  # the simulated crash: memories AND marker revert together
    assert conn.execute("SELECT COUNT(memory_id) FROM memories").fetchone()[0] == 0
    mark = conn.execute("SELECT last_wal_id FROM dream_marks").fetchone()
    assert mark is None or mark[0] == 0
    assert dream_pass1(conn, DISTILLER, agent_id) == 1  # done again, exactly once
    conn.commit()
    assert dream_pass1(conn, DISTILLER, agent_id) == 0
    assert conn.execute("SELECT COUNT(memory_id) FROM memories").fetchone()[0] == 1


def test_markers_are_forward_only_and_undeletable(
    world: tuple[sqlite3.Connection, int, int],
) -> None:
    conn, agent_id, session_id = world
    _say(conn, session_id, 1, "remember: a fact")
    dream_pass1(conn, DISTILLER, agent_id)
    with pytest.raises(sqlite3.IntegrityError, match="forward"):
        conn.execute("UPDATE dream_marks SET last_wal_id = 0")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM dream_marks")
    full_dream(conn, agent_id)
    with pytest.raises(sqlite3.IntegrityError, match="forward"):
        conn.execute("UPDATE agent_dream_state SET last_full_wal_id = 0")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM agent_dream_state")


def test_full_dream_reconciles_view_written_ledger_drift(
    world: tuple[sqlite3.Connection, int, int], tmp_path: Path
) -> None:
    """Scoped agents can write ledger events through my_ledger with no
    arithmetic attached. The full dream makes the ledger canonical: worth
    is recomputed from it, and net +2 earns promotion."""
    conn, agent_id, _ = world
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content) VALUES (?, 'drifted fact')", (agent_id,)
    )
    memory_id = cur.lastrowid
    conn.commit()
    scoped = connect_agent(tmp_path / "test-brain.db", agent_id)
    for cause in ("used well", "used again"):
        scoped.execute(
            "INSERT INTO my_ledger (memory_id, event, cause) VALUES (?, 'reinforce', ?)",
            (memory_id, cause),
        )
    scoped.commit()
    scoped.close()
    row = conn.execute(
        "SELECT worth, status FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row == (0.1, "provisional")  # the drift: events exist, worth never moved
    report = full_dream(conn, agent_id)
    assert report["reconciled"] == 1 and report["promoted"] == 1
    row = conn.execute(
        "SELECT worth, status FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == pytest.approx(0.3) and row[1] == "durable"
    promote_event = conn.execute(
        "SELECT cause FROM learning_ledger WHERE memory_id = ? AND event = 'promote'",
        (memory_id,),
    ).fetchone()
    assert promote_event is not None and "dream" in promote_event[0]


def test_full_dream_retires_net_contradicted(
    world: tuple[sqlite3.Connection, int, int],
) -> None:
    conn, agent_id, _ = world
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content) VALUES (?, 'twice broken')", (agent_id,)
    )
    memory_id = cur.lastrowid
    for cause in ("failed once", "failed twice"):
        conn.execute(
            "INSERT INTO learning_ledger (memory_id, event, cause) "
            "VALUES (?, 'contradict', ?)",
            (memory_id, cause),
        )
    report = full_dream(conn, agent_id)
    assert report["retired"] == 1
    row = conn.execute(
        "SELECT status, retired_reason FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "retired" and "contradiction" in row[1]


def test_full_dream_is_idempotent(world: tuple[sqlite3.Connection, int, int]) -> None:
    conn, agent_id, _ = world
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content) VALUES (?, 'stable fact')", (agent_id,)
    )
    reinforce(conn, cur.lastrowid or 0, "verb-maintained")
    first = full_dream(conn, agent_id)
    assert first == {"reconciled": 0, "promoted": 0, "retired": 0}  # verbs kept it true
    second = full_dream(conn, agent_id)
    assert second == {"reconciled": 0, "promoted": 0, "retired": 0}


def test_cadence_from_durable_state(world: tuple[sqlite3.Connection, int, int]) -> None:
    conn, agent_id, session_id = world
    assert not pass1_due(conn, agent_id)
    for turn in range(1, 5):
        _say(conn, session_id, turn, f"line {turn}")
    assert not pass1_due(conn, agent_id)  # 4 < default 5
    _say(conn, session_id, 5, "line 5")
    assert pass1_due(conn, agent_id)  # 5 >= 5
    dream_pass1(conn, DISTILLER, agent_id)
    assert not pass1_due(conn, agent_id)  # the marker advanced
    conn.execute(
        "INSERT INTO agent_settings (agent_id, key, value) VALUES (?, 'dream.pass1_every', '2')",
        (agent_id,),
    )
    _say(conn, session_id, 6, "line 6")
    assert not pass1_due(conn, agent_id)
    _say(conn, session_id, 7, "line 7")
    assert pass1_due(conn, agent_id)  # per-agent override honored
    assert not full_dream_due(conn, agent_id)  # 7 < default 15
    for turn in range(8, 16):
        _say(conn, session_id, turn, f"line {turn}")
    assert full_dream_due(conn, agent_id)  # 15 >= 15
    full_dream(conn, agent_id)
    assert not full_dream_due(conn, agent_id)


def test_scoped_cannot_touch_dream_tables(
    world: tuple[sqlite3.Connection, int, int], tmp_path: Path
) -> None:
    conn, agent_id, _ = world
    conn.commit()
    scoped = connect_agent(tmp_path / "test-brain.db", agent_id)
    for sql in [
        "SELECT * FROM dream_marks",
        "UPDATE dream_marks SET last_wal_id = 99",
        "SELECT * FROM agent_dream_state",
    ]:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
            scoped.execute(sql)
    scoped.close()
