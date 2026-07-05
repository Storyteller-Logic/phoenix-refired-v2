"""Fail-loud proofs for the FTS mirror and the search() verb
(brain.spec §2.3, §4 — criterion A6 second half, R10 keyword half).

Design fact, measured during this build: FTS5's internal shadow-table
statements present to the authorizer as top-level SQL, so the index can
never be exposed to scoped connections at all. Mirrors are maintained by
sync_fts() on harness connections behind a forward-only marker.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.recall import search
from brain.substrate import (
    FtsDriftError,
    check_fts,
    connect,
    connect_agent,
    create_brain,
    rebuild_fts,
    sync_fts,
)


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, int, int]:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        agents = []
        for name, fact, line in [
            (
                "alpha",
                "the gauntlet walks model user agent session chat",
                "alpha spoke of sqlite triggers",
            ),
            (
                "beta",
                "the wyrm chain reviews with gemma and qwen",
                "beta spoke of llama swap",
            ),
        ]:
            cur = conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, name)
            )
            agent_id = cur.lastrowid
            assert agent_id is not None
            agents.append(agent_id)
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)", (agent_id, fact)
            )
            cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', ?)",
                (cur.lastrowid, line),
            )
        conn.commit()
    return path, agents[0], agents[1]


# --- search: self-syncing, both stores, agent filter, sane ranking --------------


def test_search_finds_memories_and_wal(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        hits = search(conn, "gauntlet")
        assert [h.kind for h in hits] == ["memory"]
        assert "gauntlet" in hits[0].content
        hits = search(conn, "spoke")
        assert {h.kind for h in hits} == {"wal"}
        assert len(hits) == 2
        hits = search(conn, "sqlite triggers")
        assert len(hits) == 1 and hits[0].kind == "wal"


def test_scoped_inserts_are_found_after_self_sync(world: tuple[Path, int, int]) -> None:
    path, alpha, _ = world
    a = connect_agent(path, alpha)
    a.execute("INSERT INTO my_memories (content) VALUES ('scoped breadcrumb xyzzy')")
    a.commit()
    a.close()
    with connect(path) as conn:
        hits = search(conn, "xyzzy")  # search self-syncs: the scoped row is caught up
        assert len(hits) == 1 and hits[0].kind == "memory"


def test_search_filters_by_agent(world: tuple[Path, int, int]) -> None:
    path, alpha, beta = world
    with connect(path) as conn:
        assert {h.agent_id for h in search(conn, "spoke")} == {alpha, beta}
        only_alpha = search(conn, "spoke", agent_id=alpha)
        assert len(only_alpha) == 1 and only_alpha[0].agent_id == alpha


def test_search_returns_empty_not_noise(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        assert search(conn, "absentwordnowherefound") == []
        assert search(conn, "   ") == []


def test_search_survives_hostile_queries(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        for hostile in ['"', "AND", "x OR y", "); DROP TABLE wal;--", "a*b(c", "NEAR/3"]:
            search(conn, hostile)  # must not raise FTS syntax errors


def test_search_ranks_by_relevance(world: tuple[Path, int, int]) -> None:
    path, alpha, _ = world
    with connect(path) as conn:
        cur = conn.execute("SELECT session_id FROM sessions WHERE agent_id = ?", (alpha,))
        session = cur.fetchone()[0]
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 2, 'agent', "
            "'wyrm wyrm wyrm wyrm — dense mention')",
            (session,),
        )
        conn.commit()
        hits = search(conn, "wyrm")
        assert len(hits) == 2
        assert "dense mention" in hits[0].content  # denser match ranks first


# --- the scoped blackout ---------------------------------------------------------


def test_scoped_connections_cannot_touch_the_index(world: tuple[Path, int, int]) -> None:
    """The index cannot row-filter and its shadow tables are raw content:
    a scoped connection gets nothing — reads, writes, MATCH, marker."""
    path, alpha, _ = world
    a = connect_agent(path, alpha)
    # denial surfaces three ways depending on where the wall catches it:
    # the statement ("not authorized"), the column ("prohibited"), or the
    # vtable constructor whose internal reads are denied — all are refusals.
    for sql in [
        "SELECT * FROM wal_fts WHERE wal_fts MATCH 'spoke'",
        "SELECT * FROM memories_fts WHERE memories_fts MATCH 'wyrm'",
        "SELECT * FROM wal_fts_data",
        "SELECT * FROM memories_fts_data",
        "INSERT INTO wal_fts (rowid, content) VALUES (999, 'phantom')",
        "SELECT * FROM fts_sync",
        "UPDATE fts_sync SET last_rowid = 0",
    ]:
        with pytest.raises(
            sqlite3.DatabaseError,
            match="not authorized|prohibited|vtable constructor failed",
        ):
            a.execute(sql)
    a.close()


# --- the mirror cannot drift silently (A6) ---------------------------------------


def test_injected_drift_is_caught_and_repaired(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        sync_fts(conn)
        check_fts(conn)  # pristine: passes
        conn.execute("INSERT INTO wal_fts (rowid, content) VALUES (999, 'phantom row')")
        conn.commit()
        with pytest.raises(FtsDriftError):
            check_fts(conn)
        rebuild_fts(conn)
        check_fts(conn)  # repaired: passes again


def test_sync_is_idempotent_no_double_posting(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        sync_fts(conn)
        sync_fts(conn)  # second run: marker says nothing new — no double-post
        check_fts(conn)
        assert len(search(conn, "gauntlet")) == 1


def test_marker_is_forward_only_and_undeletable(world: tuple[Path, int, int]) -> None:
    path, _, _ = world
    with connect(path) as conn:
        sync_fts(conn)
        with pytest.raises(sqlite3.IntegrityError, match="forward"):
            conn.execute("UPDATE fts_sync SET last_rowid = 0 WHERE mirror = 'wal_fts'")
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            conn.execute("DELETE FROM fts_sync WHERE mirror = 'wal_fts'")


def test_rolled_back_sync_stays_consistent(world: tuple[Path, int, int]) -> None:
    """Index rows and marker move in one transaction: a rollback reverts
    both together, and the next sync repeats the work cleanly."""
    path, _, _ = world
    conn = connect(path)
    sync_fts(conn)
    conn.rollback()  # the whole sync reverts: index AND marker together
    sync_fts(conn)
    conn.commit()
    check_fts(conn)
    assert len(search(conn, "gauntlet")) == 1
    conn.close()
