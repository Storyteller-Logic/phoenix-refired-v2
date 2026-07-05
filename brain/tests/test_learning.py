"""Fail-loud proofs for the learning verbs (brain.spec §3, §2.4 — A7 first half).

The Learning Law's arithmetic: merit moves worth, the ledger records why,
and every clause is exercised against the verbs — including the ones that
must refuse.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.learning import (
    REINFORCE_STEP,
    LearningError,
    contradict,
    promote,
    reinforce,
    retire,
    supersede,
)
from brain.recall import search
from brain.substrate import connect, create_brain

BIRTH_WORTH = 0.1


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    c = connect(path)
    cur = c.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
    c.execute("INSERT INTO agents (user_id, name) VALUES (?, 'a1')", (cur.lastrowid,))
    return c


def _agent_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT agent_id FROM agents WHERE name = 'a1'").fetchone()
    return int(row[0])


def _memory(conn: sqlite3.Connection, content: str, *, is_failure: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO memories (agent_id, content, is_failure) VALUES (?, ?, ?)",
        (_agent_id(conn), content, is_failure),
    )
    memory_id = cur.lastrowid
    assert memory_id is not None
    return memory_id


def _worth(conn: sqlite3.Connection, memory_id: int) -> float:
    row = conn.execute(
        "SELECT worth FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return float(row[0])


def _wal(conn: sqlite3.Connection, content: str) -> int:
    session = conn.execute(
        "SELECT session_id FROM sessions ORDER BY session_id LIMIT 1"
    ).fetchone()
    if session is None:
        session_id = conn.execute(
            "INSERT INTO sessions (agent_id) VALUES (?)", (_agent_id(conn),)
        ).lastrowid
    else:
        session_id = session[0]
    turn = conn.execute(
        "SELECT COALESCE(MAX(turn), 0) + 1 FROM wal WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    wal_id = conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, 'owner', ?)",
        (session_id, turn, content),
    ).lastrowid
    assert wal_id is not None
    return int(wal_id)


# --- A7 clause 1: reinforced outranks its unreinforced twin ---------------------


def test_reinforced_memory_outranks_unreinforced_twin(conn: sqlite3.Connection) -> None:
    twin_a = _memory(conn, "the same fact, twin A")
    twin_b = _memory(conn, "the same fact, twin B")
    reinforce(conn, twin_a, "recalled and confirmed useful")
    assert _worth(conn, twin_a) > _worth(conn, twin_b)
    ordered = [
        row[0]
        for row in conn.execute(
            "SELECT memory_id FROM memories WHERE content LIKE 'the same fact%' "
            "ORDER BY worth DESC"
        )
    ]
    assert ordered[0] == twin_a  # measurably first in a worth-sorted query


# --- A7 clause 2: a contradicted memory measurably falls ------------------------


def test_contradicted_memory_falls(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "a shaky claim")
    reinforce(conn, memory_id, "used once")  # headroom above the floor
    before = _worth(conn, memory_id)
    after = contradict(conn, memory_id, "proven wrong in use")
    assert after is not None and after < before


def test_balanced_signal_is_a_wash(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "disputed but stable fact")
    reinforce(conn, memory_id, "worked")
    reinforce(conn, memory_id, "worked again")
    contradict(conn, memory_id, "failed once")
    assert _worth(conn, memory_id) == pytest.approx(BIRTH_WORTH + REINFORCE_STEP)


# --- §3.4: promotion is earned, never declared ----------------------------------


def test_promote_refuses_unearned_merit(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "unproven idea")
    with pytest.raises(LearningError, match="earned"):
        promote(conn, memory_id, "I just like it")
    reinforce(conn, memory_id, "proved useful")
    promote(conn, memory_id, "earned by reinforcement")
    row = conn.execute(
        "SELECT status FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "durable"
    events = [
        r[0]
        for r in conn.execute(
            "SELECT event FROM learning_ledger WHERE memory_id = ? ORDER BY event_id",
            (memory_id,),
        )
    ]
    assert events == ["birth", "reinforce", "promote"]
    with pytest.raises(LearningError, match="provisional"):
        promote(conn, memory_id, "again")  # already durable


# --- §3.3: repeated contradiction retires ---------------------------------------


def test_one_contradiction_does_not_retire(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "newborn under fire")
    result = contradict(conn, memory_id, "first strike")
    assert result is not None  # survives, at the floor
    row = conn.execute(
        "SELECT status, worth FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "provisional" and row[1] == 0.0


def test_repeated_contradiction_retires(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "twice-broken claim")
    contradict(conn, memory_id, "first strike")
    result = contradict(conn, memory_id, "second strike")
    assert result is None  # the verb reports the retirement
    row = conn.execute(
        "SELECT status, retired_reason FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "retired" and "contradiction" in row[1]
    events = [
        r[0]
        for r in conn.execute(
            "SELECT event FROM learning_ledger WHERE memory_id = ? ORDER BY event_id",
            (memory_id,),
        )
    ]
    assert events == ["birth", "contradict", "contradict", "retire"]
    with pytest.raises(LearningError, match="retired"):
        reinforce(conn, memory_id, "too late")  # frozen for the verbs too


def test_reinforcement_buys_resilience(conn: sqlite3.Connection) -> None:
    """Net signal: a well-reinforced memory survives two contradictions."""
    memory_id = _memory(conn, "battle-tested fact")
    reinforce(conn, memory_id, "worked")
    reinforce(conn, memory_id, "worked again")
    contradict(conn, memory_id, "strike one")
    assert contradict(conn, memory_id, "strike two") is not None
    row = conn.execute(
        "SELECT status FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert row[0] == "provisional"  # 2 reinforce vs 2 contradict: not net-repeated


# --- §3.5: deliberate retirement with succession ---------------------------------


def test_deliberate_retire_with_successor(conn: sqlite3.Connection) -> None:
    old = _memory(conn, "v1 of the tactic")
    new = _memory(conn, "v2 of the tactic")
    retire(conn, old, "superseded by better tactic", superseded_by=new)
    row = conn.execute(
        "SELECT status, retired_reason, superseded_by FROM memories WHERE memory_id = ?",
        (old,),
    ).fetchone()
    assert row == ("retired", "superseded by better tactic", new)
    events = [
        r[0]
        for r in conn.execute(
            "SELECT event FROM learning_ledger WHERE memory_id = ? ORDER BY event_id",
            (old,),
        )
    ]
    assert events == ["birth", "retire", "supersede"]


def test_evidence_backed_supersede_is_atomic_and_auditable(
    conn: sqlite3.Connection,
) -> None:
    old = _memory(conn, "the project codename is Ember")
    source = _wal(conn, "Correction: the project codename is Phoenix, not Ember.")
    new = supersede(
        conn,
        old,
        "the project codename is Phoenix",
        "Owner supplied a direct correction",
        source_wal_ids=[source, source],
    )
    assert conn.execute(
        "SELECT content, status, worth FROM memories WHERE memory_id = ?", (new,)
    ).fetchone() == ("the project codename is Phoenix", "provisional", BIRTH_WORTH)
    assert conn.execute(
        "SELECT status, retired_reason, superseded_by FROM memories WHERE memory_id = ?",
        (old,),
    ).fetchone() == ("retired", "Owner supplied a direct correction", new)
    assert conn.execute(
        "SELECT wal_id FROM memory_sources WHERE memory_id = ?", (new,)
    ).fetchall() == [(source,)]
    assert [
        row[0]
        for row in conn.execute(
            "SELECT event FROM learning_ledger WHERE memory_id = ? ORDER BY event_id",
            (old,),
        )
    ] == ["birth", "retire", "supersede"]
    assert [
        row[0]
        for row in conn.execute(
            "SELECT event FROM learning_ledger WHERE memory_id = ? ORDER BY event_id",
            (new,),
        )
    ] == ["birth"]


def test_supersede_rolls_back_successor_when_provenance_is_invalid(
    conn: sqlite3.Connection,
) -> None:
    old = _memory(conn, "stale claim")
    other_user = conn.execute(
        "INSERT INTO users (name, is_owner) VALUES ('other', 0)"
    ).lastrowid
    other_agent = conn.execute(
        "INSERT INTO agents (user_id, name) VALUES (?, 'other-agent')",
        (other_user,),
    ).lastrowid
    other_session = conn.execute(
        "INSERT INTO sessions (agent_id) VALUES (?)", (other_agent,)
    ).lastrowid
    foreign_wal = conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) "
        "VALUES (?, 1, 'owner', 'foreign evidence')",
        (other_session,),
    ).lastrowid
    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="never crosses agents"):
        supersede(
            conn,
            old,
            "corrected claim",
            "correction",
            source_wal_ids=[int(foreign_wal or 0)],
        )
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
    assert conn.execute(
        "SELECT status, superseded_by FROM memories WHERE memory_id = ?", (old,)
    ).fetchone() == ("provisional", None)


def test_supersede_requires_evidence(conn: sqlite3.Connection) -> None:
    old = _memory(conn, "stale claim")
    with pytest.raises(LearningError, match="source WAL"):
        supersede(conn, old, "new claim", "correction", source_wal_ids=[])


def test_supersede_rejects_identical_replacement(conn: sqlite3.Connection) -> None:
    old = _memory(conn, "unchanged claim")
    source = _wal(conn, "Evidence repeats the unchanged claim.")
    with pytest.raises(LearningError, match="must differ"):
        supersede(
            conn,
            old,
            "unchanged claim",
            "not actually a correction",
            source_wal_ids=[source],
        )


# --- the verbs refuse ghosts and the frozen --------------------------------------


def test_verbs_refuse_ghosts_and_retired(conn: sqlite3.Connection) -> None:
    with pytest.raises(LearningError, match="no memory"):
        reinforce(conn, 9999, "ghost")
    memory_id = _memory(conn, "to be retired")
    retire(conn, memory_id, "done with it")
    for verb in (reinforce, contradict):
        with pytest.raises(LearningError, match="retired"):
            verb(conn, memory_id, "too late")
    with pytest.raises(LearningError, match="retired"):
        promote(conn, memory_id, "too late")
    with pytest.raises(LearningError, match="retired"):
        retire(conn, memory_id, "twice")


# --- §3.8: failures are first-class ----------------------------------------------


def test_failure_lesson_surfaces_when_context_recurs(conn: sqlite3.Connection) -> None:
    lesson = _memory(
        conn,
        "lesson: chattr immutable flag breaks live sqlite databases",
        is_failure=1,
    )
    reinforce(conn, lesson, "the lesson held")  # identical machinery
    assert _worth(conn, lesson) == pytest.approx(BIRTH_WORTH + REINFORCE_STEP)
    hits = search(conn, "chattr sqlite")  # the context recurs as a search
    assert len(hits) == 1 and hits[0].row_id == lesson


# --- atomicity: data change and ledger event live or die together -----------------


def test_rollback_reverts_worth_and_ledger_together(conn: sqlite3.Connection) -> None:
    memory_id = _memory(conn, "rollback subject")
    conn.commit()
    before_worth = _worth(conn, memory_id)
    before_events = conn.execute(
        "SELECT COUNT(event_id) FROM learning_ledger WHERE memory_id = ?", (memory_id,)
    ).fetchone()[0]
    reinforce(conn, memory_id, "about to vanish")
    conn.rollback()
    assert _worth(conn, memory_id) == before_worth
    after_events = conn.execute(
        "SELECT COUNT(event_id) FROM learning_ledger WHERE memory_id = ?", (memory_id,)
    ).fetchone()[0]
    assert after_events == before_events
