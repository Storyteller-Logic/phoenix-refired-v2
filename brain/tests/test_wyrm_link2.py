"""Wyrm chain link 2 — the hardenings the Gemma-Opus review earned, after
every finding was verified by live probe (operation.spec §2.1).

Most findings were refuted (the reviewer misread the three-tier connection
trust model). The real kernel: the recall verbs should refuse a scoped
agent connection INTENTIONALLY and clearly — today's denial is incidental,
an opaque authorizer trip on an internal table. These proofs pin the
intentional refusal.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.recall import RecallError, recall, search
from brain.substrate import connect, connect_agent, create_brain


class ToyStone:
    embedder_id = "toy"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, int]:
    path = tmp_path / "test-brain.db"
    create_brain(path)
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'a1')", (cur.lastrowid,)
        )
        agent_id = cur.lastrowid
        assert agent_id is not None
        conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, 'a fact')", (agent_id,)
        )
        conn.commit()
    return path, agent_id


def test_search_refuses_a_scoped_connection_clearly(world: tuple[Path, int]) -> None:
    path, agent_id = world
    scoped = connect_agent(path, agent_id)
    with pytest.raises(RecallError, match="harness connection"):
        search(scoped, "fact")
    scoped.close()


def test_recall_refuses_a_scoped_connection_clearly(world: tuple[Path, int]) -> None:
    path, agent_id = world
    scoped = connect_agent(path, agent_id)
    with pytest.raises(RecallError, match="harness connection"):
        recall(scoped, ToyStone(), "fact")
    scoped.close()


def test_harness_connection_still_works(world: tuple[Path, int]) -> None:
    """The guard must not break the lawful caller — a harness connection."""
    path, _ = world
    with connect(path) as conn:
        assert isinstance(search(conn, "fact"), list)  # runs, no refusal
