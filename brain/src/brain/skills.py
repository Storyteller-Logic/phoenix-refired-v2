"""Skill formation from replayable WAL evidence (brain.spec §5.2)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass


class SkillError(ValueError):
    """A skill operation was not grounded in valid same-agent evidence."""


@dataclass(frozen=True)
class SkillFormationResult:
    skill_id: int
    source_count: int
    created: bool


def form_skill_from_evidence(
    conn: sqlite3.Connection,
    *,
    agent_id: int,
    name: str,
    content: str,
    source_wal_ids: Iterable[int],
    evidence_phrase: str | None = None,
    minimum_sources: int = 3,
) -> SkillFormationResult:
    """Create or reinforce one active skill from repeated successful WAL evidence.

    This is deliberately stricter than a manual/configured skill insert: the
    autonomous learning path must cite enough same-agent WAL rows to be replayed.
    """

    ids = _unique_source_ids(source_wal_ids)
    _require_text(name, "skill name")
    _require_text(content, "skill content")
    if len(ids) < minimum_sources:
        raise SkillError("skill formation requires repeated WAL evidence")
    _require_same_agent_wal(conn, agent_id, ids)

    with conn:
        row = conn.execute(
            "SELECT skill_id, content, status FROM skills WHERE agent_id = ? AND name = ?",
            (agent_id, name),
        ).fetchone()
        created = row is None
        if row is None:
            cur = conn.execute(
                "INSERT INTO skills (agent_id, name, content) VALUES (?, ?, ?)",
                (agent_id, name, content),
            )
            if cur.lastrowid is None:
                raise SkillError("skill insert did not return an id")
            skill_id = int(cur.lastrowid)
        else:
            skill_id = int(row[0])
            if str(row[1]) != content:
                raise SkillError("existing skill content differs; supersede instead")
            if str(row[2]) != "active":
                raise SkillError("retired skill cannot be reinforced")

        _insert_skill_sources(
            conn,
            skill_id=skill_id,
            wal_ids=ids,
            outcome="success",
            evidence_phrase=evidence_phrase,
        )
        source_count = _sync_skill_counters(conn, skill_id)

    return SkillFormationResult(
        skill_id=skill_id, source_count=source_count, created=created
    )


def record_skill_use_outcome(
    conn: sqlite3.Connection,
    *,
    agent_id: int,
    skill_id: int,
    source_wal_id: int,
    succeeded: bool,
    evidence_phrase: str | None = None,
    failure_retire_threshold: int = 2,
) -> int:
    """Append one replayable skill-use outcome and demote after repeated failure."""

    if failure_retire_threshold < 1:
        raise SkillError("failure retire threshold must be positive")
    _require_same_agent_skill(conn, agent_id, skill_id)
    _require_same_agent_wal(conn, agent_id, [source_wal_id])
    outcome = "success" if succeeded else "failure"

    with conn:
        _insert_skill_sources(
            conn,
            skill_id=skill_id,
            wal_ids=[source_wal_id],
            outcome=outcome,
            evidence_phrase=evidence_phrase,
        )
        source_count = _sync_skill_counters(conn, skill_id)
        failures = conn.execute(
            "SELECT COUNT(*) FROM skill_sources "
            "WHERE skill_id = ? AND outcome = 'failure'",
            (skill_id,),
        ).fetchone()[0]
        if int(failures) >= failure_retire_threshold:
            conn.execute(
                "UPDATE skills SET status = 'retired', retired_reason = ? "
                "WHERE skill_id = ? AND status = 'active'",
                ("retired after repeated failed WAL outcomes", skill_id),
            )

    return source_count


def _unique_source_ids(source_wal_ids: Iterable[int]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for raw in source_wal_ids:
        wal_id = int(raw)
        if wal_id not in seen:
            ids.append(wal_id)
            seen.add(wal_id)
    return ids


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise SkillError(f"{label} is required")


def _require_same_agent_skill(
    conn: sqlite3.Connection, agent_id: int, skill_id: int
) -> None:
    row = conn.execute(
        "SELECT status FROM skills WHERE skill_id = ? AND agent_id = ?",
        (skill_id, agent_id),
    ).fetchone()
    if row is None:
        raise SkillError("skill does not belong to the active agent")
    if str(row[0]) != "active":
        raise SkillError("retired skill cannot record new outcomes")


def _require_same_agent_wal(
    conn: sqlite3.Connection, agent_id: int, wal_ids: list[int]
) -> None:
    if not wal_ids:
        raise SkillError("source WAL evidence is required")
    placeholders = ", ".join("?" for _ in wal_ids)
    rows = conn.execute(
        "SELECT w.wal_id FROM wal w "
        "JOIN sessions s ON s.session_id = w.session_id "
        f"WHERE s.agent_id = ? AND w.wal_id IN ({placeholders})",
        (agent_id, *wal_ids),
    ).fetchall()
    found = {int(row[0]) for row in rows}
    missing_or_cross_scope = [wal_id for wal_id in wal_ids if wal_id not in found]
    if missing_or_cross_scope:
        raise SkillError("skill evidence must cite existing WAL from the same agent")


def _insert_skill_sources(
    conn: sqlite3.Connection,
    *,
    skill_id: int,
    wal_ids: list[int],
    outcome: str,
    evidence_phrase: str | None,
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO skill_sources "
        "(skill_id, wal_id, outcome, evidence_phrase) VALUES (?, ?, ?, ?)",
        [(skill_id, wal_id, outcome, evidence_phrase) for wal_id in wal_ids],
    )


def _sync_skill_counters(conn: sqlite3.Connection, skill_id: int) -> int:
    source_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM skill_sources WHERE skill_id = ?", (skill_id,)
        ).fetchone()[0]
    )
    success_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM skill_sources "
            "WHERE skill_id = ? AND outcome = 'success'",
            (skill_id,),
        ).fetchone()[0]
    )
    conn.execute(
        "UPDATE skills SET use_count = MAX(use_count, ?), "
        "success_count = MAX(success_count, ?) WHERE skill_id = ?",
        (source_count, success_count, skill_id),
    )
    return source_count
