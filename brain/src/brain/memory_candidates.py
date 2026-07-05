"""Durable memory-candidate review queue.

Inference may propose candidate memories, but candidates do not become
knowledge until accepted. User-global candidates require Owner approval through
the same substrate gate as any other user-global memory.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence


class MemoryCandidateError(RuntimeError):
    """A candidate transition was invalid."""


def enqueue_candidate(
    conn: sqlite3.Connection,
    *,
    agent_id: int,
    claim: str,
    route: str,
    proposed_scope: str,
    source_wal_ids: Sequence[int],
    category: str | None = None,
    triage: Mapping[str, object] | None = None,
) -> int:
    """Persist a candidate memory proposal and its WAL provenance."""
    content = " ".join(claim.split())
    if not content:
        raise MemoryCandidateError("enqueue_candidate: claim must not be empty")
    sources = tuple(dict.fromkeys(int(wal_id) for wal_id in source_wal_ids))
    if not sources:
        raise MemoryCandidateError("enqueue_candidate: at least one source WAL row is required")
    if route not in {"agent", "user_review", "reject"}:
        raise MemoryCandidateError(f"enqueue_candidate: invalid route {route!r}")
    if proposed_scope not in {"agent", "user_global"}:
        raise MemoryCandidateError(
            f"enqueue_candidate: invalid proposed_scope {proposed_scope!r}"
        )
    if route == "user_review" and proposed_scope != "user_global":
        raise MemoryCandidateError("enqueue_candidate: user_review requires user_global scope")

    cur = conn.execute(
        "INSERT INTO memory_candidates "
        "(agent_id, claim, route, proposed_scope, category, triage_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            agent_id,
            content,
            route,
            proposed_scope,
            category,
            json.dumps(dict(triage or {}), sort_keys=True),
        ),
    )
    candidate_id = cur.lastrowid
    assert candidate_id is not None
    conn.executemany(
        "INSERT INTO memory_candidate_sources (candidate_id, wal_id) VALUES (?, ?)",
        [(int(candidate_id), wal_id) for wal_id in sources],
    )
    return int(candidate_id)


def accept_candidate_agent(
    conn: sqlite3.Connection,
    candidate_id: int,
    cause: str,
    *,
    resolved_by_wal_id: int | None = None,
) -> int:
    """Materialize a pending candidate as an agent-local provisional memory."""
    if not cause.strip():
        raise MemoryCandidateError("accept_candidate_agent: cause must not be empty")
    candidate = _pending_candidate(conn, candidate_id, "accept_candidate_agent")
    if str(candidate["route"]) == "reject":
        raise MemoryCandidateError("accept_candidate_agent: rejected-route candidate cannot accept")
    return _materialize(conn, candidate, "agent", resolved_by_wal_id, cause.strip())


def approve_candidate_user_global(
    conn: sqlite3.Connection,
    candidate_id: int,
    approval_wal_id: int,
    cause: str,
) -> int:
    """Materialize a pending user-review candidate as user-global memory.

    The substrate requires a blessed connection and a same-user owner WAL row.
    """
    if not cause.strip():
        raise MemoryCandidateError("approve_candidate_user_global: cause must not be empty")
    candidate = _pending_candidate(conn, candidate_id, "approve_candidate_user_global")
    if candidate["route"] != "user_review" or candidate["proposed_scope"] != "user_global":
        raise MemoryCandidateError(
            "approve_candidate_user_global: candidate was not routed for user review"
        )
    return _materialize(conn, candidate, "user_global", approval_wal_id, cause.strip())


def reject_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    reason: str,
    *,
    resolved_by_wal_id: int | None = None,
) -> None:
    """Resolve a pending candidate as rejected, retaining the evidence trail."""
    if not reason.strip():
        raise MemoryCandidateError("reject_candidate: reason must not be empty")
    _pending_candidate(conn, candidate_id, "reject_candidate")
    conn.execute(
        "UPDATE memory_candidates SET status = 'rejected', resolved_by_wal_id = ?, "
        "resolution_reason = ?, resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE candidate_id = ?",
        (resolved_by_wal_id, reason.strip(), candidate_id),
    )


def _pending_candidate(
    conn: sqlite3.Connection, candidate_id: int, verb: str
) -> dict[str, object]:
    row = conn.execute(
        "SELECT candidate_id, agent_id, claim, route, proposed_scope, status "
        "FROM memory_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise MemoryCandidateError(f"{verb}: no candidate with id {candidate_id}")
    if str(row[5]) != "pending":
        raise MemoryCandidateError(f"{verb}: candidate {candidate_id} is already resolved")
    return {
        "candidate_id": int(row[0]),
        "agent_id": int(row[1]),
        "claim": str(row[2]),
        "route": str(row[3]),
        "proposed_scope": str(row[4]),
    }


def _candidate_sources(conn: sqlite3.Connection, candidate_id: int) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute(
            "SELECT wal_id FROM memory_candidate_sources WHERE candidate_id = ? "
            "ORDER BY wal_id",
            (candidate_id,),
        ).fetchall()
    ]


def _materialize(
    conn: sqlite3.Connection,
    candidate: dict[str, object],
    scope: str,
    approval_wal_id: int | None,
    cause: str,
) -> int:
    candidate_id = int(str(candidate["candidate_id"]))
    sources = _candidate_sources(conn, candidate_id)
    if not sources:
        raise MemoryCandidateError("_materialize: candidate has no source WAL rows")
    conn.execute("SAVEPOINT brain_memory_candidate_materialize")
    try:
        columns = ["agent_id", "content", "scope"]
        agent_id = int(str(candidate["agent_id"]))
        values: list[object] = [agent_id, str(candidate["claim"]), scope]
        if scope == "user_global":
            columns.append("global_approved_by_wal_id")
            values.append(approval_wal_id)
        cur = conn.execute(
            f"INSERT INTO memories ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        memory_id = cur.lastrowid
        assert memory_id is not None
        conn.executemany(
            "INSERT INTO memory_sources (memory_id, wal_id, warnings_json) "
            "VALUES (?, ?, ?)",
            [
                (
                    int(memory_id),
                    wal_id,
                    json.dumps(["memory_candidate", f"candidate:{candidate_id}"]),
                )
                for wal_id in sources
            ],
        )
        conn.execute(
            "UPDATE memory_candidates SET status = 'accepted', materialized_memory_id = ?, "
            "resolved_by_wal_id = ?, resolution_reason = ?, "
            "resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE candidate_id = ?",
            (int(memory_id), approval_wal_id, cause, candidate_id),
        )
        conn.execute("RELEASE SAVEPOINT brain_memory_candidate_materialize")
        return int(memory_id)
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT brain_memory_candidate_materialize")
        conn.execute("RELEASE SAVEPOINT brain_memory_candidate_materialize")
        raise
