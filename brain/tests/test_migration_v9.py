"""Schema v8 to v9 migration is explicit, fingerprinted, and snapshotted."""

import sqlite3
from pathlib import Path

from brain import substrate
from brain.substrate import (
    connect,
    create_brain,
    migrate_v8_to_v9,
    migrate_v9_to_v10,
    migrate_v10_to_v11,
    migrate_v11_to_v12,
    migrate_v12_to_v13,
    migrate_v13_to_v14,
    migrate_v14_to_v15,
)


def test_v8_migration_requires_and_preserves_snapshot(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v8.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP TRIGGER memory_sources_same_agent")
        conn.execute("DROP TRIGGER memory_sources_no_update")
        conn.execute("DROP TRIGGER memory_sources_no_delete")
        conn.execute("DROP TABLE memory_sources")
        conn.execute("DROP TABLE wal_embeddings")
        conn.execute(
            "UPDATE brain_meta SET value = '8' WHERE key = 'schema_version'"
        )
        conn.commit()

    raw = sqlite3.connect(path)
    old_fingerprint = substrate._fingerprint(raw)
    raw.close()
    monkeypatch.setattr(substrate, "V8_FINGERPRINT", old_fingerprint)  # type: ignore[attr-defined]

    migrate_v8_to_v9(path, snapshot)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "9"
        assert raw.execute(
            "SELECT COUNT(*) FROM wal_embeddings"
        ).fetchone()[0] == 0
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "8"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'wal_embeddings'"
        ).fetchone() is None
    finally:
        old.close()


def test_v9_to_current_migration_reaches_canonical_schema_with_snapshot(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "brain.db"
    v8_snapshot = tmp_path / "brain-v8.snapshot.db"
    v9_snapshot = tmp_path / "brain-v9.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP TRIGGER memory_sources_same_agent")
        conn.execute("DROP TRIGGER memory_sources_no_update")
        conn.execute("DROP TRIGGER memory_sources_no_delete")
        conn.execute("DROP TABLE memory_sources")
        conn.execute("DROP TABLE wal_embeddings")
        conn.execute("DROP TRIGGER memories_user_global_insert_gate")
        conn.execute("DROP TRIGGER memories_user_global_update_gate")
        conn.execute("DROP TRIGGER memories_user_global_no_downgrade")
        conn.execute("DROP TRIGGER memories_user_global_approval_immutable")
        conn.execute("DROP TRIGGER memories_user_global_approval_insert")
        conn.execute("DROP TRIGGER memories_user_global_approval_update")
        conn.execute("UPDATE brain_meta SET value = '8' WHERE key = 'schema_version'")
        conn.commit()
    raw = sqlite3.connect(path)
    try:
        old_fingerprint = substrate._fingerprint(raw)
    finally:
        raw.close()
    monkeypatch.setattr(substrate, "V8_FINGERPRINT", old_fingerprint)  # type: ignore[attr-defined]

    migrate_v8_to_v9(path, v8_snapshot)
    migrate_v9_to_v10(path, v9_snapshot)
    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        columns = {
            row[1]
            for row in raw.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert {"scope", "global_approved_by_wal_id"}.issubset(columns)
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'memory_candidates'"
        ).fetchone() == (1,)
    finally:
        raw.close()

    old = sqlite3.connect(v9_snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "9"
    finally:
        old.close()


def test_v10_to_v11_migration_adds_candidate_queue_with_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v10.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP TRIGGER memory_candidate_sources_same_agent")
        conn.execute("DROP TRIGGER memory_candidate_sources_no_update")
        conn.execute("DROP TRIGGER memory_candidate_sources_no_delete")
        conn.execute("DROP TABLE memory_candidate_sources")
        conn.execute("DROP TRIGGER memory_candidates_agent_must_be_active")
        conn.execute("DROP TRIGGER memory_candidates_identity_immutable")
        conn.execute("DROP TRIGGER memory_candidates_no_delete")
        conn.execute("DROP TRIGGER memory_candidates_no_reopen")
        conn.execute("DROP TRIGGER memory_candidates_accepted_same_agent")
        conn.execute("DROP TABLE memory_candidates")
        conn.execute("UPDATE brain_meta SET value = '10' WHERE key = 'schema_version'")
        conn.commit()

    migrate_v10_to_v11(path, snapshot)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        assert raw.execute(
            "SELECT COUNT(*) FROM memory_candidates"
        ).fetchone()[0] == 0
        assert raw.execute(
            "SELECT COUNT(*) FROM memory_candidate_sources"
        ).fetchone()[0] == 0
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "10"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'memory_candidates'"
        ).fetchone() is None
    finally:
        old.close()


def test_v11_to_v12_migration_adds_skill_sources_with_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v11.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP TRIGGER my_skill_sources_insert")
        conn.execute("DROP VIEW my_skill_sources")
        conn.execute("DROP TRIGGER skill_sources_same_agent")
        conn.execute("DROP TRIGGER skill_sources_no_update")
        conn.execute("DROP TRIGGER skill_sources_no_delete")
        conn.execute("DROP TABLE skill_sources")
        conn.execute("UPDATE brain_meta SET value = '11' WHERE key = 'schema_version'")
        conn.commit()

    migrate_v11_to_v12(path, snapshot)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        assert raw.execute(
            "SELECT COUNT(*) FROM skill_sources"
        ).fetchone()[0] == 0
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'my_skill_sources'"
        ).fetchone() == (1,)
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "11"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'skill_sources'"
        ).fetchone() is None
    finally:
        old.close()


def test_v12_to_v13_migration_adds_wal_native_schema_with_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v12.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP TRIGGER wal_native_events_no_update")
        conn.execute("DROP TRIGGER wal_native_events_no_delete")
        conn.execute("DROP TRIGGER wal_native_event_links_no_update")
        conn.execute("DROP TRIGGER wal_native_event_links_no_delete")
        conn.execute("DROP TRIGGER wal_native_replay_reports_no_update")
        conn.execute("DROP TRIGGER wal_native_replay_reports_no_delete")
        conn.execute("DROP TABLE wal_native_replay_reports")
        conn.execute("DROP TABLE wal_native_projection_current")
        conn.execute("DROP TABLE wal_native_event_links")
        conn.execute("DROP TABLE wal_native_events")
        conn.execute("UPDATE brain_meta SET value = '12' WHERE key = 'schema_version'")
        conn.commit()

    try:
        connect(path)
    except substrate.BrainIntegrityError:
        pass
    else:  # pragma: no cover - defensive clarity for this fail-closed proof
        raise AssertionError("v12 file should be refused by v13 substrate")

    migrate_v12_to_v13(path, snapshot)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        for table in (
            "wal_native_events",
            "wal_native_event_links",
            "wal_native_projection_current",
            "wal_native_replay_reports",
        ):
            assert raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone() == (1,)
            assert raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "12"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'wal_native_events'"
        ).fetchone() is None
    finally:
        old.close()


def test_v13_to_v14_migration_adds_model_profiles_with_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v13.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP VIEW my_session_model_current")
        conn.execute("DROP VIEW session_model_current")
        conn.execute("DROP TRIGGER session_model_bindings_profile_active")
        conn.execute("DROP TRIGGER session_model_bindings_slot_match")
        conn.execute("DROP TRIGGER session_model_bindings_no_delete")
        conn.execute("DROP TRIGGER session_model_bindings_no_update")
        conn.execute("DROP TRIGGER agent_model_defaults_slot_match_update")
        conn.execute("DROP TRIGGER agent_model_defaults_slot_match")
        conn.execute("DROP TRIGGER agent_model_defaults_no_delete")
        conn.execute("DROP TRIGGER agent_model_defaults_gate_update")
        conn.execute("DROP TRIGGER agent_model_defaults_gate_insert")
        conn.execute("DROP TRIGGER model_profiles_no_delete")
        conn.execute("DROP TRIGGER model_profiles_gate_update")
        conn.execute("DROP TRIGGER model_profiles_gate_insert")
        conn.execute("DROP TABLE session_model_bindings")
        conn.execute("DROP TABLE agent_model_defaults")
        conn.execute("DROP TABLE model_profiles")
        conn.execute("UPDATE brain_meta SET value = '13' WHERE key = 'schema_version'")
        conn.commit()

    try:
        connect(path)
    except substrate.BrainIntegrityError:
        pass
    else:  # pragma: no cover
        raise AssertionError("v13 file should be refused by v14 substrate")

    migrate_v13_to_v14(path, snapshot)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        for table in (
            "model_profiles",
            "agent_model_defaults",
            "session_model_bindings",
        ):
            assert raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone() == (1,)
            assert raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'my_session_model_current'"
        ).fetchone() == (1,)
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "13"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'model_profiles'"
        ).fetchone() is None
    finally:
        old.close()


def test_v14_to_v15_migration_adds_persona_proposals_with_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "brain.db"
    snapshot = tmp_path / "brain-v14.snapshot.db"
    create_brain(path)
    with connect(path, blessed=True) as conn:
        conn.execute("DROP VIEW my_persona_proposals")
        conn.execute("DROP TRIGGER persona_proposals_review_wal_same_user")
        conn.execute("DROP TRIGGER persona_proposals_materialized_same_scope")
        conn.execute("DROP TRIGGER persona_proposals_no_reopen")
        conn.execute("DROP TRIGGER persona_proposals_no_delete")
        conn.execute("DROP TRIGGER persona_proposals_identity_immutable")
        conn.execute("DROP TRIGGER persona_proposals_proposer_same_user")
        conn.execute("DROP TRIGGER persona_proposals_parent_same_user")
        conn.execute("DROP TABLE persona_proposals")
        conn.execute("UPDATE brain_meta SET value = '14' WHERE key = 'schema_version'")
        conn.commit()

    try:
        connect(path)
    except substrate.BrainIntegrityError:
        pass
    else:  # pragma: no cover
        raise AssertionError("v14 file should be refused by v15 substrate")

    migrate_v14_to_v15(path, snapshot)

    raw = sqlite3.connect(path)
    try:
        assert raw.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == substrate.SCHEMA_VERSION
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'persona_proposals'"
        ).fetchone() == (1,)
        assert raw.execute("SELECT COUNT(*) FROM persona_proposals").fetchone()[0] == 0
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'my_persona_proposals'"
        ).fetchone() == (1,)
    finally:
        raw.close()

    old = sqlite3.connect(snapshot)
    try:
        assert old.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "14"
        assert old.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'persona_proposals'"
        ).fetchone() is None
    finally:
        old.close()
