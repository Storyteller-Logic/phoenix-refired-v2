"""Persona proposal substrate laws.

Durable personas materialize as agent rows, but model-proposed personas must
wait in a source/audit friendly proposal queue until same-user review resolves
them. These tests keep that lifecycle from becoming a loose convention.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from brain.substrate import connect, create_brain


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _seed_world(path: Path) -> dict[str, int]:
    create_brain(path)
    with connect(path) as conn:
        owner = int(
            conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)").lastrowid
            or 0
        )
        peer = int(conn.execute("INSERT INTO users (name) VALUES ('peer')").lastrowid or 0)
        parent = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'parent')", (owner,)
            ).lastrowid
            or 0
        )
        proposer = int(
            conn.execute(
                "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'proposer')",
                (owner, parent),
            ).lastrowid
            or 0
        )
        peer_agent = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'peer-agent')", (peer,)
            ).lastrowid
            or 0
        )
        parent_session = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (parent,)).lastrowid or 0
        )
        peer_session = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (peer_agent,)).lastrowid
            or 0
        )
        review_wal = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', ?)",
                (parent_session, "Approve the Scout durable persona."),
            ).lastrowid
            or 0
        )
        peer_review_wal = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', ?)",
                (peer_session, "Peer tries to review someone else's persona."),
            ).lastrowid
            or 0
        )
        conn.commit()
    return {
        "owner": owner,
        "peer": peer,
        "parent": parent,
        "proposer": proposer,
        "peer_agent": peer_agent,
        "parent_session": parent_session,
        "review_wal": review_wal,
        "peer_review_wal": peer_review_wal,
    }


def test_persona_proposal_requires_same_user_parent_and_proposer(tmp_path: Path) -> None:
    ids = _seed_world(tmp_path / "brain.db")
    persona = _canonical_json({"identity": {"role": "critic"}})
    with connect(tmp_path / "brain.db") as conn:
        with pytest.raises(sqlite3.IntegrityError, match="parent"):
            conn.execute(
                """INSERT INTO persona_proposals
                   (user_id, parent_agent_id, proposed_by_agent_id, name, persona_json)
                   VALUES (?, ?, ?, 'bad-parent', ?)""",
                (ids["owner"], ids["peer_agent"], ids["proposer"], persona),
            )
        with pytest.raises(sqlite3.IntegrityError, match="proposer"):
            conn.execute(
                """INSERT INTO persona_proposals
                   (user_id, parent_agent_id, proposed_by_agent_id, name, persona_json)
                   VALUES (?, ?, ?, 'bad-proposer', ?)""",
                (ids["owner"], ids["parent"], ids["peer_agent"], persona),
            )


def test_persona_proposal_review_and_materialization_are_scoped(tmp_path: Path) -> None:
    ids = _seed_world(tmp_path / "brain.db")
    persona = _canonical_json({"identity": {"role": "scout"}})
    with connect(tmp_path / "brain.db") as conn:
        proposal_id = int(
            conn.execute(
                """INSERT INTO persona_proposals
                   (user_id, parent_agent_id, proposed_by_agent_id, name, persona_json)
                   VALUES (?, ?, ?, 'Scout', ?)""",
                (ids["owner"], ids["parent"], ids["proposer"], persona),
            ).lastrowid
            or 0
        )
        child = int(
            conn.execute(
                "INSERT INTO agents (user_id, parent_agent_id, name) VALUES (?, ?, 'Scout')",
                (ids["owner"], ids["parent"]),
            ).lastrowid
            or 0
        )

        with pytest.raises(sqlite3.IntegrityError, match="same-user review"):
            conn.execute(
                """UPDATE persona_proposals
                   SET status = 'approved', materialized_agent_id = ?,
                       review_wal_id = ?, resolution_reason = 'peer review',
                       resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE proposal_id = ?""",
                (child, ids["peer_review_wal"], proposal_id),
            )

        conn.execute(
            """UPDATE persona_proposals
               SET status = 'approved', materialized_agent_id = ?,
                   review_wal_id = ?, resolution_reason = 'approved by owner',
                   resolved_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE proposal_id = ?""",
            (child, ids["review_wal"], proposal_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="resolved"):
            conn.execute(
                "UPDATE persona_proposals SET status = 'pending' WHERE proposal_id = ?",
                (proposal_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
            conn.execute("DELETE FROM persona_proposals WHERE proposal_id = ?", (proposal_id,))


def test_persona_and_delegation_event_types_are_canonical(tmp_path: Path) -> None:
    ids = _seed_world(tmp_path / "brain.db")
    with connect(tmp_path / "brain.db") as conn:
        for index, event_type in enumerate(
            [
                "delegation.requested",
                "delegation.started",
                "delegation.result",
                "delegation.failed",
                "persona.proposed",
                "persona.approved",
                "persona.activated",
                "persona.retired",
                "persona.superseded",
                "persona.grant_requested",
                "persona.grant_decided",
                "persona.policy_updated",
            ],
            start=1,
        ):
            payload = {"event_type": event_type, "index": index}
            conn.execute(
                """INSERT INTO wal_native_events (
                       event_id, event_type, scope_user_id, scope_agent_id,
                       scope_session_id, actor_kind, actor_agent_id, payload_json,
                       payload_sha256
                   ) VALUES (?, ?, ?, ?, ?, 'agent', ?, ?, ?)""",
                (
                    f"phase8-{index}",
                    event_type,
                    ids["owner"],
                    ids["parent"],
                    ids["parent_session"],
                    ids["parent"],
                    _canonical_json(payload),
                    _payload_hash(payload),
                ),
            )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM wal_native_events").fetchone()[0] == 12
