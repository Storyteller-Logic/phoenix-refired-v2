"""Fail-loud proofs for the embedder slot and recall()
(brain.spec §4 — R10 second half, R11, A10 mechanics, A7 ranking clause).

The embedders here are deterministic toys (hashed bag-of-words); their
similarity bands were MEASURED (related >= 0.365, unrelated <= 0.277 across
both stones) and the explicit floor=0.3 sits in that gap — the floor
PARAMETER is what these proofs exercise; the 0.5 default is the starting
value for the real stone. The Brain's criteria measure the SLOT — never-mix,
auto-re-embed, floor-then-worth — not any particular model. The real stone
arrives with the Harness.
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.learning import reinforce
from brain.recall import (
    RecallError,
    active_embedder_id,
    embed_pending,
    recall,
    set_embedder,
)
from brain.substrate import connect, connect_agent, create_brain


class ToyEmbedder:
    """Deterministic hashed bag-of-words. Same tokens -> similar vectors;
    a different salt is a genuinely different vector space."""

    def __init__(self, embedder_id: str, salt: int = 0, dim: int = 32) -> None:
        self.embedder_id = embedder_id
        self._salt = salt
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dim
            for token in text.lower().split():
                digest = hashlib.md5(f"{self._salt}:{token}".encode()).hexdigest()
                vector[int(digest[:8], 16) % self._dim] += 1.0
            vectors.append(vector)
        return vectors


STONE_A = ToyEmbedder("toy-a", salt=1)
STONE_B = ToyEmbedder("toy-b", salt=2)


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, int, int, dict[str, int]]:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    ids: dict[str, int] = {}
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        agents = []
        for name in ("alpha", "beta"):
            cur = conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, name)
            )
            agents.append(cur.lastrowid or 0)
        for key, agent, content in [
            ("walk", agents[0], "the gauntlet walk goes model user agent session chat"),
            ("walk_twin", agents[0], "the gauntlet walk is the door into the system"),
            ("wyrm", agents[0], "the wyrm chain reviews code with gemma and qwen"),
            ("beta_walk", agents[1], "beta also knows the gauntlet walk order"),
            ("noise", agents[0], "completely unrelated topic about cooking soup"),
        ]:
            cur = conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)", (agent, content)
            )
            ids[key] = cur.lastrowid or 0
        conn.commit()
    return path, agents[0], agents[1], ids


def _blessed(path: Path) -> sqlite3.Connection:
    return connect(path, blessed=True)


def test_recall_refuses_without_an_active_stone(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, _, _, _ = world
    with connect(path) as conn:
        with pytest.raises(RecallError, match="no active embedder"):
            recall(conn, STONE_A, "gauntlet walk")


def test_set_embedder_requires_the_blessing(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, _, _, _ = world
    with connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="blessing"):
            set_embedder(conn, STONE_A)


def test_set_embedder_re_embeds_everything(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, _, _, _ = world
    with _blessed(path) as conn:
        count = set_embedder(conn, STONE_A)
        assert count == 5  # every non-retired memory got a vector, automatically
        assert active_embedder_id(conn) == "toy-a"
        vectors = conn.execute(
            "SELECT COUNT(memory_id) FROM embeddings WHERE embedder_id = 'toy-a'"
        ).fetchone()[0]
        assert vectors == 5


def test_recall_floor_then_worth(world: tuple[Path, int, int, dict[str, int]]) -> None:
    path, alpha, _, ids = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    with connect(path) as conn:
        # make the irrelevant memory the highest-worth row in the Brain,
        # and reinforce one walk-twin so worth must sort within the floor
        for _ in range(5):
            reinforce(conn, ids["noise"], "soup was useful, irrelevantly")
        reinforce(conn, ids["walk_twin"], "the walk fact proved out")
        hits = recall(conn, STONE_A, "gauntlet walk", agent_id=alpha, floor=0.3)
        contents = [h.content for h in hits]
        assert any("model user agent session" in c for c in contents)
        assert any("door into the system" in c for c in contents)
        assert all("soup" not in c for c in contents)  # high worth, no relevance: drowned
        assert hits[0].memory_id == ids["walk_twin"]  # worth ranks within the floor
        assert hits[0].worth > hits[1].worth


def test_recall_filters_agent_and_skips_retired(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, beta, ids = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    with connect(path) as conn:
        beta_hits = recall(conn, STONE_A, "gauntlet walk", agent_id=beta, floor=0.3)
        assert [h.memory_id for h in beta_hits] == [ids["beta_walk"]]
        conn.execute(
            "UPDATE memories SET status = 'retired', retired_reason = 'drill' "
            "WHERE memory_id = ?",
            (ids["walk"],),
        )
        hits = recall(conn, STONE_A, "gauntlet walk", agent_id=alpha, floor=0.3)
        assert ids["walk"] not in [h.memory_id for h in hits]  # retired never surfaces


def test_recall_writes_recall_ledger_events(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, ids = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    with connect(path) as conn:
        hits = recall(conn, STONE_A, "wyrm chain reviews", agent_id=alpha)
        assert hits, "the wyrm memory must be recalled"
        row = conn.execute(
            "SELECT event, cause FROM learning_ledger "
            "WHERE memory_id = ? AND event = 'recall'",
            (ids["wyrm"],),
        ).fetchone()
        assert row is not None and "wyrm chain reviews" in row[1]


def test_stone_swap_drill(world: tuple[Path, int, int, dict[str, int]]) -> None:
    """A10 mechanics: swap -> automatic re-embed from content -> recall
    still answers -> zero knowledge lost -> old stone refused."""
    path, alpha, _, ids = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        before = recall(conn, STONE_A, "gauntlet walk", agent_id=alpha, floor=0.3)
        assert before, "stone A must answer before the swap"
        memories_before = conn.execute("SELECT COUNT(memory_id) FROM memories").fetchone()[0]
        count = set_embedder(conn, STONE_B)  # the swap
        assert count == 5  # automatic full re-embed from content
        conn.commit()
    with connect(path) as conn:
        after = recall(conn, STONE_B, "gauntlet walk", agent_id=alpha, floor=0.3)
        assert {h.memory_id for h in after} == {h.memory_id for h in before}  # equivalent
        memories_after = conn.execute("SELECT COUNT(memory_id) FROM memories").fetchone()[0]
        assert memories_after == memories_before  # zero knowledge lost
        with pytest.raises(RecallError, match="active"):
            recall(conn, STONE_A, "gauntlet walk")  # vectors are never mixed


def test_late_memories_are_embedded_on_next_recall(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, "
            "'a late gauntlet walk note added after the stone was set')",
            (alpha,),
        )
        hits = recall(conn, STONE_A, "gauntlet walk note", agent_id=alpha, floor=0.3)
        assert any("late" in h.content for h in hits)  # self-healing, like search


def test_embed_pending_counts(world: tuple[Path, int, int, dict[str, int]]) -> None:
    path, alpha, _, _ = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    with connect(path) as conn:
        assert embed_pending(conn, STONE_A) == 0  # nothing pending after the set
        conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, 'fresh fact')", (alpha,)
        )
        assert embed_pending(conn, STONE_A) == 1


def test_scoped_connection_cannot_read_embeddings(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    with _blessed(path) as conn:
        set_embedder(conn, STONE_A)
        conn.commit()
    a = connect_agent(path, alpha)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("SELECT * FROM embeddings")
    a.close()
