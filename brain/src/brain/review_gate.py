"""Owner review gate for durable memory candidates.

This is the explicit boundary between model inference and user-global memory:
pending candidates can be approved, kept agent-local, rejected, deferred, or
rewritten. Rewrites preserve the original candidate as resolved provenance and
materialize a new reviewed candidate.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from brain.memory_candidates import (
    accept_candidate_agent,
    approve_candidate_user_global,
    enqueue_candidate,
    reject_candidate,
)

ReviewAction = Literal[
    "approve_global",
    "keep_agent",
    "reject",
    "rewrite_global",
    "rewrite_agent",
    "defer",
]


class ReviewGateError(RuntimeError):
    """An Owner review action was invalid."""


@dataclass(frozen=True)
class CandidateSummary:
    candidate_id: int
    agent_id: int
    agent_name: str
    claim: str
    route: str
    proposed_scope: str
    category: str | None
    status: str
    created_at: str


@dataclass(frozen=True)
class SourceTurn:
    wal_id: int
    session_id: int
    turn: int
    role: str
    content: str


@dataclass(frozen=True)
class CandidateDetail:
    summary: CandidateSummary
    triage: dict[str, object]
    sources: tuple[SourceTurn, ...]


@dataclass(frozen=True)
class ReviewResult:
    action: ReviewAction
    candidate_id: int
    approval_wal_id: int | None = None
    memory_id: int | None = None
    rewritten_candidate_id: int | None = None


@dataclass(frozen=True)
class ReviewDecision:
    candidate_id: int
    action: ReviewAction
    reason: str
    rewrite_claim: str | None = None
    session_id: int | None = None


def pending_candidates(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
) -> list[CandidateSummary]:
    """Return pending candidates oldest-first for Owner review."""
    if limit <= 0:
        raise ReviewGateError("pending_candidates: limit must be positive")
    return [
        _summary_from_row(row)
        for row in conn.execute(
            "SELECT c.candidate_id, c.agent_id, a.name, c.claim, c.route, "
            "c.proposed_scope, c.category, c.status, c.created_at "
            "FROM memory_candidates c JOIN agents a ON a.agent_id = c.agent_id "
            "WHERE c.status = 'pending' ORDER BY c.candidate_id LIMIT ?",
            (limit,),
        )
    ]


def candidate_detail(conn: sqlite3.Connection, candidate_id: int) -> CandidateDetail:
    """Load one candidate with its triage JSON and source WAL excerpts."""
    row = conn.execute(
        "SELECT c.candidate_id, c.agent_id, a.name, c.claim, c.route, "
        "c.proposed_scope, c.category, c.status, c.created_at, c.triage_json "
        "FROM memory_candidates c JOIN agents a ON a.agent_id = c.agent_id "
        "WHERE c.candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ReviewGateError(f"candidate_detail: no candidate with id {candidate_id}")
    sources = tuple(_candidate_sources(conn, candidate_id))
    return CandidateDetail(
        summary=_summary_from_row(row[:9]),
        triage=_decode_json_object(str(row[9])),
        sources=sources,
    )


def review_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    action: ReviewAction,
    reason: str,
    *,
    rewrite_claim: str | None = None,
    session_id: int | None = None,
) -> ReviewResult:
    """Apply one explicit Owner review decision.

    `approve_global` and `rewrite_global` require the caller's connection to be
    blessed; the substrate enforces this fail-closed when the memory is written.
    Every resolving action appends an Owner WAL row before materialization.
    """
    cleaned_reason = " ".join(reason.split())
    if action == "defer":
        return ReviewResult(action=action, candidate_id=candidate_id)
    if not cleaned_reason:
        raise ReviewGateError("review_candidate: reason must not be empty")

    detail = candidate_detail(conn, candidate_id)
    if detail.summary.status != "pending":
        raise ReviewGateError(f"review_candidate: candidate {candidate_id} is not pending")

    approval_wal_id = _append_owner_review_wal(
        conn,
        detail.summary.agent_id,
        candidate_id,
        action,
        cleaned_reason,
        rewrite_claim=rewrite_claim,
        session_id=session_id,
    )

    if action == "approve_global":
        memory_id = approve_candidate_user_global(
            conn,
            candidate_id,
            approval_wal_id,
            cleaned_reason,
        )
        return ReviewResult(action, candidate_id, approval_wal_id, memory_id)
    if action == "keep_agent":
        memory_id = accept_candidate_agent(
            conn,
            candidate_id,
            cleaned_reason,
            resolved_by_wal_id=approval_wal_id,
        )
        return ReviewResult(action, candidate_id, approval_wal_id, memory_id)
    if action == "reject":
        reject_candidate(
            conn,
            candidate_id,
            cleaned_reason,
            resolved_by_wal_id=approval_wal_id,
        )
        return ReviewResult(action, candidate_id, approval_wal_id)
    if action in {"rewrite_global", "rewrite_agent"}:
        rewritten_candidate_id, memory_id = _rewrite_and_materialize(
            conn,
            detail,
            action,
            cleaned_reason,
            approval_wal_id,
            rewrite_claim,
        )
        reject_candidate(
            conn,
            candidate_id,
            f"rewritten as candidate {rewritten_candidate_id}: {cleaned_reason}",
            resolved_by_wal_id=approval_wal_id,
        )
        return ReviewResult(
            action,
            candidate_id,
            approval_wal_id,
            memory_id,
            rewritten_candidate_id,
        )
    raise ReviewGateError(f"review_candidate: invalid action {action!r}")


def review_candidates(
    conn: sqlite3.Connection,
    decisions: Iterable[ReviewDecision],
) -> list[ReviewResult]:
    """Apply a batch of explicit Owner review decisions in order."""
    results: list[ReviewResult] = []
    seen: set[int] = set()
    for decision in decisions:
        if decision.candidate_id in seen:
            raise ReviewGateError(
                f"review_candidates: duplicate candidate {decision.candidate_id}"
            )
        seen.add(decision.candidate_id)
        results.append(
            review_candidate(
                conn,
                decision.candidate_id,
                decision.action,
                decision.reason,
                rewrite_claim=decision.rewrite_claim,
                session_id=decision.session_id,
            )
        )
    return results


def _summary_from_row(row: Sequence[object]) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=int(str(row[0])),
        agent_id=int(str(row[1])),
        agent_name=str(row[2]),
        claim=str(row[3]),
        route=str(row[4]),
        proposed_scope=str(row[5]),
        category=str(row[6]) if row[6] is not None else None,
        status=str(row[7]),
        created_at=str(row[8]),
    )


def _candidate_sources(conn: sqlite3.Connection, candidate_id: int) -> list[SourceTurn]:
    return [
        SourceTurn(
            wal_id=int(row[0]),
            session_id=int(row[1]),
            turn=int(row[2]),
            role=str(row[3]),
            content=str(row[4]),
        )
        for row in conn.execute(
            "SELECT w.wal_id, w.session_id, w.turn, w.role, w.content "
            "FROM memory_candidate_sources cs "
            "JOIN wal w ON w.wal_id = cs.wal_id "
            "WHERE cs.candidate_id = ? ORDER BY w.wal_id",
            (candidate_id,),
        )
    ]


def _decode_json_object(raw: str) -> dict[str, object]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewGateError("candidate_detail: invalid triage JSON") from exc
    if not isinstance(decoded, dict):
        raise ReviewGateError("candidate_detail: triage JSON must be an object")
    return dict(decoded)


def _append_owner_review_wal(
    conn: sqlite3.Connection,
    agent_id: int,
    candidate_id: int,
    action: ReviewAction,
    reason: str,
    *,
    rewrite_claim: str | None,
    session_id: int | None,
) -> int:
    chosen_session = _review_session(conn, agent_id, session_id)
    turn = int(
        conn.execute(
            "SELECT COALESCE(MAX(turn), 0) + 1 FROM wal WHERE session_id = ?",
            (chosen_session,),
        ).fetchone()[0]
    )
    content = (
        f"Owner review candidate {candidate_id}: {_review_action_label(action)}. "
        f"Reason: {reason}"
    )
    if rewrite_claim is not None:
        content = f"{content}. Rewrite: {' '.join(rewrite_claim.split())}"
    wal_id = conn.execute(
        "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, 'owner', ?)",
        (chosen_session, turn, content),
    ).lastrowid
    assert wal_id is not None
    return int(wal_id)


def _review_action_label(action: ReviewAction) -> str:
    labels: dict[str, str] = {
        "approve_global": "promote_to_this_user",
        "keep_agent": "keep_for_this_agent",
        "reject": "reject",
        "rewrite_global": "rewrite_for_this_user",
        "rewrite_agent": "rewrite_for_this_agent",
        "defer": "defer",
    }
    return labels[action]


def _review_session(
    conn: sqlite3.Connection,
    agent_id: int,
    session_id: int | None,
) -> int:
    if session_id is not None:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if row is None:
            raise ReviewGateError(
                f"review session {session_id} does not belong to agent {agent_id}"
            )
        return int(row[0])
    row = conn.execute(
        "SELECT session_id FROM sessions WHERE agent_id = ? "
        "ORDER BY session_id DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    if row is not None:
        return int(row[0])
    session = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,)).lastrowid
    assert session is not None
    return int(session)


def _rewrite_and_materialize(
    conn: sqlite3.Connection,
    detail: CandidateDetail,
    action: ReviewAction,
    reason: str,
    approval_wal_id: int,
    rewrite_claim: str | None,
) -> tuple[int, int]:
    rewritten = " ".join((rewrite_claim or "").split())
    if not rewritten:
        raise ReviewGateError(f"{action}: rewrite_claim must not be empty")
    source_ids = [source.wal_id for source in detail.sources]
    triage = {
        **detail.triage,
        "rewritten_from_candidate_id": detail.summary.candidate_id,
        "review_action": action,
    }
    if action == "rewrite_global":
        route = "user_review"
        proposed_scope = "user_global"
    else:
        route = "agent"
        proposed_scope = "agent"
    rewritten_candidate_id = enqueue_candidate(
        conn,
        agent_id=detail.summary.agent_id,
        claim=rewritten,
        route=route,
        proposed_scope=proposed_scope,
        category=detail.summary.category,
        source_wal_ids=[*source_ids, approval_wal_id],
        triage=triage,
    )
    if action == "rewrite_global":
        memory_id = approve_candidate_user_global(
            conn,
            rewritten_candidate_id,
            approval_wal_id,
            f"rewritten Owner approval: {reason}",
        )
    else:
        memory_id = accept_candidate_agent(
            conn,
            rewritten_candidate_id,
            f"rewritten Owner approval: {reason}",
            resolved_by_wal_id=approval_wal_id,
        )
    return rewritten_candidate_id, memory_id
