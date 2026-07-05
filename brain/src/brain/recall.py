"""Recall verbs (brain.spec §4).

search() — dumb technology from a smart operator: keyword full-text search
over memories and the WAL, no embedding in the loop, ranked by BM25.

recall() — semantic similarity through the embedder SLOT: the relevance
floor selects, earned worth ranks. The embedder itself is a stone in the
Harness's Infinity Glove; the Brain owns the slot — active-stone
bookkeeping, automatic re-embed on swap, and the never-mix law. Content is
canonical; vectors are derived data and can always be rebuilt.

Doctrine for both: retrieve generously; the agent reasons, the embedder
does not rank truth.
"""

import json
import math
import sqlite3
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from brain.substrate import sync_fts


class Embedder(Protocol):
    """A stone for the embedding slot: a stable identity and a batch encoder."""

    embedder_id: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class RecallError(RuntimeError):
    """recall() was asked to run without a lawful stone in the slot, or on
    the wrong tier of connection."""


def _refuse_scoped_connection(conn: sqlite3.Connection, verb: str) -> None:
    """The recall verbs are HARNESS tools — they query across the index and
    every agent the caller is entitled to (the caller passes agent_id to
    filter). A scoped agent connection must never run them: it would trip
    the isolation authorizer anyway, but we refuse it here intentionally,
    with a clear message, rather than leaning on an incidental denial
    (Wyrm chain link 2, hardening the trust boundary)."""
    try:
        scoped = conn.execute("SELECT current_agent_id()").fetchone()
    except sqlite3.OperationalError:
        return  # no such function: a harness/blessed connection — the lawful caller
    raise RecallError(
        f"{verb}() needs a harness connection, not a scoped agent connection "
        f"(agent {scoped[0]}); the harness runs recall on the agent's behalf"
    )


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: str  # 'memory' or 'wal'
    row_id: int
    agent_id: int
    content: str
    rank: float  # BM25: lower (more negative) is a better match


def _sanitize(query: str) -> str:
    """Quote every token: user words are words, never FTS5 syntax."""
    tokens = [t.replace('"', '""') for t in query.split()]
    return " ".join(f'"{t}"' for t in tokens)


_MEMORY_SQL = (
    "SELECT m.memory_id, m.agent_id, m.content, bm25(memories_fts) "
    "FROM memories_fts "
    "JOIN memories m ON m.memory_id = memories_fts.rowid "
    "WHERE memories_fts MATCH ?"
)

_WAL_SQL = (
    "SELECT w.wal_id, s.agent_id, w.content, bm25(wal_fts) "
    "FROM wal_fts "
    "JOIN wal w ON w.wal_id = wal_fts.rowid "
    "JOIN sessions s ON s.session_id = w.session_id "
    "WHERE wal_fts MATCH ?"
)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    agent_id: int | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Keyword search across memories AND the WAL on a harness connection.

    All query tokens must match (implicit AND). Optionally filtered to one
    agent. Returns hits best-first; [] when nothing matches.
    """
    _refuse_scoped_connection(conn, "search")
    match = _sanitize(query)
    if not match:
        return []
    sync_fts(conn)  # self-syncing: the index is fresh at the moment it is read
    hits: list[SearchHit] = []
    for kind, sql in (("memory", _MEMORY_SQL), ("wal", _WAL_SQL)):
        scoped_sql = sql
        params: tuple[object, ...] = (match,)
        if agent_id is not None:
            if kind == "memory":
                scoped_sql += (
                    " AND (m.agent_id = ? OR (m.scope = 'user_global' "
                    "AND m.agent_id IN ("
                    "SELECT peer.agent_id FROM agents peer "
                    "JOIN agents requester ON requester.user_id = peer.user_id "
                    "WHERE requester.agent_id = ?)))"
                )
                params = (match, agent_id, agent_id)
            else:
                scoped_sql += " AND s.agent_id = ?"
                params = (match, agent_id)
        for row in conn.execute(scoped_sql, params):
            hits.append(SearchHit(kind, row[0], row[1], row[2], row[3]))
    hits.sort(key=lambda h: h.rank)
    return hits[:limit]


# --- the semantic verb: recall() through the embedder slot ----------------------


@dataclass(frozen=True, slots=True)
class RecallHit:
    memory_id: int
    agent_id: int
    content: str
    worth: float
    similarity: float
    is_failure: bool
    scope: str = "agent"


@dataclass(frozen=True, slots=True)
class HybridRecallHit:
    kind: str  # 'memory' or 'wal'
    row_id: int
    agent_id: int
    content: str
    similarity: float
    session_id: int | None = None
    turn: int | None = None
    role: str | None = None
    worth: float | None = None
    status: str | None = None
    scope: str | None = None
    is_failure: bool = False
    source_wal_ids: tuple[int, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WalContext:
    wal_id: int
    session_id: int
    turn: int
    role: str
    content: str
    relation: str  # 'source', 'previous', or 'next'


@dataclass(frozen=True, slots=True)
class HybridRecallResult:
    hits: tuple[HybridRecallHit, ...]
    context: tuple[WalContext, ...]


def _pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    vector = array("f")
    vector.frombytes(blob)
    return list(vector)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise RecallError(
            f"vector dimensions disagree ({len(a)} vs {len(b)}): mixed stones?"
        )
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def active_embedder_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM global_settings WHERE key = 'active_embedder'"
    ).fetchone()
    return None if row is None else str(row[0])


def set_embedder(conn: sqlite3.Connection, embedder: Embedder) -> int:
    """Put a stone in the slot (a blessed act — the gate enforces it) and
    automatically re-embed every non-retired memory from canonical content.
    Returns the number of memories embedded. A swap can never lose
    knowledge, because knowledge was never stored in the vectors (§4)."""
    conn.execute(
        "INSERT INTO global_settings (key, value) VALUES ('active_embedder', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (embedder.embedder_id,),
    )
    memories = embed_pending(conn, embedder)
    embed_pending_wal(conn, embedder)
    return memories


def _known_widths(conn: sqlite3.Connection, embedder_id: str) -> set[int]:
    rows = conn.execute(
        "SELECT LENGTH(vector) / 4 FROM embeddings WHERE embedder_id = ? "
        "UNION SELECT LENGTH(vector) / 4 FROM wal_embeddings WHERE embedder_id = ?",
        (embedder_id, embedder_id),
    ).fetchall()
    return {int(row[0]) for row in rows}


def embed_pending(conn: sqlite3.Connection, embedder: Embedder) -> int:
    """Embed every non-retired memory that lacks a vector under this stone.
    Harness connections may call this freely — vectors are derived data."""
    rows = conn.execute(
        "SELECT memory_id, content FROM memories WHERE status <> 'retired' "
        "AND memory_id NOT IN "
        "(SELECT memory_id FROM embeddings WHERE embedder_id = ?) "
        "ORDER BY memory_id",
        (embedder.embedder_id,),
    ).fetchall()
    if not rows:
        return 0
    vectors = embedder.embed([str(content) for _, content in rows])
    # A stone must speak one language: every vector the same width. Caught
    # here, at the source, BEFORE any insert — a faulty embedder cannot
    # poison the table with ragged vectors that only crash later at recall
    # (Wyrm chain link 3). Validated against the stone's own first vector
    # and any width already on file for this embedder_id.
    widths = {len(v) for v in vectors}
    widths.update(_known_widths(conn, embedder.embedder_id))
    if len(widths) > 1:
        raise RecallError(
            f"embedder {embedder.embedder_id!r} returned inconsistent vector "
            f"dimensions {sorted(widths)}: a stone must speak one width"
        )
    conn.executemany(
        "INSERT INTO embeddings (memory_id, embedder_id, vector) VALUES (?, ?, ?)",
        [
            (memory_id, embedder.embedder_id, _pack(vector))
            for (memory_id, _), vector in zip(rows, vectors, strict=True)
        ],
    )
    return len(rows)


def embed_pending_wal(conn: sqlite3.Connection, embedder: Embedder) -> int:
    """Embed canonical WAL rows missing a vector under the active stone."""
    rows = conn.execute(
        "SELECT wal_id, content FROM wal WHERE wal_id NOT IN "
        "(SELECT wal_id FROM wal_embeddings WHERE embedder_id = ?) ORDER BY wal_id",
        (embedder.embedder_id,),
    ).fetchall()
    if not rows:
        return 0
    vectors = embedder.embed([str(content) for _, content in rows])
    widths = {len(vector) for vector in vectors}
    widths.update(_known_widths(conn, embedder.embedder_id))
    if len(widths) > 1:
        raise RecallError(
            f"embedder {embedder.embedder_id!r} returned inconsistent vector "
            f"dimensions {sorted(widths)}: a stone must speak one width"
        )
    conn.executemany(
        "INSERT INTO wal_embeddings (wal_id, embedder_id, vector) VALUES (?, ?, ?)",
        [
            (wal_id, embedder.embedder_id, _pack(vector))
            for (wal_id, _), vector in zip(rows, vectors, strict=True)
        ],
    )
    return len(rows)


def _require_active_embedder(conn: sqlite3.Connection, embedder: Embedder) -> None:
    active = active_embedder_id(conn)
    if active is None:
        raise RecallError("no active embedder — the slot is empty; set_embedder() first")
    if embedder.embedder_id != active:
        raise RecallError(
            f"stone {embedder.embedder_id!r} offered but {active!r} is active: "
            "vectors are never mixed (§4)"
        )


def recall(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    *,
    agent_id: int | None = None,
    floor: float = 0.5,
    limit: int = 10,
) -> list[RecallHit]:
    """Semantic recall: the relevance floor selects, earned worth ranks
    (§4 — high-worth-but-irrelevant noise never drowns real matches).
    Refuses to run without the active stone: vectors are never mixed.
    Every returned hit is recorded in the learning ledger as a 'recall'."""
    _refuse_scoped_connection(conn, "recall")
    _require_active_embedder(conn, embedder)
    embed_pending(conn, embedder)  # self-healing, like search's sync
    query_vector = embedder.embed([query])[0]
    sql = (
        "SELECT e.memory_id, m.agent_id, m.content, m.worth, m.is_failure, "
        "m.scope, e.vector FROM embeddings e "
        "JOIN memories m ON m.memory_id = e.memory_id "
        "WHERE e.embedder_id = ? AND m.status <> 'retired'"
    )
    params: tuple[object, ...] = (embedder.embedder_id,)
    if agent_id is not None:
        sql += (
            " AND (m.agent_id = ? OR (m.scope = 'user_global' "
            "AND m.agent_id IN ("
            "SELECT peer.agent_id FROM agents peer "
            "JOIN agents requester ON requester.user_id = peer.user_id "
            "WHERE requester.agent_id = ?)))"
        )
        params = (embedder.embedder_id, agent_id, agent_id)
    hits: list[RecallHit] = []
    for memory_id, owner, content, worth, is_failure, scope, blob in conn.execute(
        sql, params
    ):
        similarity = _cosine(query_vector, _unpack(blob))
        if similarity >= floor:
            hits.append(
                RecallHit(
                    memory_id,
                    owner,
                    content,
                    worth,
                    similarity,
                    bool(is_failure),
                    str(scope),
                )
            )
    hits.sort(key=lambda h: (-h.worth, -h.similarity))
    hits = hits[:limit]
    cause = f"recall: {query[:120]}"
    conn.executemany(
        "INSERT INTO learning_ledger (memory_id, event, cause) VALUES (?, 'recall', ?)",
        [(h.memory_id, cause) for h in hits],
    )
    return hits


def _memory_provenance(
    conn: sqlite3.Connection, memory_id: int
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    rows = conn.execute(
        "SELECT wal_id, warnings_json FROM memory_sources "
        "WHERE memory_id = ? ORDER BY wal_id",
        (memory_id,),
    ).fetchall()
    warnings: set[str] = set()
    for _, raw in rows:
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            warnings.update(str(item) for item in parsed)
    return tuple(int(row[0]) for row in rows), tuple(sorted(warnings))


def _adjacent_owner_context(
    conn: sqlite3.Connection,
    selected: list[HybridRecallHit],
    *,
    limit: int,
    wal_before_id: int | None,
) -> tuple[WalContext, ...]:
    if limit <= 0:
        return ()
    contexts: list[WalContext] = []
    seen: set[int] = set()
    direct_wal_ids = {
        hit.row_id for hit in selected if hit.kind == "wal"
    }
    source_ids = {
        wal_id for hit in selected for wal_id in hit.source_wal_ids
    } | direct_wal_ids
    for wal_id in sorted(source_ids):
        if wal_before_id is not None and wal_id >= wal_before_id:
            continue
        row = conn.execute(
            "SELECT wal_id, session_id, turn, role, content FROM wal WHERE wal_id = ?",
            (wal_id,),
        ).fetchone()
        if row is None:
            continue
        _, session_id, turn, _, _ = row
        candidates = []
        if wal_id not in direct_wal_ids:
            candidates.append((*row, "source"))
        cutoff_sql = " AND wal_id < ?" if wal_before_id is not None else ""
        cutoff_params: tuple[object, ...] = (
            (wal_before_id,) if wal_before_id is not None else ()
        )
        previous = conn.execute(
            "SELECT wal_id, session_id, turn, role, content FROM wal "
            "WHERE session_id = ? AND role IN ('owner', 'user') AND turn < ? "
            + cutoff_sql
            + " ORDER BY turn DESC LIMIT 1",
            (session_id, turn, *cutoff_params),
        ).fetchone()
        following = conn.execute(
            "SELECT wal_id, session_id, turn, role, content FROM wal "
            "WHERE session_id = ? AND role IN ('owner', 'user') AND turn > ? "
            + cutoff_sql
            + " ORDER BY turn LIMIT 1",
            (session_id, turn, *cutoff_params),
        ).fetchone()
        if previous is not None:
            candidates.append((*previous, "previous"))
        if following is not None:
            candidates.append((*following, "next"))
        for candidate in candidates:
            context_wal_id = int(candidate[0])
            if context_wal_id in seen or context_wal_id in direct_wal_ids:
                continue
            seen.add(context_wal_id)
            contexts.append(
                WalContext(
                    wal_id=context_wal_id,
                    session_id=int(candidate[1]),
                    turn=int(candidate[2]),
                    role=str(candidate[3]),
                    content=str(candidate[4]),
                    relation=str(candidate[5]),
                )
            )
            if len(contexts) >= limit:
                return tuple(contexts)
    return tuple(contexts)


def hybrid_recall(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    *,
    agent_id: int | None = None,
    floor: float = 0.5,
    limit: int = 10,
    context_limit: int = 30,
    wal_before_id: int | None = None,
) -> HybridRecallResult:
    """Recall provisional memories and immutable owner WAL with provenance.

    Durable/provisional memories lead when available; immutable WAL is the
    fallback that preserves facts not yet distilled. Within each tier,
    similarity builds a generous candidate pool. Source-turn diversity keeps
    repeated memories or transcript fragments from crowding the context, then
    adjacent owner turns restore terse conversational answers.
    """
    _refuse_scoped_connection(conn, "hybrid_recall")
    if limit <= 0:
        return HybridRecallResult((), ())
    _require_active_embedder(conn, embedder)
    embed_pending(conn, embedder)
    embed_pending_wal(conn, embedder)
    query_vector = embedder.embed([query])[0]
    candidates: list[HybridRecallHit] = []

    memory_sql = (
        "SELECT e.memory_id, m.agent_id, m.content, m.worth, m.status, "
        "m.scope, m.is_failure, e.vector FROM embeddings e "
        "JOIN memories m ON m.memory_id = e.memory_id "
        "WHERE e.embedder_id = ? AND m.status <> 'retired'"
    )
    memory_params: tuple[object, ...] = (embedder.embedder_id,)
    if agent_id is not None:
        memory_sql += (
            " AND (m.agent_id = ? OR (m.scope = 'user_global' "
            "AND m.agent_id IN ("
            "SELECT peer.agent_id FROM agents peer "
            "JOIN agents requester ON requester.user_id = peer.user_id "
            "WHERE requester.agent_id = ?)))"
        )
        memory_params = (embedder.embedder_id, agent_id, agent_id)
    for memory_id, owner, content, worth, status, scope, is_failure, blob in conn.execute(
        memory_sql, memory_params
    ):
        similarity = _cosine(query_vector, _unpack(blob))
        if similarity < floor:
            continue
        source_wal_ids, warnings = _memory_provenance(conn, int(memory_id))
        candidates.append(
            HybridRecallHit(
                kind="memory",
                row_id=int(memory_id),
                agent_id=int(owner),
                content=str(content),
                similarity=similarity,
                worth=float(worth),
                status=str(status),
                scope=str(scope),
                is_failure=bool(is_failure),
                source_wal_ids=source_wal_ids,
                warnings=warnings,
            )
        )

    wal_sql = (
        "SELECT we.wal_id, s.agent_id, w.session_id, w.turn, w.role, w.content, "
        "we.vector FROM wal_embeddings we JOIN wal w ON w.wal_id = we.wal_id "
        "JOIN sessions s ON s.session_id = w.session_id "
        "WHERE we.embedder_id = ? AND w.role IN ('owner', 'user')"
    )
    wal_params: tuple[object, ...] = (embedder.embedder_id,)
    if agent_id is not None:
        wal_sql += " AND s.agent_id = ?"
        wal_params = (embedder.embedder_id, agent_id)
    if wal_before_id is not None:
        wal_sql += " AND we.wal_id < ?"
        wal_params = (*wal_params, wal_before_id)
    for wal_id, owner, session_id, turn, role, content, blob in conn.execute(
        wal_sql, wal_params
    ):
        similarity = _cosine(query_vector, _unpack(blob))
        if similarity < floor:
            continue
        candidates.append(
            HybridRecallHit(
                kind="wal",
                row_id=int(wal_id),
                agent_id=int(owner),
                content=str(content),
                similarity=similarity,
                session_id=int(session_id),
                turn=int(turn),
                role=str(role),
                source_wal_ids=(int(wal_id),),
            )
        )

    candidates.sort(
        key=lambda hit: (
            hit.kind != "memory",
            -hit.similarity,
            -(hit.worth or 0.0),
            hit.row_id,
        )
    )
    selected: list[HybridRecallHit] = []
    used_wal_ids: set[int] = set()
    for candidate in candidates:
        source_ids = set(candidate.source_wal_ids)
        if source_ids and source_ids.intersection(used_wal_ids):
            continue
        selected.append(candidate)
        used_wal_ids.update(source_ids)
        if len(selected) >= limit:
            break

    cause = f"hybrid recall: {query[:120]}"
    conn.executemany(
        "INSERT INTO learning_ledger (memory_id, event, cause) VALUES (?, 'recall', ?)",
        [(hit.row_id, cause) for hit in selected if hit.kind == "memory"],
    )
    context = _adjacent_owner_context(
        conn, selected, limit=context_limit, wal_before_id=wal_before_id
    )
    return HybridRecallResult(tuple(selected), context)
