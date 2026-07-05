"""Hybrid recall proofs: WAL fallback, provenance diversity, and adjacency."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.learning import supersede
from brain.recall import hybrid_recall, set_embedder
from brain.substrate import connect, connect_agent, create_brain


class ControlledEmbedder:
    embedder_id = "controlled"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if "target" in text.lower() else [0.0, 1.0]
            for text in texts
        ]


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, int, int, int]:
    path = tmp_path / "hybrid.db"
    create_brain(path)
    with connect(path) as conn:
        user_id = conn.execute(
            "INSERT INTO users (name, is_owner) VALUES ('owner', 1)"
        ).lastrowid
        agent_id = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'agent')", (user_id,)
        ).lastrowid
        session_id = conn.execute(
            "INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,)
        ).lastrowid
        assert agent_id is not None and session_id is not None
        wal_ids = []
        for turn, role, content in (
            (1, "owner", "previous owner context"),
            (2, "agent", "an agent reply"),
            (3, "owner", "target source statement"),
            (5, "owner", "next owner context"),
        ):
            wal_id = conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, ?, ?)",
                (session_id, turn, role, content),
            ).lastrowid
            wal_ids.append(int(wal_id or 0))
        memory_id = conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, 'target memory claim')",
            (agent_id,),
        ).lastrowid
        assert memory_id is not None
        conn.execute(
            "INSERT INTO memory_sources (memory_id, wal_id, warnings_json) "
            "VALUES (?, ?, '[\"provisional\"]')",
            (memory_id, wal_ids[2]),
        )
        conn.commit()
    return path, int(agent_id), int(session_id), int(memory_id)


def test_hybrid_recall_embeds_wal_diversifies_and_expands(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, _, memory_id = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        set_embedder(conn, stone)
        conn.commit()
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wal_embeddings").fetchone()[0] == 4
        result = hybrid_recall(
            conn, stone, "target question", agent_id=agent_id, floor=0.5, limit=3
        )
        assert result.hits[0].kind == "memory"
        assert result.hits[0].row_id == memory_id
        assert result.hits[0].warnings == ("provisional",)
        assert not any(
            hit.kind == "wal" and hit.content == "target source statement"
            for hit in result.hits
        )
        context = {(item.turn, item.relation, item.content) for item in result.context}
        assert (3, "source", "target source statement") in context
        assert (1, "previous", "previous owner context") in context
        assert (5, "next", "next owner context") in context
        assert all(item.role == "owner" for item in result.context)
        recalled = conn.execute(
            "SELECT COUNT(*) FROM learning_ledger "
            "WHERE memory_id = ? AND event = 'recall'",
            (memory_id,),
        ).fetchone()[0]
        assert recalled == 1


def test_memories_precede_raw_wal_fallback(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, _, memory_id = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        set_embedder(conn, stone)
        result = hybrid_recall(
            conn, stone, "target question", agent_id=agent_id, floor=0.0, limit=2
        )
        assert result.hits[0].kind == "memory"
        assert result.hits[0].row_id == memory_id
        assert result.hits[1].kind == "wal"


def test_late_wal_is_embedded_on_next_hybrid_recall(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, session_id, _ = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        set_embedder(conn, stone)
        conn.commit()
    with connect(path) as conn:
        wal_id = conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) "
            "VALUES (?, 7, 'owner', 'late target detail')",
            (session_id,),
        ).lastrowid
        hybrid_recall(conn, stone, "target", agent_id=agent_id, floor=0.5)
        assert conn.execute(
            "SELECT 1 FROM wal_embeddings WHERE wal_id = ?", (wal_id,)
        ).fetchone() is not None


def test_scoped_agent_cannot_read_hybrid_index(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, _, _ = world
    scoped = connect_agent(path, agent_id)
    try:
        for table in ("wal_embeddings", "memory_sources"):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
                scoped.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        scoped.close()


def test_wal_cutoff_excludes_current_prompt_and_context(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, session_id, _ = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        set_embedder(conn, stone)
        current_id = conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) "
            "VALUES (?, 7, 'owner', 'target current question')",
            (session_id,),
        ).lastrowid
        assert current_id is not None
        result = hybrid_recall(
            conn,
            stone,
            "target current question",
            agent_id=agent_id,
            floor=0.5,
            wal_before_id=int(current_id),
        )
        assert all(hit.row_id != current_id for hit in result.hits if hit.kind == "wal")
        assert all(item.wal_id != current_id for item in result.context)


def test_zero_limits_return_no_hits_or_context(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, _, _ = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        set_embedder(conn, stone)
        result = hybrid_recall(
            conn, stone, "target", agent_id=agent_id, floor=0.5, limit=0
        )
        assert result.hits == ()
        assert result.context == ()

        result = hybrid_recall(
            conn,
            stone,
            "target",
            agent_id=agent_id,
            floor=0.5,
            limit=3,
            context_limit=0,
        )
        assert result.hits
        assert result.context == ()


def test_superseded_memory_is_excluded_and_successor_keeps_provenance(
    world: tuple[Path, int, int, int],
) -> None:
    path, agent_id, _, old_memory_id = world
    stone = ControlledEmbedder()
    with connect(path, blessed=True) as conn:
        source_wal_id = conn.execute(
            "SELECT wal_id FROM memory_sources WHERE memory_id = ?",
            (old_memory_id,),
        ).fetchone()[0]
        new_memory_id = supersede(
            conn,
            old_memory_id,
            "target corrected memory claim",
            "Owner corrected the claim",
            source_wal_ids=[source_wal_id],
        )
        set_embedder(conn, stone)
        result = hybrid_recall(
            conn, stone, "target question", agent_id=agent_id, floor=0.5, limit=10
        )
        memory_hits = [hit for hit in result.hits if hit.kind == "memory"]
        assert [hit.row_id for hit in memory_hits] == [new_memory_id]
        assert memory_hits[0].source_wal_ids == (source_wal_id,)
        assert conn.execute(
            "SELECT status, superseded_by FROM memories WHERE memory_id = ?",
            (old_memory_id,),
        ).fetchone() == ("retired", new_memory_id)
