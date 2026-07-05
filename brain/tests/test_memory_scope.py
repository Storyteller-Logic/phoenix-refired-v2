"""User-global memory scope gates and recall visibility."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.learning import promote, promote_user_global, reinforce
from brain.recall import hybrid_recall, recall, set_embedder
from brain.substrate import connect, connect_agent, create_brain


class ScopeEmbedder:
    embedder_id = "scope-test"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    1.0 if "global" in lowered else 0.0,
                    1.0 if "alpha" in lowered else 0.0,
                    1.0 if "beta" in lowered else 0.0,
                    1.0 if "other" in lowered else 0.0,
                ]
            )
        return vectors


@pytest.fixture()
def scoped_world(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    path = tmp_path / "scope.db"
    create_brain(path)
    ids: dict[str, int] = {}
    with connect(path) as conn:
        owner_user = conn.execute(
            "INSERT INTO users (name, is_owner) VALUES ('owner', 1)"
        ).lastrowid
        other_user = conn.execute("INSERT INTO users (name) VALUES ('other')").lastrowid
        assert owner_user is not None and other_user is not None
        for key, user_id in (
            ("alpha", owner_user),
            ("beta", owner_user),
            ("other", other_user),
        ):
            agent_id = int(
                conn.execute(
                    "INSERT INTO agents (user_id, name) VALUES (?, ?)",
                    (user_id, key),
                ).lastrowid
                or 0
            )
            ids[f"{key}_agent"] = agent_id
            session_id = int(
                conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,)).lastrowid
                or 0
            )
            ids[f"{key}_session"] = session_id
            ids[f"{key}_approval"] = int(
                conn.execute(
                    "INSERT INTO wal (session_id, turn, role, content) "
                    "VALUES (?, 1, 'owner', ?)",
                    (session_id, f"Approve global memory for {key}"),
                ).lastrowid
                or 0
            )
        ids["alpha_local"] = int(
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, 'alpha local fact')",
                (ids["alpha_agent"],),
            ).lastrowid
            or 0
        )
        ids["beta_local"] = int(
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, 'beta local fact')",
                (ids["beta_agent"],),
            ).lastrowid
            or 0
        )
        ids["alpha_global"] = int(
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, 'global shared fact')",
                (ids["alpha_agent"],),
            ).lastrowid
            or 0
        )
        ids["other_global"] = int(
            conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, 'other global fact')",
                (ids["other_agent"],),
            ).lastrowid
            or 0
        )
        conn.commit()
    return path, ids


def test_user_global_promotion_requires_blessed_owner_approval(
    scoped_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = scoped_world
    with connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="Owner approval"):
            promote_user_global(
                conn,
                ids["alpha_global"],
                ids["alpha_approval"],
                "Owner approved sharing this across their agents",
            )

    with connect(path, blessed=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="same user"):
            promote_user_global(
                conn,
                ids["alpha_global"],
                ids["other_approval"],
                "Wrong user's approval must not work",
            )
        promote_user_global(
            conn,
            ids["alpha_global"],
            ids["alpha_approval"],
            "Owner approved sharing this across their agents",
        )
        assert conn.execute(
            "SELECT scope, global_approved_by_wal_id FROM memories WHERE memory_id = ?",
            (ids["alpha_global"],),
        ).fetchone() == ("user_global", ids["alpha_approval"])
        with pytest.raises(sqlite3.IntegrityError, match="never downgraded"):
            conn.execute(
                "UPDATE memories SET scope = 'agent' WHERE memory_id = ?",
                (ids["alpha_global"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE memories SET global_approved_by_wal_id = ? WHERE memory_id = ?",
                (ids["beta_approval"], ids["alpha_global"]),
            )


def test_recall_sees_own_local_and_same_user_global_only(
    scoped_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = scoped_world
    stone = ScopeEmbedder()
    with connect(path, blessed=True) as conn:
        promote_user_global(
            conn,
            ids["alpha_global"],
            ids["alpha_approval"],
            "Owner approved sharing this across their agents",
        )
        promote_user_global(
            conn,
            ids["other_global"],
            ids["other_approval"],
            "Other user approved their own global memory",
        )
        set_embedder(conn, stone)
        conn.commit()

    with connect(path) as conn:
        beta_hits = recall(
            conn, stone, "global alpha beta other", agent_id=ids["beta_agent"], floor=0.0
        )
        beta_ids = {hit.memory_id for hit in beta_hits}
        assert ids["beta_local"] in beta_ids
        assert ids["alpha_global"] in beta_ids
        assert ids["alpha_local"] not in beta_ids
        assert ids["other_global"] not in beta_ids

        hybrid = hybrid_recall(
            conn,
            stone,
            "global alpha beta other",
            agent_id=ids["beta_agent"],
            floor=0.0,
            limit=10,
        )
        hybrid_ids = {hit.row_id for hit in hybrid.hits if hit.kind == "memory"}
        assert ids["beta_local"] in hybrid_ids
        assert ids["alpha_global"] in hybrid_ids
        assert ids["alpha_local"] not in hybrid_ids
        assert ids["other_global"] not in hybrid_ids


def test_scoped_agent_cannot_self_promote_memory_global(
    scoped_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = scoped_world
    scoped = connect_agent(path, ids["alpha_agent"])
    try:
        with pytest.raises(sqlite3.IntegrityError, match="Owner approval"):
            scoped.execute(
                "UPDATE my_memories SET scope = 'user_global', "
                "global_approved_by_wal_id = ? WHERE memory_id = ?",
                (ids["alpha_approval"], ids["alpha_local"]),
            )
    finally:
        scoped.close()


def test_user_global_memory_can_be_durable_governing_belief(
    scoped_world: tuple[Path, dict[str, int]],
) -> None:
    path, ids = scoped_world
    with connect(path, blessed=True) as conn:
        reinforce(conn, ids["alpha_global"], "proved useful")
        promote(conn, ids["alpha_global"], "earned durability")
        promote_user_global(
            conn,
            ids["alpha_global"],
            ids["alpha_approval"],
            "Owner approved sharing this across their agents",
        )
        assert conn.execute(
            "SELECT content FROM memories WHERE scope = 'user_global' AND status = 'durable'"
        ).fetchone() == ("global shared fact",)
