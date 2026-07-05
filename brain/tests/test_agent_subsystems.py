"""Fail-loud proofs for the agent sub-system and global-layer tables
(brain.spec §2.1, §2.2, §5.2, §6 — L4, L5, L6, criterion A4).
"""

import sqlite3
from pathlib import Path

import pytest

from brain.skills import SkillError, form_skill_from_evidence, record_skill_use_outcome
from brain.substrate import connect, create_brain

GLOBAL_INSERTS = [
    "INSERT INTO global_knowledge (content) VALUES ('the sky is blue')",
    "INSERT INTO global_hooks (event, action) VALUES ('session_start', 'read truth')",
    "INSERT INTO global_skills (name, content) VALUES ('leash', 'copy-to-test first')",
]


@pytest.fixture()
def brain_path(tmp_path: Path) -> Path:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    return path


@pytest.fixture()
def conn(brain_path: Path) -> sqlite3.Connection:
    return connect(brain_path)


def _agent(conn: sqlite3.Connection, name: str = "a1") -> int:
    row = conn.execute("SELECT user_id FROM users WHERE name = 'owner'").fetchone()
    if row is None:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
    else:
        user_id = row[0]
    cur = conn.execute("INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, name))
    agent_id = cur.lastrowid
    assert agent_id is not None
    return agent_id


def _wal_rows(conn: sqlite3.Connection, agent_id: int, count: int, prefix: str) -> list[int]:
    cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
    session_id = cur.lastrowid
    assert session_id is not None
    ids: list[int] = []
    for turn in range(1, count + 1):
        cur = conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, 'agent', ?)",
            (session_id, turn, f"{prefix} outcome {turn}"),
        )
        assert cur.lastrowid is not None
        ids.append(int(cur.lastrowid))
    return ids


# --- the global layer stays behind the gate (§2.1) ----------------------------


def test_global_tables_refuse_unblessed_writes(conn: sqlite3.Connection) -> None:
    for sql in GLOBAL_INSERTS:
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute(sql)


def test_global_tables_accept_blessed_writes(brain_path: Path) -> None:
    with connect(brain_path, blessed=True) as conn:
        for sql in GLOBAL_INSERTS:
            conn.execute(sql)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM global_knowledge").fetchone()[0] == 1


def test_global_knowledge_is_immutable_even_blessed(brain_path: Path) -> None:
    """A4: global/blessed nodes provably immutable — the blessing opens the
    door for INSERT; nothing opens UPDATE or DELETE. Correction = supersede."""
    with connect(brain_path, blessed=True) as conn:
        cur = conn.execute("INSERT INTO global_knowledge (content) VALUES ('v1')")
        first = cur.lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE global_knowledge SET content = 'edited'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM global_knowledge")
        conn.execute(
            "INSERT INTO global_knowledge (content, supersedes) VALUES ('v2', ?)", (first,)
        )


# --- agent config: settings, identity, hooks (§2.2) ---------------------------


def test_agent_config_tables_work_and_enforce_fks(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "INSERT INTO agent_settings (agent_id, key, value) VALUES (?, 'model', 'qwen')",
        (agent_id,),
    )
    conn.execute(
        "INSERT INTO agent_identity (agent_id, key, value) VALUES (?, 'name', 'Worker')",
        (agent_id,),
    )
    conn.execute(
        "INSERT INTO agent_hooks (agent_id, event, action) VALUES (?, 'turn_end', 'dream')",
        (agent_id,),
    )
    conn.execute(
        "UPDATE agent_identity SET value = 'Worker Prime' WHERE agent_id = ? AND key = 'name'",
        (agent_id,),
    )
    row = conn.execute(
        "SELECT value FROM agent_identity WHERE agent_id = ? AND key = 'name'", (agent_id,)
    ).fetchone()
    assert row[0] == "Worker Prime"  # identity is served live from these rows
    for table in ("agent_settings", "agent_identity"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {table} (agent_id, key, value) VALUES (999, 'k', 'v')"
            )


# --- skills: knowledge with a track record (§5.2) ------------------------------


def test_skill_counters_obey_arithmetic(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "INSERT INTO skills (agent_id, name, content) VALUES (?, 'leash', 'proof first')",
        (agent_id,),
    )
    row = conn.execute("SELECT use_count, success_count FROM skills").fetchone()
    assert row == (0, 0)  # born with an empty track record
    conn.execute("UPDATE skills SET use_count = 3, success_count = 2")
    with pytest.raises(sqlite3.IntegrityError):  # success cannot exceed use
        conn.execute("UPDATE skills SET success_count = 99")
    with pytest.raises(sqlite3.IntegrityError):  # counters never negative
        conn.execute("UPDATE skills SET use_count = -1")


def test_skill_content_immutable_retire_never_delete(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    cur = conn.execute(
        "INSERT INTO skills (agent_id, name, content) VALUES (?, 's1', 'tactic v1')",
        (agent_id,),
    )
    skill_id = cur.lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE skills SET content = 'rewritten' WHERE skill_id = ?", (skill_id,))
    with pytest.raises(sqlite3.IntegrityError, match="retire"):
        conn.execute("DELETE FROM skills WHERE skill_id = ?", (skill_id,))
    with pytest.raises(sqlite3.IntegrityError):  # retire without a reason: CHECK fails
        conn.execute("UPDATE skills SET status = 'retired' WHERE skill_id = ?", (skill_id,))
    cur = conn.execute(
        "INSERT INTO skills (agent_id, name, content) VALUES (?, 's2', 'tactic v2')",
        (agent_id,),
    )
    conn.execute(
        "UPDATE skills SET status = 'retired', retired_reason = 'superseded', "
        "superseded_by = ? WHERE skill_id = ?",
        (cur.lastrowid, skill_id),
    )


def test_skill_supersede_cannot_cross_agents(conn: sqlite3.Connection) -> None:
    a1 = _agent(conn, "a1")
    a2 = _agent(conn, "a2")
    cur = conn.execute(
        "INSERT INTO skills (agent_id, name, content) VALUES (?, 'mine', 'x')", (a1,)
    )
    mine = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO skills (agent_id, name, content) VALUES (?, 'theirs', 'y')", (a2,)
    )
    theirs = cur.lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="agent"):
        conn.execute(
            "UPDATE skills SET status = 'retired', retired_reason = 'r', "
            "superseded_by = ? WHERE skill_id = ?",
            (theirs, mine),
        )


def test_retired_agent_gets_no_new_skills(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "UPDATE agents SET status = 'retired', retired_reason = 'proof' WHERE agent_id = ?",
        (agent_id,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="retired"):
        conn.execute(
            "INSERT INTO skills (agent_id, name, content) VALUES (?, 'late', 'x')",
            (agent_id,),
        )


def test_autonomous_skill_formation_requires_repeated_wal_evidence(
    conn: sqlite3.Connection,
) -> None:
    agent_id = _agent(conn)
    source_wal_ids = _wal_rows(conn, agent_id, 3, "repeat proof")

    result = form_skill_from_evidence(
        conn,
        agent_id=agent_id,
        name="audit-before-claim",
        content="Audit durable SQLite state before claiming a Brain gate passes.",
        source_wal_ids=source_wal_ids,
        evidence_phrase="audit durable SQLite state",
    )

    assert result.created is True
    assert result.source_count == 3
    row = conn.execute(
        "SELECT use_count, success_count, status FROM skills WHERE skill_id = ?",
        (result.skill_id,),
    ).fetchone()
    assert row == (3, 3, "active")
    assert conn.execute(
        "SELECT COUNT(*) FROM skill_sources WHERE skill_id = ? AND outcome = 'success'",
        (result.skill_id,),
    ).fetchone()[0] == 3

    replay = form_skill_from_evidence(
        conn,
        agent_id=agent_id,
        name="audit-before-claim",
        content="Audit durable SQLite state before claiming a Brain gate passes.",
        source_wal_ids=source_wal_ids,
    )
    assert replay.created is False
    assert replay.source_count == 3
    assert conn.execute(
        "SELECT use_count, success_count FROM skills WHERE skill_id = ?",
        (result.skill_id,),
    ).fetchone() == (3, 3)


def test_skill_formation_rejects_sourceless_and_cross_agent_evidence(
    conn: sqlite3.Connection,
) -> None:
    a1 = _agent(conn, "a1")
    a2 = _agent(conn, "a2")
    a1_wal = _wal_rows(conn, a1, 2, "a1")
    a2_wal = _wal_rows(conn, a2, 1, "a2")

    with pytest.raises(SkillError, match="repeated WAL evidence"):
        form_skill_from_evidence(
            conn,
            agent_id=a1,
            name="too-soon",
            content="Do not promote from one-off behavior.",
            source_wal_ids=a1_wal,
        )

    with pytest.raises(SkillError, match="same agent"):
        form_skill_from_evidence(
            conn,
            agent_id=a1,
            name="cross-agent",
            content="Never learn from another agent's private WAL.",
            source_wal_ids=[*a1_wal, *a2_wal],
        )

    assert conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM skill_sources").fetchone()[0] == 0


def test_skill_provenance_is_append_only_and_same_agent(
    conn: sqlite3.Connection,
) -> None:
    a1 = _agent(conn, "a1")
    a2 = _agent(conn, "a2")
    a1_wal = _wal_rows(conn, a1, 3, "a1")
    a2_wal = _wal_rows(conn, a2, 1, "a2")
    result = form_skill_from_evidence(
        conn,
        agent_id=a1,
        name="source-linked",
        content="A skill must cite the WAL outcomes that earned it.",
        source_wal_ids=a1_wal,
    )

    with pytest.raises(sqlite3.IntegrityError, match="crosses agents"):
        conn.execute(
            "INSERT INTO skill_sources (skill_id, wal_id, outcome) VALUES (?, ?, 'success')",
            (result.skill_id, a2_wal[0]),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE skill_sources SET outcome = 'failure' WHERE skill_id = ?",
            (result.skill_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM skill_sources WHERE skill_id = ?", (result.skill_id,))


def test_skill_failures_demote_after_repeated_wal_outcomes(
    conn: sqlite3.Connection,
) -> None:
    agent_id = _agent(conn)
    source_wal_ids = _wal_rows(conn, agent_id, 5, "mixed")
    result = form_skill_from_evidence(
        conn,
        agent_id=agent_id,
        name="fragile-skill",
        content="Use only while the evidence says it works.",
        source_wal_ids=source_wal_ids[:3],
    )

    record_skill_use_outcome(
        conn,
        agent_id=agent_id,
        skill_id=result.skill_id,
        source_wal_id=source_wal_ids[3],
        succeeded=False,
    )
    assert conn.execute(
        "SELECT use_count, success_count, status FROM skills WHERE skill_id = ?",
        (result.skill_id,),
    ).fetchone() == (4, 3, "active")

    record_skill_use_outcome(
        conn,
        agent_id=agent_id,
        skill_id=result.skill_id,
        source_wal_id=source_wal_ids[4],
        succeeded=False,
    )
    assert conn.execute(
        "SELECT use_count, success_count, status, retired_reason "
        "FROM skills WHERE skill_id = ?",
        (result.skill_id,),
    ).fetchone() == (5, 3, "retired", "retired after repeated failed WAL outcomes")


# --- secrets: references only, shaped by the schema (§6) ------------------------


def test_secret_refs_accept_placeholders_only(conn: sqlite3.Connection) -> None:
    agent_id = _agent(conn)
    conn.execute(
        "INSERT INTO secret_refs (agent_id, name, vault_ref) "
        "VALUES (?, 'openrouter', '<openrouter.api_key>')",
        (agent_id,),
    )
    for raw in ("sk-abc123def456", "hunter2", "Bearer eyJhbGciOi", "<noservice>"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO secret_refs (agent_id, name, vault_ref) VALUES (?, 'x', ?)",
                (agent_id, raw),
            )
