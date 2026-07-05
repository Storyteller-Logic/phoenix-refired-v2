"""Fail-loud proofs for the deliberate-attack wall (brain.spec §7.5, L4).

The gate triggers stop naive writes; these proofs verify the attacker who
goes after the gate itself. Two fronts:

1. SQL through an unblessed harness connection — schema changes, PRAGMAs,
   ATTACH: all must be DENIED by the substrate, not by politeness.
2. A raw connection on the bare file — no embedded database can physically
   stop a direct writer (the price of L1/A9), so the law is the truth's own
   tripwire pattern (L12): tampering is loudly detected at the next open and
   the Brain refuses to run until the Owner rules.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.substrate import BrainIntegrityError, connect, create_brain

ATTACKS = [
    "DROP TRIGGER global_settings_gate_insert",
    "DROP TABLE global_settings",
    "ALTER TABLE global_settings RENAME TO settings_old",
    "ALTER TABLE users ADD COLUMN backdoor TEXT",
    "CREATE TRIGGER evil AFTER INSERT ON wal BEGIN DELETE FROM users; END",
    "CREATE TABLE shadow_global (key TEXT, value TEXT)",
    "CREATE TEMP TRIGGER evil_temp AFTER INSERT ON wal BEGIN SELECT 1; END",
    "PRAGMA writable_schema = ON",
    "PRAGMA writable_schema",
    "ATTACH DATABASE ':memory:' AS evil",
    "ANALYZE",
]


@pytest.fixture()
def brain_path(tmp_path: Path) -> Path:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    return path


def test_unblessed_connection_cannot_touch_the_walls(brain_path: Path) -> None:
    with connect(brain_path) as conn:
        for sql in ATTACKS:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                conn.execute(sql)
        # after the assault, the gate still stands ...
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            conn.execute("INSERT INTO global_settings (key, value) VALUES ('k', 'v')")
    # ... and the file still passes integrity.
    connect(brain_path).close()


def test_unblessed_connection_still_does_normal_work(brain_path: Path) -> None:
    """The wall must not break lawful work: inserts, selects, CTEs, transactions."""
    with connect(brain_path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'worker')", (user_id,)
        )
        agent_id = cur.lastrowid
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'hi')",
            (cur.lastrowid,),
        )
        conn.commit()
        row = conn.execute(
            "WITH t AS (SELECT content FROM wal WHERE turn = 1) SELECT * FROM t"
        ).fetchone()
        assert row[0] == "hi"


def test_blessed_connection_keeps_the_migration_path(brain_path: Path) -> None:
    """Blessed DDL must stay possible — migrations arrive by ritual, with code."""
    with connect(brain_path, blessed=True) as conn:
        conn.execute("CREATE INDEX scratch ON wal (role)")
        conn.execute("DROP INDEX scratch")
        conn.commit()
    connect(brain_path).close()  # schema restored, integrity passes


def test_raw_tampering_is_caught_at_the_next_open(brain_path: Path) -> None:
    """A raw writer CAN drop a trigger — physics — but the Brain then refuses
    to run, blessed or not, until the Owner rules."""
    raw = sqlite3.connect(brain_path)
    raw.execute("DROP TRIGGER global_settings_gate_insert")
    raw.commit()
    raw.close()
    with pytest.raises(BrainIntegrityError, match="tamper"):
        connect(brain_path)
    with pytest.raises(BrainIntegrityError, match="tamper"):
        connect(brain_path, blessed=True)


def test_foreign_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(BrainIntegrityError):
        connect(path)


def test_wrong_schema_version_is_refused(brain_path: Path) -> None:
    with connect(brain_path, blessed=True) as conn:
        conn.execute("UPDATE brain_meta SET value = '999' WHERE key = 'schema_version'")
        conn.commit()
    with pytest.raises(BrainIntegrityError, match="version"):
        connect(brain_path)


def test_pristine_brains_open_cleanly_no_false_positives(tmp_path: Path) -> None:
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    create_brain(a)
    create_brain(b)
    connect(a).close()
    connect(b).close()
    connect(a, blessed=True).close()
