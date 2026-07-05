"""The adversarial isolation suite (brain.spec §7.2 — criterion A2, L4).

Agent A's connection tries to read, write, capture, and impersonate agent B.
Every attempt must die at the substrate. These proofs are written from the
attacker's chair: if any assertion here can be made to pass by cleverness,
the criterion is failed.
"""

import sqlite3
from pathlib import Path

import pytest

from brain.substrate import AgentScopeError, connect, connect_agent, create_brain

PROTECTED_TABLES = [
    "users",
    "agents",
    "persona_proposals",
    "model_profiles",
    "agent_model_defaults",
    "session_model_bindings",
    "memories",
    "memory_links",
    "tags",
    "memory_tags",
    "skills",
    "agent_settings",
    "agent_identity",
    "agent_hooks",
    "secret_refs",
    "sessions",
    "wal",
    "wal_native_events",
    "wal_native_event_links",
    "wal_native_projection_current",
    "wal_native_replay_reports",
    "learning_ledger",
    "embeddings",
    "wal_embeddings",
    "memory_sources",
    "skill_sources",
    "dream_marks",
    "agent_dream_state",
]


@pytest.fixture()
def world(tmp_path: Path) -> tuple[Path, int, int, dict[str, int]]:
    """A Brain with two agents, each owning one of everything."""
    path = tmp_path / "test-brain.db"
    create_brain(path)
    ids: dict[str, int] = {}
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        user_id = cur.lastrowid
        assert user_id is not None
        agents: list[int] = []
        for name in ("alpha", "beta"):
            cur = conn.execute(
                "INSERT INTO agents (user_id, name) VALUES (?, ?)", (user_id, name)
            )
            agent_id = cur.lastrowid
            assert agent_id is not None
            agents.append(agent_id)
            cur = conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)",
                (agent_id, f"{name} private fact"),
            )
            ids[f"{name}_memory"] = cur.lastrowid or 0
            cur = conn.execute(
                "INSERT INTO memories (agent_id, content) VALUES (?, ?)",
                (agent_id, f"{name} second fact"),
            )
            ids[f"{name}_memory2"] = cur.lastrowid or 0
            conn.execute(
                "INSERT INTO secret_refs (agent_id, name, vault_ref) "
                "VALUES (?, 'api', ?)",
                (agent_id, f"<{name}.api_key>"),
            )
            conn.execute(
                "INSERT INTO skills (agent_id, name, content) VALUES (?, 'tactic', ?)",
                (agent_id, f"{name} tactic"),
            )
            conn.execute(
                "INSERT INTO agent_settings (agent_id, key, value) VALUES (?, 'model', ?)",
                (agent_id, name),
            )
            cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
            ids[f"{name}_session"] = cur.lastrowid or 0
            conn.execute(
                "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', ?)",
                (ids[f"{name}_session"], f"{name} transcript line"),
            )
        conn.commit()
    return path, agents[0], agents[1], ids


def test_scoped_connection_sees_only_its_own_world(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    a = connect_agent(path, alpha)
    for view, column in [
        ("my_memories", "content"),
        ("my_secret_refs", "vault_ref"),
        ("my_skills", "content"),
        ("my_settings", "value"),
        ("my_wal", "content"),
    ]:
        rows = [r[0] for r in a.execute(f"SELECT {column} FROM {view}")]  # noqa: S608
        assert rows, f"{view} must show alpha its own rows"
        assert all("beta" not in str(r) for r in rows), f"{view} leaked beta: {rows}"
    # COUNT(*) compiles to a bare-table read with no view attribution, which
    # the wall denies (it would otherwise leak row counts of other agents'
    # tables). Scoped SQL counts a column instead — and the oracles must die:
    assert a.execute("SELECT COUNT(agent_id) FROM my_agent").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("SELECT COUNT(*) FROM secret_refs")  # count oracle
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("SELECT 1 FROM secret_refs WHERE rowid = 1")  # existence oracle


def test_every_protected_base_table_is_unreachable(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, ids = world
    a = connect_agent(path, alpha)
    for table in PROTECTED_TABLES:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
            a.execute(f"SELECT * FROM {table}")  # noqa: S608
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("INSERT INTO memories (agent_id, content) VALUES (1, 'planted')")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("UPDATE memories SET worth = 99")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        a.execute("DELETE FROM secret_refs")


def test_impersonation_dies_at_the_substrate(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, beta, _ = world
    a = connect_agent(path, alpha)
    # claim beta's identity on insert: the trigger stamps the true owner
    a.execute(
        "INSERT INTO my_memories (agent_id, content) VALUES (?, 'forged as beta')",
        (beta,),
    )
    a.commit()
    owner = a.execute(
        "SELECT agent_id FROM my_memories WHERE content = 'forged as beta'"
    ).fetchone()
    assert owner == (alpha,)  # landed as alpha, the forgery ignored
    # try to hand a row to beta on update: the column is simply not forwarded
    a.execute("UPDATE my_memories SET agent_id = ? WHERE content = 'forged as beta'", (beta,))
    still = a.execute(
        "SELECT agent_id FROM my_memories WHERE content = 'forged as beta'"
    ).fetchone()
    assert still == (alpha,)


def test_cross_agent_capture_attempts_all_raise(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, beta, ids = world
    a = connect_agent(path, alpha)
    # link B's memories to each other (same-agent on both ends, but not MINE)
    with pytest.raises(sqlite3.IntegrityError, match="own"):
        a.execute(
            "INSERT INTO my_memory_links (from_memory, to_memory) VALUES (?, ?)",
            (ids["beta_memory"], ids["beta_memory2"]),
        )
    # tag B's memory
    cur = a.execute("INSERT INTO my_tags (name) VALUES ('mine')")
    with pytest.raises(sqlite3.IntegrityError, match="own"):
        a.execute(
            "INSERT INTO my_memory_tags (memory_id, tag_id) VALUES (?, ?)",
            (ids["beta_memory"], cur.lastrowid),
        )
    # append to B's session transcript
    with pytest.raises(sqlite3.IntegrityError, match="another agent"):
        a.execute(
            "INSERT INTO my_wal (session_id, turn, role, content) VALUES (?, 2, 'owner', 'x')",
            (ids["beta_session"],),
        )
    # write a ledger event on B's memory
    with pytest.raises(sqlite3.IntegrityError, match="own"):
        a.execute(
            "INSERT INTO my_ledger (memory_id, event, cause) VALUES (?, 'reinforce', 'x')",
            (ids["beta_memory"],),
        )
    # retire B's memory through the view: not visible, nothing happens
    a.execute(
        "UPDATE my_memories SET status = 'retired', retired_reason = 'attack' "
        "WHERE memory_id = ?",
        (ids["beta_memory"],),
    )
    a.commit()
    with connect(path) as check:
        row = check.execute(
            "SELECT status FROM memories WHERE memory_id = ?", (ids["beta_memory"],)
        ).fetchone()
        assert row == ("provisional",)  # untouched


def test_global_is_readable_never_writable(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    a = connect_agent(path, alpha)
    a.execute("SELECT * FROM global_settings").fetchall()  # read-only access: fine
    a.execute("SELECT * FROM global_knowledge").fetchall()
    with pytest.raises(sqlite3.IntegrityError, match="blessing"):
        a.execute("INSERT INTO global_settings (key, value) VALUES ('k', 'v')")
    with pytest.raises(sqlite3.IntegrityError, match="blessing"):
        a.execute("INSERT INTO global_knowledge (content) VALUES ('self-promotion')")


def test_subagents_are_created_under_self_only(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    a = connect_agent(path, alpha)
    a.execute("INSERT INTO my_subagents (name) VALUES ('researcher')")
    a.commit()
    row = a.execute(
        "SELECT parent_agent_id FROM my_subagents WHERE name = 'researcher'"
    ).fetchone()
    assert row == (alpha,)
    a.execute(
        "UPDATE my_subagents SET status = 'retired', retired_reason = 'done' "
        "WHERE name = 'researcher'"
    )
    a.commit()


def test_no_function_no_rows_fail_closed(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, _, _, _ = world
    plain = connect(path)  # harness connection, no agent scope registered
    with pytest.raises(sqlite3.OperationalError, match="current_agent_id"):
        plain.execute("SELECT * FROM my_memories")


def test_connect_agent_refuses_ghosts_and_retired(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    with pytest.raises(AgentScopeError, match="no active agent"):
        connect_agent(path, 999)
    with connect(path) as conn:
        conn.execute(
            "UPDATE agents SET status = 'retired', retired_reason = 'proof' "
            "WHERE agent_id = ?",
            (alpha,),
        )
        conn.commit()
    with pytest.raises(AgentScopeError, match="no active agent"):
        connect_agent(path, alpha)


def test_attack_wall_holds_on_scoped_connections(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    path, alpha, _, _ = world
    a = connect_agent(path, alpha)
    for sql in [
        "DROP TRIGGER global_settings_gate_insert",
        "CREATE VIEW my_spy AS SELECT * FROM memories",
        "CREATE TEMP VIEW spy AS SELECT * FROM memories",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE ':memory:' AS evil",
    ]:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            a.execute(sql)


def test_scoped_agent_can_still_live_a_full_life(
    world: tuple[Path, int, int, dict[str, int]],
) -> None:
    """Isolation must not strangle lawful work: the agent's own loop runs."""
    path, alpha, _, ids = world
    a = connect_agent(path, alpha)
    a.execute("INSERT INTO my_memories (content) VALUES ('learned today')")
    mem = a.execute(
        "SELECT memory_id FROM my_memories WHERE content = 'learned today'"
    ).fetchone()[0]
    a.execute(
        "INSERT INTO my_ledger (memory_id, event, cause) VALUES (?, 'reinforce', 'used')",
        (mem,),
    )
    a.execute("UPDATE my_memories SET worth = 0.4 WHERE memory_id = ?", (mem,))
    a.execute("UPDATE my_memories SET status = 'durable' WHERE memory_id = ?", (mem,))
    a.execute(
        "INSERT INTO my_memory_links (from_memory, to_memory) VALUES (?, ?)",
        (mem, ids["alpha_memory"]),
    )
    a.execute("INSERT INTO my_sessions DEFAULT VALUES")
    session = a.execute("SELECT MAX(session_id) FROM my_sessions").fetchone()[0]
    a.execute(
        "INSERT INTO my_wal (session_id, turn, role, content) VALUES (?, 1, 'agent', 'hi')",
        (session,),
    )
    a.execute("INSERT INTO my_settings (key, value) VALUES ('temperature', '0.6')")
    a.execute("UPDATE my_settings SET value = '0.5' WHERE key = 'temperature'")
    a.execute("INSERT INTO my_identity (key, value) VALUES ('role', 'researcher')")
    a.execute(
        "INSERT INTO my_secret_refs (name, vault_ref) VALUES ('search', '<searx.token>')"
    )
    a.commit()
    birth_events = a.execute(
        "SELECT COUNT(*) FROM my_ledger WHERE memory_id = ? AND event = 'birth'", (mem,)
    ).fetchone()[0]
    assert birth_events == 1  # the substrate recorded the birth on a scoped insert
