"""Durable review queue for memory candidates."""

import sqlite3
from pathlib import Path

import pytest

from brain.memory_candidates import (
    MemoryCandidateError,
    accept_candidate_agent,
    approve_candidate_user_global,
    enqueue_candidate,
    reject_candidate,
)
from brain.substrate import connect, create_brain


@pytest.fixture()
def candidate_world(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    path = tmp_path / "candidates.db"
    create_brain(path)
    ids: dict[str, int] = {}
    with connect(path) as conn:
        user_id = int(
            conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)").lastrowid
            or 0
        )
        agent_id = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'triage')",
                (user_id,),
            ).lastrowid
            or 0
        )
        session_id = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,)).lastrowid
            or 0
        )
        owner_wal = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) "
                "VALUES (?, 1, 'owner', 'source claim')",
                (session_id,),
            ).lastrowid
            or 0
        )
        approval_wal = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) "
                "VALUES (?, 2, 'owner', 'approve user global candidate')",
                (session_id,),
            ).lastrowid
            or 0
        )
        ids.update(
            {
                "agent": agent_id,
                "session": session_id,
                "source_wal": owner_wal,
                "approval_wal": approval_wal,
            }
        )
        conn.commit()
    return path, ids


def test_candidate_queue_persists_review_request(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="The owner evaluates AI assistants for implementation fidelity.",
            route="user_review",
            proposed_scope="user_global",
            category="project_goal",
            source_wal_ids=[ids["source_wal"]],
            triage={"reason": "cross_agent_high_value"},
        )
        conn.commit()

    with connect(path) as conn:
        row = conn.execute(
            "SELECT claim, route, proposed_scope, status, materialized_memory_id "
            "FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        assert row == (
            "The owner evaluates AI assistants for implementation fidelity.",
            "user_review",
            "user_global",
            "pending",
            None,
        )


def test_accept_candidate_agent_materializes_with_sources(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="The owner curates playlists for functional use.",
            route="agent",
            proposed_scope="agent",
            category="music_analysis_pattern",
            source_wal_ids=[ids["source_wal"]],
        )
        memory_id = accept_candidate_agent(conn, candidate_id, "agent-local useful context")
        conn.commit()

    with connect(path) as conn:
        assert conn.execute(
            "SELECT scope, content FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone() == ("agent", "The owner curates playlists for functional use.")
        assert conn.execute(
            "SELECT status, materialized_memory_id FROM memory_candidates "
            "WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("accepted", memory_id)
        assert conn.execute(
            "SELECT wal_id FROM memory_sources WHERE memory_id = ?",
            (memory_id,),
        ).fetchall() == [(ids["source_wal"],)]


def test_user_global_candidate_requires_blessed_owner_approval(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="The owner prefers demonstrated behavior over verbal declarations.",
            route="user_review",
            proposed_scope="user_global",
            category="interpretive_framework",
            source_wal_ids=[ids["source_wal"]],
        )
        with pytest.raises(sqlite3.IntegrityError, match="Owner approval"):
            approve_candidate_user_global(
                conn,
                candidate_id,
                ids["approval_wal"],
                "Owner approved global review candidate",
            )
        conn.rollback()

    with connect(path, blessed=True) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="The owner prefers demonstrated behavior over verbal declarations.",
            route="user_review",
            proposed_scope="user_global",
            category="interpretive_framework",
            source_wal_ids=[ids["source_wal"]],
        )
        memory_id = approve_candidate_user_global(
            conn,
            candidate_id,
            ids["approval_wal"],
            "Owner approved global review candidate",
        )
        conn.commit()

    with connect(path) as conn:
        assert conn.execute(
            "SELECT scope, global_approved_by_wal_id FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone() == ("user_global", ids["approval_wal"])
        assert conn.execute(
            "SELECT status, materialized_memory_id, resolved_by_wal_id "
            "FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("accepted", memory_id, ids["approval_wal"])


def test_reject_candidate_resolves_without_memory(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="The owner mentions a song, suggesting early exposure to hierarchy.",
            route="reject",
            proposed_scope="agent",
            source_wal_ids=[ids["source_wal"]],
        )
        reject_candidate(conn, candidate_id, "speculative inference")
        conn.commit()

    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute(
            "SELECT status, resolution_reason FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("rejected", "speculative inference")


def test_resolved_candidate_cannot_be_reopened(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim="A candidate to reject.",
            route="reject",
            proposed_scope="agent",
            source_wal_ids=[ids["source_wal"]],
        )
        reject_candidate(conn, candidate_id, "not useful")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE memory_candidates SET status = 'pending' WHERE candidate_id = ?",
                (candidate_id,),
            )


def test_candidate_sources_cannot_cross_agents(tmp_path: Path) -> None:
    path = tmp_path / "cross.db"
    create_brain(path)
    with connect(path) as conn:
        user_id = int(
            conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)").lastrowid
            or 0
        )
        agent_a = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'a')",
                (user_id,),
            ).lastrowid
            or 0
        )
        agent_b = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'b')",
                (user_id,),
            ).lastrowid
            or 0
        )
        session_a = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_a,)).lastrowid
            or 0
        )
        session_b = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_b,)).lastrowid
            or 0
        )
        wal_b = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'b')",
                (session_b,),
            ).lastrowid
            or 0
        )
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', 'a')",
            (session_a,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="never crosses agents"):
            enqueue_candidate(
                conn,
                agent_id=agent_a,
                claim="Cross-agent source should fail.",
                route="agent",
                proposed_scope="agent",
                source_wal_ids=[wal_b],
            )


def test_invalid_candidate_inputs_fail_loud(
    candidate_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = candidate_world
    with connect(path) as conn:
        with pytest.raises(MemoryCandidateError, match="claim"):
            enqueue_candidate(
                conn,
                agent_id=ids["agent"],
                claim=" ",
                route="agent",
                proposed_scope="agent",
                source_wal_ids=[ids["source_wal"]],
            )
        with pytest.raises(MemoryCandidateError, match="user_review"):
            enqueue_candidate(
                conn,
                agent_id=ids["agent"],
                claim="Bad scope.",
                route="user_review",
                proposed_scope="agent",
                source_wal_ids=[ids["source_wal"]],
            )

