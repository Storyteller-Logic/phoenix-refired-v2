"""Dreams (brain.spec §5.3 — R14).

The agent never stops and the conversation never blocks: scheduling is the
harness's job. The Brain owns the operations — and they are built so that
a dream can die mid-flight and leave nothing behind (A8): each pass moves
its data and its forward-only markers in one transaction.

Pass 1 distills recent WAL into provisional memories through a pluggable
Distiller (the model-backed distiller is a Glove stone; any distiller
works here). The full dream is the housekeeping pass, and its doctrine is
the Brain's own: **the ledger is canonical, worth is derived data** —
worth is recomputed from the event trail (repairing drift from events
written without arithmetic, e.g. through my_ledger), then the accumulated
net signal is applied: net +2 promotes what is provisional, net −2
retires — the promote rule is the exact mirror of the verbs' retire rule,
not a new constant. Cadence is computable from durable state alone: WAL
counts against markers, thresholds from agent_settings (§5.3 proposed
defaults: pass 1 every 5 turns, full dream every 15), no volatile
counters anywhere.
"""

import sqlite3
from collections.abc import Sequence
from typing import Protocol

from brain.learning import CONTRADICT_STEP, REINFORCE_STEP

BIRTH_WORTH = 0.1
DEFAULT_PASS1_EVERY = 5
DEFAULT_FULL_EVERY = 15
NET_PROMOTE = 2
NET_RETIRE = 2


class Distiller(Protocol):
    """A stone for the distillation slot: transcript lines in, memory
    contents out."""

    def distill(self, transcript: Sequence[tuple[str, str]]) -> list[str]: ...


def _setting(conn: sqlite3.Connection, agent_id: int, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT value FROM agent_settings WHERE agent_id = ? AND key = ?",
        (agent_id, key),
    ).fetchone()
    try:
        return int(row[0]) if row is not None else default
    except ValueError:
        return default


def pass1_due(conn: sqlite3.Connection, agent_id: int) -> bool:
    """True when any of the agent's sessions has accumulated enough
    un-dreamed WAL rows (agent_settings['dream.pass1_every'], default 5)."""
    threshold = _setting(conn, agent_id, "dream.pass1_every", DEFAULT_PASS1_EVERY)
    row = conn.execute(
        "SELECT 1 FROM sessions s "
        "WHERE s.agent_id = ? "
        "AND (SELECT COUNT(w.wal_id) FROM wal w WHERE w.session_id = s.session_id "
        "     AND w.wal_id > COALESCE((SELECT last_wal_id FROM dream_marks dm "
        "                              WHERE dm.session_id = s.session_id), 0)"
        ") >= ? LIMIT 1",
        (agent_id, threshold),
    ).fetchone()
    return row is not None


def full_dream_due(conn: sqlite3.Connection, agent_id: int) -> bool:
    """True when the agent has accumulated enough WAL rows since its last
    full dream (agent_settings['dream.full_every'], default 15)."""
    threshold = _setting(conn, agent_id, "dream.full_every", DEFAULT_FULL_EVERY)
    count = conn.execute(
        "SELECT COUNT(w.wal_id) FROM wal w "
        "JOIN sessions s ON s.session_id = w.session_id "
        "WHERE s.agent_id = ? AND w.wal_id > "
        "COALESCE((SELECT last_full_wal_id FROM agent_dream_state "
        "          WHERE agent_id = ?), 0)",
        (agent_id, agent_id),
    ).fetchone()[0]
    return bool(count >= threshold)


def dream_pass1(conn: sqlite3.Connection, distiller: Distiller, agent_id: int) -> int:
    """Distill each session's WAL rows past its marker into provisional
    memories; advance the markers. One transaction: a crash reverts
    memories and markers together, so the pass is idempotent. Returns the
    number of memories created."""
    created = 0
    status = conn.execute(
        "SELECT status FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if status is None or status[0] == "retired":
        return 0  # a retired agent is done; its leftover WAL stays ground truth
    sessions = conn.execute(
        "SELECT session_id FROM sessions WHERE agent_id = ? ORDER BY session_id",
        (agent_id,),
    ).fetchall()
    for (session_id,) in sessions:
        rows = conn.execute(
            "SELECT wal_id, role, content FROM wal "
            "WHERE session_id = ? AND wal_id > "
            "COALESCE((SELECT last_wal_id FROM dream_marks "
            "          WHERE session_id = ?), 0) "
            "ORDER BY wal_id",
            (session_id, session_id),
        ).fetchall()
        if not rows:
            continue
        transcript = [(str(role), str(content)) for _, role, content in rows]
        for content in distiller.distill(transcript):
            cur = conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)",
                (agent_id, content),
            )
            memory_id = cur.lastrowid
            assert memory_id is not None
            conn.executemany(
                "INSERT INTO memory_sources (memory_id, wal_id) VALUES (?, ?)",
                [(int(memory_id), int(wal_id)) for wal_id, _, _ in rows],
            )
            created += 1
        conn.execute(
            "INSERT INTO dream_marks (session_id, last_wal_id) VALUES (?, ?) "
            "ON CONFLICT (session_id) DO UPDATE SET last_wal_id = excluded.last_wal_id",
            (session_id, rows[-1][0]),
        )
    return created


def full_dream(conn: sqlite3.Connection, agent_id: int | None = None) -> dict[str, int]:
    """The housekeeping pass: reconcile worth from the canonical ledger,
    promote net +2 provisionals, retire net −2. One transaction; running
    it twice changes nothing the second time. Returns its counts."""
    report = {"reconciled": 0, "promoted": 0, "retired": 0}
    sql = (
        "SELECT m.memory_id, m.status, m.worth, "
        "COALESCE(SUM(CASE WHEN ll.event = 'reinforce' THEN 1 END), 0), "
        "COALESCE(SUM(CASE WHEN ll.event = 'contradict' THEN 1 END), 0) "
        "FROM memories m "
        "LEFT JOIN learning_ledger ll ON ll.memory_id = m.memory_id "
        "WHERE m.status <> 'retired'"
    )
    params: tuple[object, ...] = ()
    if agent_id is not None:
        sql += " AND m.agent_id = ?"
        params = (agent_id,)
    sql += " GROUP BY m.memory_id"
    for memory_id, status, worth, reinforces, contradicts in conn.execute(
        sql, params
    ).fetchall():
        canonical = max(
            0.0, BIRTH_WORTH + REINFORCE_STEP * reinforces - CONTRADICT_STEP * contradicts
        )
        if abs(float(worth) - canonical) > 1e-9:
            conn.execute(
                "UPDATE memories SET worth = ? WHERE memory_id = ?",
                (canonical, memory_id),
            )
            report["reconciled"] += 1
        net = int(reinforces) - int(contradicts)
        if net <= -NET_RETIRE:
            reason = f"full dream: repeated contradiction (net {net})"
            conn.execute(
                "UPDATE memories SET status = 'retired', retired_reason = ? "
                "WHERE memory_id = ?",
                (reason, memory_id),
            )
            conn.execute(
                "INSERT INTO learning_ledger (memory_id, event, cause) "
                "VALUES (?, 'retire', ?)",
                (memory_id, reason),
            )
            report["retired"] += 1
        elif net >= NET_PROMOTE and status == "provisional":
            cause = f"full dream: earned durability (net +{net})"
            conn.execute(
                "UPDATE memories SET status = 'durable' WHERE memory_id = ?",
                (memory_id,),
            )
            conn.execute(
                "INSERT INTO learning_ledger (memory_id, event, cause) "
                "VALUES (?, 'promote', ?)",
                (memory_id, cause),
            )
            report["promoted"] += 1
    # advance the cadence marker for every agent the pass covered
    # (Wyrm pass 1, finding F3: the global pass must not leave "due" lying)
    if agent_id is not None:
        covered = [agent_id]
    else:
        covered = [
            int(row[0])
            for row in conn.execute("SELECT agent_id FROM agents WHERE status = 'active'")
        ]
    for covered_id in covered:
        row = conn.execute(
            "SELECT COALESCE(MAX(w.wal_id), 0) FROM wal w "
            "JOIN sessions s ON s.session_id = w.session_id WHERE s.agent_id = ?",
            (covered_id,),
        ).fetchone()
        max_wal = int(row[0])
        conn.execute(
            "INSERT INTO agent_dream_state (agent_id, last_full_wal_id) VALUES (?, ?) "
            "ON CONFLICT (agent_id) DO UPDATE SET last_full_wal_id = "
            "MAX(last_full_wal_id, excluded.last_full_wal_id)",
            (covered_id, max_wal),
        )
    return report
