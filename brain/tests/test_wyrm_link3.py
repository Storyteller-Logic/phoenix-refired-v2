"""Wyrm chain link 3 — the one hardening the Qwen review earned, after every
finding was verified by live probe (operation.spec §2.1).

Qwen's two high-severity findings (global_settings UPDATE gate allegedly
missing; lineage trigger allegedly not firing on a parent-only change) were
both REFUTED by probe. Its output then degenerated into a repetition loop.
The single real kernel: a faulty embedder could store dimension-inconsistent
vectors that only crash later, at recall, with a misleading message. The
guard catches it at the source.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.recall import RecallError, embed_pending, set_embedder
from brain.substrate import connect, create_brain


class RaggedStone:
    """A buggy stone: returns vectors of inconsistent dimension."""

    embedder_id = "ragged"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0][: 2 + i % 2] for i, _ in enumerate(texts)]


@pytest.fixture()
def brain_with_two_memories(tmp_path: Path) -> Path:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'a1')", (cur.lastrowid,)
        )
        agent_id = cur.lastrowid
        for text in ("first memory", "second memory"):
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)", (agent_id, text)
            )
        conn.commit()
    return path


def test_ragged_embedder_is_caught_at_embed_time(brain_with_two_memories: Path) -> None:
    path = brain_with_two_memories
    with connect(path, blessed=True) as conn:
        with pytest.raises(RecallError, match="dimension"):
            set_embedder(conn, RaggedStone())  # set_embedder calls embed_pending


def test_ragged_embedder_writes_no_vectors(brain_with_two_memories: Path) -> None:
    """The guard must fire BEFORE any insert — no partial, corrupt vector set."""
    path = brain_with_two_memories
    with connect(path) as conn:
        with pytest.raises(RecallError, match="dimension"):
            embed_pending(conn, RaggedStone())
        count = conn.execute("SELECT COUNT(memory_id) FROM embeddings").fetchone()[0]
        assert count == 0  # nothing stored
