"""Owner review gate for durable memory candidates."""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.memory_candidates import enqueue_candidate
from brain.recall import recall, set_embedder
from brain.review_cli import main as review_main
from brain.review_gate import (
    ReviewDecision,
    ReviewGateError,
    candidate_detail,
    pending_candidates,
    review_candidate,
    review_candidates,
)
from brain.substrate import connect, create_brain


@pytest.fixture()
def review_world(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    path = tmp_path / "review.db"
    create_brain(path)
    ids: dict[str, int] = {}
    with connect(path) as conn:
        user_id = int(
            conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)").lastrowid
            or 0
        )
        agent_id = int(
            conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, 'review-agent')",
                (user_id,),
            ).lastrowid
            or 0
        )
        session_id = int(
            conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,)).lastrowid
            or 0
        )
        source_wal = int(
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) "
                "VALUES (?, 1, 'owner', 'The owner values demonstrated behavior.')",
                (session_id,),
            ).lastrowid
            or 0
        )
        ids.update(
            {
                "agent": agent_id,
                "session": session_id,
                "source_wal": source_wal,
            }
        )
        conn.commit()
    return path, ids


def _candidate(
    path: Path,
    ids: dict[str, int],
    *,
    claim: str = "The owner prefers demonstrated behavior over verbal declarations.",
    route: str = "user_review",
    proposed_scope: str = "user_global",
) -> int:
    with connect(path) as conn:
        candidate_id = enqueue_candidate(
            conn,
            agent_id=ids["agent"],
            claim=claim,
            route=route,
            proposed_scope=proposed_scope,
            category="interpretive_framework",
            source_wal_ids=[ids["source_wal"]],
            triage={"reason": "cross_agent_high_value"},
        )
        conn.commit()
        return candidate_id


def _write_decisions(tmp_path: Path, decisions: list[dict[str, object]]) -> Path:
    path = tmp_path / "review_decisions.json"
    path.write_text(json.dumps({"decisions": decisions}), encoding="utf-8")
    return path


class TokenEmbedder:
    embedder_id = "review-token"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.casefold()
            vectors.append(
                [
                    1.0 if "demonstrated" in lowered or "behavior" in lowered else 0.0,
                    1.0 if "implementation" in lowered or "fidelity" in lowered else 0.0,
                    1.0 if "playlist" in lowered else 0.0,
                    1.0,
                ]
            )
        return vectors


def test_pending_and_detail_show_candidate_with_sources(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path) as conn:
        pending = pending_candidates(conn)
        detail = candidate_detail(conn, candidate_id)

    assert [candidate.candidate_id for candidate in pending] == [candidate_id]
    assert (
        detail.summary.claim
        == "The owner prefers demonstrated behavior over verbal declarations."
    )
    assert detail.triage == {"reason": "cross_agent_high_value"}
    assert [source.wal_id for source in detail.sources] == [ids["source_wal"]]


def test_approve_global_requires_blessed_connection_and_owner_wal(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="Owner approval"):
            review_candidate(
                conn,
                candidate_id,
                "approve_global",
                "Owner approved for global use.",
            )
        conn.rollback()

    with connect(path, blessed=True) as conn:
        result = review_candidate(
            conn,
            candidate_id,
            "approve_global",
            "Owner approved for global use.",
        )
        conn.commit()

    assert result.memory_id is not None
    assert result.approval_wal_id is not None
    with connect(path) as conn:
        assert conn.execute(
            "SELECT scope, content, global_approved_by_wal_id "
            "FROM memories WHERE memory_id = ?",
            (result.memory_id,),
        ).fetchone() == (
            "user_global",
            "The owner prefers demonstrated behavior over verbal declarations.",
            result.approval_wal_id,
        )
        assert conn.execute(
            "SELECT status, resolved_by_wal_id FROM memory_candidates "
            "WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("accepted", result.approval_wal_id)
        review_wal = conn.execute(
            "SELECT content FROM wal WHERE wal_id = ?",
            (result.approval_wal_id,),
        ).fetchone()
        assert review_wal is not None
        assert "promote_to_this_user" in str(review_wal[0])
        assert "approve_global" not in str(review_wal[0])


def test_keep_agent_materializes_agent_memory_with_owner_review_wal(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path, blessed=True) as conn:
        result = review_candidate(
            conn,
            candidate_id,
            "keep_agent",
            "Useful to this agent, not global.",
        )
        conn.commit()

    assert result.memory_id is not None
    assert result.approval_wal_id is not None
    with connect(path) as conn:
        assert conn.execute(
            "SELECT scope FROM memories WHERE memory_id = ?",
            (result.memory_id,),
        ).fetchone() == ("agent",)
        assert conn.execute(
            "SELECT resolved_by_wal_id FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == (result.approval_wal_id,)


def test_reject_records_owner_review_wal_without_memory(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path, blessed=True) as conn:
        result = review_candidate(
            conn,
            candidate_id,
            "reject",
            "Too interpretive.",
        )
        conn.commit()

    assert result.memory_id is None
    assert result.approval_wal_id is not None
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute(
            "SELECT status, resolved_by_wal_id, resolution_reason "
            "FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("rejected", result.approval_wal_id, "Too interpretive.")


def test_rewrite_global_creates_new_candidate_and_resolves_original(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids, claim="The owner always trusts behavior.")

    with connect(path, blessed=True) as conn:
        result = review_candidate(
            conn,
            candidate_id,
            "rewrite_global",
            "Original wording was too absolute.",
            rewrite_claim="The owner prefers demonstrated behavior over verbal declarations.",
        )
        conn.commit()

    assert result.memory_id is not None
    assert result.rewritten_candidate_id is not None
    assert result.approval_wal_id is not None
    with connect(path) as conn:
        assert conn.execute(
            "SELECT status, resolution_reason FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == (
            "rejected",
            f"rewritten as candidate {result.rewritten_candidate_id}: "
            "Original wording was too absolute.",
        )
        assert conn.execute(
            "SELECT claim, status, materialized_memory_id FROM memory_candidates "
            "WHERE candidate_id = ?",
            (result.rewritten_candidate_id,),
        ).fetchone() == (
            "The owner prefers demonstrated behavior over verbal declarations.",
            "accepted",
            result.memory_id,
        )
        assert conn.execute(
            "SELECT wal_id FROM memory_sources WHERE memory_id = ? ORDER BY wal_id",
            (result.memory_id,),
        ).fetchall() == [(ids["source_wal"],), (result.approval_wal_id,)]


def test_rewrite_requires_new_claim(review_world: tuple[Path, dict[str, int]]) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path, blessed=True) as conn:
        with pytest.raises(ReviewGateError, match="rewrite_claim"):
            review_candidate(conn, candidate_id, "rewrite_agent", "Needs clearer wording.")


def test_cli_lists_and_shows_pending_candidate(
    review_world: tuple[Path, dict[str, int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    assert review_main([str(path), "list"]) == 0
    listed = capsys.readouterr().out
    assert str(candidate_id) in listed
    assert "route=user_review" in listed

    assert review_main([str(path), "show", str(candidate_id)]) == 0
    shown = capsys.readouterr().out
    assert "candidate_id:" in shown
    assert "sources:" in shown
    assert str(ids["source_wal"]) in shown

    assert review_main([str(path), "list", "--sources"]) == 0
    sourced = capsys.readouterr().out
    assert "candidate_id:" in sourced
    assert "sources:" in sourced
    assert f"wal_id={ids['source_wal']}" in sourced


def test_cli_apply_decision_file_dry_run_rolls_back(
    review_world: tuple[Path, dict[str, int]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, ids = review_world
    approve_id = _candidate(path, ids)
    reject_id = _candidate(
        path,
        ids,
        claim="The owner always distrusts every verbal declaration.",
    )
    rewrite_id = _candidate(
        path,
        ids,
        claim="The owner values implementation fidelity above everything.",
    )
    keep_id = _candidate(
        path,
        ids,
        claim="The owner curates playlists actively for functional use.",
    )
    defer_id = _candidate(
        path,
        ids,
        claim="The owner may later decide whether this belongs globally.",
    )
    decisions = _write_decisions(
        tmp_path,
        [
            {
                "candidate_id": approve_id,
                "action": "approve_global",
                "reason": "Owner approved stable operating context.",
            },
            {
                "candidate_id": reject_id,
                "action": "reject",
                "reason": "Too absolute and not supported.",
            },
            {
                "candidate_id": rewrite_id,
                "action": "rewrite_global",
                "reason": "Original wording was too absolute.",
                "rewrite_claim": (
                    "The owner values implementation fidelity and correctness "
                    "over surface completion."
                ),
            },
            {
                "candidate_id": keep_id,
                "action": "keep_agent",
                "reason": "Useful for this agent, not global.",
            },
            {"candidate_id": defer_id, "action": "defer", "reason": ""},
        ],
    )

    assert review_main([str(path), "apply", str(decisions), "--dry-run"]) == 0
    output = capsys.readouterr().out

    assert "dry_run=true" in output
    assert "accepted_global=2" in output
    assert "accepted_agent=1" in output
    assert "rejected=1" in output
    assert "rewritten=1" in output
    assert "deferred=1" in output
    with connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_candidates WHERE status = 'pending'"
        ).fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM wal WHERE content LIKE 'Owner review candidate%'"
        ).fetchone()[0] == 0


def test_cli_apply_decision_file_commits_operator_batch(
    review_world: tuple[Path, dict[str, int]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, ids = review_world
    approve_id = _candidate(path, ids)
    reject_id = _candidate(
        path,
        ids,
        claim="The owner always distrusts every verbal declaration.",
    )
    rewrite_id = _candidate(
        path,
        ids,
        claim="The owner values implementation fidelity above everything.",
    )
    keep_id = _candidate(
        path,
        ids,
        claim="The owner curates playlists actively for functional use.",
    )
    defer_id = _candidate(
        path,
        ids,
        claim="The owner may later decide whether this belongs globally.",
    )
    decisions = _write_decisions(
        tmp_path,
        [
            {
                "candidate_id": approve_id,
                "action": "approve_global",
                "reason": "Owner approved stable operating context.",
            },
            {
                "candidate_id": reject_id,
                "action": "reject",
                "reason": "Too absolute and not supported.",
            },
            {
                "candidate_id": rewrite_id,
                "action": "rewrite_global",
                "reason": "Original wording was too absolute.",
                "rewrite_claim": (
                    "The owner values implementation fidelity and correctness "
                    "over surface completion."
                ),
            },
            {
                "candidate_id": keep_id,
                "action": "keep_agent",
                "reason": "Useful for this agent, not global.",
            },
            {"candidate_id": defer_id, "action": "defer", "reason": ""},
        ],
    )

    assert review_main([str(path), "apply", str(decisions)]) == 0
    output = capsys.readouterr().out

    assert "dry_run=false" in output
    assert "accepted_global=2" in output
    assert "accepted_agent=1" in output
    assert "rejected=1" in output
    assert "rewritten=1" in output
    assert "deferred=1" in output
    with connect(path) as conn:
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (approve_id,),
        ).fetchone() == ("accepted",)
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (reject_id,),
        ).fetchone() == ("rejected",)
        assert conn.execute(
            "SELECT status, resolution_reason FROM memory_candidates WHERE candidate_id = ?",
            (rewrite_id,),
        ).fetchone()[0] == "rejected"
        assert conn.execute(
            "SELECT resolution_reason FROM memory_candidates WHERE candidate_id = ?",
            (rewrite_id,),
        ).fetchone()[0].endswith(": Original wording was too absolute.")
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (keep_id,),
        ).fetchone() == ("accepted",)
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (defer_id,),
        ).fetchone() == ("pending",)
        assert conn.execute(
            "SELECT COUNT(*) FROM memories WHERE scope = 'user_global'"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM memories WHERE scope = 'agent'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM wal WHERE content LIKE 'Owner review candidate%'"
        ).fetchone()[0] == 4


def test_cli_apply_decision_file_fails_closed_on_invalid_batch(
    review_world: tuple[Path, dict[str, int]],
    tmp_path: Path,
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)
    decisions = _write_decisions(
        tmp_path,
        [
            {"candidate_id": candidate_id, "action": "defer", "reason": ""},
            {
                "candidate_id": candidate_id,
                "action": "reject",
                "reason": "Duplicate should fail.",
            },
        ],
    )

    with pytest.raises(ReviewGateError, match="duplicate candidate"):
        review_main([str(path), "apply", str(decisions)])

    with connect(path) as conn:
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("pending",)
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_cli_apply_decision_file_validates_schema_before_writes(
    review_world: tuple[Path, dict[str, int]],
    tmp_path: Path,
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)
    decisions = tmp_path / "invalid_review_decisions.json"
    decisions.write_text(
        json.dumps([{"candidate_id": candidate_id, "action": "reject"}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="keys must include"):
        review_main([str(path), "apply", str(decisions)])

    with connect(path) as conn:
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone() == ("pending",)


def test_batch_review_burn_down_preserves_state_after_reconnect(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    approve_id = _candidate(
        path,
        ids,
        claim="The owner prefers demonstrated behavior over verbal declarations.",
    )
    reject_id = _candidate(
        path,
        ids,
        claim="The owner always distrusts every verbal declaration.",
    )
    rewrite_id = _candidate(
        path,
        ids,
        claim="The owner wants implementation fidelity above everything.",
    )
    keep_id = _candidate(
        path,
        ids,
        claim="The owner curates playlists actively for functional use.",
    )
    defer_id = _candidate(
        path,
        ids,
        claim="The owner may later decide whether this belongs globally.",
    )

    with connect(path, blessed=True) as conn:
        results = review_candidates(
            conn,
            [
                ReviewDecision(
                    approve_id,
                    "approve_global",
                    "Owner approved stable operating context.",
                ),
                ReviewDecision(
                    reject_id,
                    "reject",
                    "Too absolute and not supported.",
                ),
                ReviewDecision(
                    rewrite_id,
                    "rewrite_global",
                    "Original wording was too absolute.",
                    rewrite_claim=(
                        "The owner values implementation fidelity and correctness "
                        "over surface completion."
                    ),
                ),
                ReviewDecision(
                    keep_id,
                    "keep_agent",
                    "Useful for this agent, not global.",
                ),
                ReviewDecision(
                    defer_id,
                    "defer",
                    "",
                ),
            ],
        )
        embedder = TokenEmbedder()
        set_embedder(conn, embedder)
        conn.commit()

    by_action = {result.action: result for result in results if result.action != "defer"}
    assert by_action["approve_global"].memory_id is not None
    assert by_action["rewrite_global"].memory_id is not None
    assert by_action["rewrite_global"].rewritten_candidate_id is not None
    assert by_action["keep_agent"].memory_id is not None

    with connect(path) as conn:
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (approve_id,),
        ).fetchone() == ("accepted",)
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (reject_id,),
        ).fetchone() == ("rejected",)
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (rewrite_id,),
        ).fetchone() == ("rejected",)
        assert conn.execute(
            "SELECT status FROM memory_candidates WHERE candidate_id = ?",
            (defer_id,),
        ).fetchone() == ("pending",)
        assert conn.execute(
            "SELECT scope FROM memories WHERE memory_id = ?",
            (by_action["keep_agent"].memory_id,),
        ).fetchone() == ("agent",)
        assert conn.execute(
            "SELECT scope FROM memories WHERE memory_id = ?",
            (by_action["approve_global"].memory_id,),
        ).fetchone() == ("user_global",)
        hits = recall(
            conn,
            embedder,
            "What does the owner value about implementation fidelity?",
            agent_id=ids["agent"],
            floor=0.2,
        )
        contents = [hit.content for hit in hits]
        assert any("implementation fidelity and correctness" in content for content in contents)
        assert all("above everything" not in content for content in contents)
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_candidates WHERE status = 'pending'"
        ).fetchone()[0] == 1


def test_batch_review_refuses_duplicate_candidate(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path, blessed=True) as conn:
        with pytest.raises(ReviewGateError, match="duplicate candidate"):
            review_candidates(
                conn,
                [
                    ReviewDecision(candidate_id, "defer", ""),
                    ReviewDecision(candidate_id, "reject", "duplicate should fail"),
                ],
            )


def test_batch_review_does_not_reopen_resolved_candidate(
    review_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = review_world
    candidate_id = _candidate(path, ids)

    with connect(path, blessed=True) as conn:
        review_candidates(
            conn,
            [ReviewDecision(candidate_id, "reject", "Owner rejected.")],
        )
        with pytest.raises(ReviewGateError, match="not pending"):
            review_candidates(
                conn,
                [ReviewDecision(candidate_id, "keep_agent", "Try to reopen.")],
            )
