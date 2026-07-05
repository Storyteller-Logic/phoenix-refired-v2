"""The learning verbs (brain.spec §3 — the Learning Law, L3).

Merit moves worth; the ledger records why (§2.4). Every verb writes its
data change and its ledger event in the same implicit transaction — they
live or die together. Nothing here reads the clock: there is no time decay,
only signal. Failures are first-class: the verbs make no distinction.

The arithmetic, from the law's own words and nothing else:
- reinforce/contradict are symmetric steps, so a balanced signal is a wash,
  not a change (§3.7).
- "repeated contradiction retires it" (§3.3): repeated = contradictions
  exceeding reinforcements by two, net.
- promotion is earned, never declared (§3.4): a memory with zero reinforce
  events has no earned merit and cannot be promoted.
"""

import sqlite3
from collections.abc import Sequence

REINFORCE_STEP = 0.1
CONTRADICT_STEP = 0.1
RETIRE_NET_CONTRADICTIONS = 2


class LearningError(RuntimeError):
    """A learning verb was asked to do something the Law forbids."""


def _status_of(conn: sqlite3.Connection, memory_id: int, verb: str) -> str:
    row = conn.execute(
        "SELECT status FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        raise LearningError(f"{verb}: no memory with id {memory_id}")
    status = str(row[0])
    if status == "retired":
        raise LearningError(f"{verb}: memory {memory_id} is retired and frozen")
    return status


def _event(conn: sqlite3.Connection, memory_id: int, event: str, cause: str) -> None:
    conn.execute(
        "INSERT INTO learning_ledger (memory_id, event, cause) VALUES (?, ?, ?)",
        (memory_id, event, cause),
    )


def reinforce(conn: sqlite3.Connection, memory_id: int, cause: str) -> float:
    """Proven use: worth rises (§3.2). Returns the new worth."""
    _status_of(conn, memory_id, "reinforce")
    conn.execute(
        "UPDATE memories SET worth = worth + ? WHERE memory_id = ?",
        (REINFORCE_STEP, memory_id),
    )
    _event(conn, memory_id, "reinforce", cause)
    row = conn.execute(
        "SELECT worth FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return float(row[0])


def contradict(conn: sqlite3.Connection, memory_id: int, cause: str) -> float | None:
    """Proven wrong: worth falls; repeated contradiction retires (§3.3).
    Returns the new worth, or None if this contradiction retired the memory."""
    _status_of(conn, memory_id, "contradict")
    conn.execute(
        "UPDATE memories SET worth = MAX(0.0, worth - ?) WHERE memory_id = ?",
        (CONTRADICT_STEP, memory_id),
    )
    _event(conn, memory_id, "contradict", cause)
    counts = conn.execute(
        "SELECT "
        "SUM(CASE WHEN event = 'contradict' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN event = 'reinforce' THEN 1 ELSE 0 END) "
        "FROM learning_ledger WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    net = int(counts[0] or 0) - int(counts[1] or 0)
    if net >= RETIRE_NET_CONTRADICTIONS:
        reason = f"repeated contradiction (net {net}): {cause}"
        conn.execute(
            "UPDATE memories SET status = 'retired', retired_reason = ? "
            "WHERE memory_id = ?",
            (reason, memory_id),
        )
        _event(conn, memory_id, "retire", reason)
        return None
    row = conn.execute(
        "SELECT worth FROM memories WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    return float(row[0])


def promote(conn: sqlite3.Connection, memory_id: int, cause: str) -> None:
    """Provisional -> durable, on earned merit only (§3.4)."""
    status = _status_of(conn, memory_id, "promote")
    if status != "provisional":
        raise LearningError(
            f"promote: memory {memory_id} is {status}, only provisional can be promoted"
        )
    reinforced = conn.execute(
        "SELECT COUNT(event_id) FROM learning_ledger "
        "WHERE memory_id = ? AND event = 'reinforce'",
        (memory_id,),
    ).fetchone()[0]
    if not reinforced:
        raise LearningError(
            f"promote: memory {memory_id} has no reinforce events — "
            "merit is earned, never declared (L3)"
        )
    conn.execute(
        "UPDATE memories SET status = 'durable' WHERE memory_id = ?", (memory_id,)
    )
    _event(conn, memory_id, "promote", cause)


def promote_user_global(
    conn: sqlite3.Connection,
    memory_id: int,
    approval_wal_id: int,
    cause: str,
) -> None:
    """Promote a memory from agent-local to user-global scope.

    The substrate requires an Owner-blessed connection and an owner WAL approval
    row from the same user. This verb is the explicit gate; inference models
    should propose candidates, never call this by themselves.
    """
    _status_of(conn, memory_id, "promote_user_global")
    if not cause.strip():
        raise LearningError("promote_user_global: cause must not be empty")
    conn.execute(
        "UPDATE memories SET scope = 'user_global', global_approved_by_wal_id = ? "
        "WHERE memory_id = ?",
        (approval_wal_id, memory_id),
    )
    _event(conn, memory_id, "promote", f"user-global approval: {cause.strip()}")


def retire(
    conn: sqlite3.Connection,
    memory_id: int,
    cause: str,
    *,
    superseded_by: int | None = None,
) -> None:
    """Deliberate retirement, with a reason, optionally linked to what
    superseded it (§3.5). Never a deletion."""
    _status_of(conn, memory_id, "retire")
    conn.execute(
        "UPDATE memories SET status = 'retired', retired_reason = ?, superseded_by = ? "
        "WHERE memory_id = ?",
        (cause, superseded_by, memory_id),
    )
    _event(conn, memory_id, "retire", cause)
    if superseded_by is not None:
        _event(conn, memory_id, "supersede", f"superseded by memory {superseded_by}")


def supersede(
    conn: sqlite3.Connection,
    memory_id: int,
    corrected_content: str,
    cause: str,
    *,
    source_wal_ids: Sequence[int],
) -> int:
    """Replace stale knowledge with a new evidence-linked provisional memory.

    The old memory is retired only after the successor and every provenance
    link exist. A savepoint makes the verb atomic even inside a caller's larger
    transaction. The successor must earn durability under the normal Law.
    """
    _status_of(conn, memory_id, "supersede")
    content = corrected_content.strip()
    if not content:
        raise LearningError("supersede: corrected content must not be empty")
    if not cause.strip():
        raise LearningError("supersede: cause must not be empty")
    sources = tuple(dict.fromkeys(int(wal_id) for wal_id in source_wal_ids))
    if not sources:
        raise LearningError("supersede: at least one source WAL row is required")
    row = conn.execute(
        "SELECT agent_id, is_failure, content FROM memories WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    assert row is not None
    agent_id, is_failure = int(row[0]), int(row[1])
    if content == str(row[2]).strip():
        raise LearningError("supersede: corrected content must differ from stale content")

    conn.execute("SAVEPOINT brain_supersede")
    try:
        cur = conn.execute(
            "INSERT INTO memories (agent_id, content, is_failure) VALUES (?, ?, ?)",
            (agent_id, content, is_failure),
        )
        successor_id = cur.lastrowid
        assert successor_id is not None
        conn.executemany(
            "INSERT INTO memory_sources (memory_id, wal_id) VALUES (?, ?)",
            [(int(successor_id), wal_id) for wal_id in sources],
        )
        retire(
            conn,
            memory_id,
            cause.strip(),
            superseded_by=int(successor_id),
        )
        conn.execute("RELEASE SAVEPOINT brain_supersede")
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT brain_supersede")
        conn.execute("RELEASE SAVEPOINT brain_supersede")
        raise
    return int(successor_id)
