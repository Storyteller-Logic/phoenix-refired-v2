"""Fail-loud proofs for the schema substrate (brain.spec §2, §7.5, §7.6 — L4, L6).

Every test here exists to CRASH if the substrate can be talked out of a law.
All proofs run on throwaway files under tmp_path — never on a live Brain.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.substrate import SCHEMA_VERSION, connect, create_brain


@pytest.fixture()
def brain_path(tmp_path: Path) -> Path:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    return path


def _seed_user_agent_session(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Create owner user -> agent -> session; return their ids."""
    cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
    user_id = cur.lastrowid
    assert user_id is not None
    cur = conn.execute(
        "INSERT INTO agents (user_id, name) VALUES (?, 'first-agent')", (user_id,)
    )
    agent_id = cur.lastrowid
    assert agent_id is not None
    cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
    session_id = cur.lastrowid
    assert session_id is not None
    conn.commit()
    return user_id, agent_id, session_id


# --- One file, schema present ------------------------------------------------


def test_create_brain_is_one_file_with_schema(brain_path: Path) -> None:
    assert brain_path.is_file()
    with connect(brain_path) as conn:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None and row[0] == SCHEMA_VERSION


def test_create_brain_refuses_to_overwrite(brain_path: Path) -> None:
    with pytest.raises(FileExistsError):
        create_brain(brain_path)


# --- WAL append-only (L6, criterion A6 schema half) ---------------------------


def test_wal_insert_works_update_and_delete_raise(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        _, _, session_id = _seed_user_agent_session(conn)
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'hello')",
            (session_id,),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE wal SET content = 'tampered' WHERE turn = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM wal WHERE turn = 1")
        row = conn.execute("SELECT content FROM wal WHERE turn = 1").fetchone()
        assert row[0] == "hello"


# --- Blessing gate fail-closed (L4/L5, criterion A5) --------------------------


def test_blessing_gate_raw_connection_fails_closed(brain_path: Path) -> None:
    """A connection that never heard of the gate (raw sqlite3, no registration)
    must be REFUSED — the trigger calls a function the connection doesn't have."""
    raw = sqlite3.connect(brain_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="owner_blessing"):
            raw.execute("INSERT INTO global_settings (key, value) VALUES ('k', 'v')")
    finally:
        raw.close()


def test_blessing_gate_denies_unblessed_connection(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute("INSERT INTO global_settings (key, value) VALUES ('k', 'v')")
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute("INSERT INTO brain_meta (key, value) VALUES ('k', 'v')")


def test_blessing_gate_allows_blessed_connection(brain_path: Path) -> None:
    with connect(brain_path, blessed=True) as conn:
        conn.execute("INSERT INTO global_settings (key, value) VALUES ('k', 'v')")
        conn.commit()
        row = conn.execute("SELECT value FROM global_settings WHERE key = 'k'").fetchone()
        assert row[0] == "v"


def test_blessing_dies_with_the_connection(brain_path: Path) -> None:
    """The privilege is connection-local: a blessed write, then a NEW unblessed
    connection to the same file must be denied. Nothing persists in the file."""
    with connect(brain_path, blessed=True) as conn:
        conn.execute("INSERT INTO global_settings (key, value) VALUES ('a', '1')")
        conn.commit()
    with connect(brain_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute("UPDATE global_settings SET value = '2' WHERE key = 'a'")
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute("DELETE FROM global_settings WHERE key = 'a'")


# --- Layer skeleton: Global -> User -> Agent -> Sub-agent (§2.6, L4) ----------


def test_exactly_one_owner(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO users (name, is_owner) VALUES ('usurper', 1)")


def test_subagent_must_share_parents_user(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        user_id, agent_id, _ = _seed_user_agent_session(conn)
        cur = conn.execute("INSERT INTO users (name) VALUES ('other')")
        other_user = cur.lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            conn.execute(
                "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'thief')",
                (other_user, agent_id),
            )
        conn.execute(
            "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'researcher')",
            (user_id, agent_id),
        )


def test_agent_lineage_is_immutable(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        _, agent_id, _ = _seed_user_agent_session(conn)
        cur = conn.execute("INSERT INTO users (name) VALUES ('other')")
        other_user = cur.lastrowid
        with pytest.raises(sqlite3.IntegrityError, match="lineage"):
            conn.execute(
                "UPDATE agents SET user_id = ? WHERE agent_id = ?", (other_user, agent_id)
            )
        with pytest.raises(sqlite3.IntegrityError, match="lineage"):
            conn.execute(
                "UPDATE agents SET parent_agent_id = ? WHERE agent_id = ?",
                (agent_id, agent_id),
            )


def test_retire_never_delete(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        user_id, agent_id, session_id = _seed_user_agent_session(conn)
        with pytest.raises(sqlite3.IntegrityError, match="retire"):
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        with pytest.raises(sqlite3.IntegrityError, match="retire"):
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        with pytest.raises(sqlite3.IntegrityError, match="retire"):
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE agents SET status = 'retired', retired_reason = 'proof' "
            "WHERE agent_id = ?",
            (agent_id,),
        )


def test_foreign_keys_are_enforced(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO agents (user_id, name) VALUES (999, 'orphan')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) "
                "VALUES (999, 1, 'owner', 'x')"
            )


def test_schema_uses_owner_naming_never_local_name(brain_path: Path) -> None:
    """brain.spec §2.6: the system's term is Owner; the local name appears nowhere."""
    with connect(brain_path) as conn:
        ddl = " ".join(
            row[0].lower() for row in conn.execute(
                "SELECT COALESCE(sql, '') FROM sqlite_master"
            )
        )
    assert "wyld" not in ddl
    assert "owner" in ddl
