"""Fail-loud proofs for snapshot/restore (criterion A8 first half,
operation.spec §4: snapshot before ANY change, restore never silent).
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from brain.durability import DurabilityError, restore, snapshot, verify_twin
from brain.learning import reinforce
from brain.recall import search
from brain.substrate import BrainIntegrityError, connect, create_brain


@pytest.fixture()
def brain_path(tmp_path: Path) -> Path:
    path = tmp_path / "live" / "brain.db"
    path.parent.mkdir()
    create_brain(path)
    with connect(path) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'keeper')", (cur.lastrowid,)
        )
        agent_id = cur.lastrowid
        conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, 'the drill fact')",
            (agent_id,),
        )
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) "
            "VALUES (?, 1, 'owner', 'before the snapshot')",
            (cur.lastrowid,),
        )
        conn.commit()
    return path


def test_snapshot_leaves_original_working(brain_path: Path) -> None:
    snap = snapshot(brain_path)
    assert snap.is_file() and snap != brain_path
    assert snap.parent.name == "snapshots"
    with connect(brain_path) as conn:  # original: still passes integrity, still answers
        assert len(search(conn, "drill")) == 1
    with connect(snap) as conn:  # snapshot: a working Brain in its own right
        assert len(search(conn, "drill")) == 1


def test_snapshot_during_open_transaction_holds_committed_truth_only(
    brain_path: Path,
) -> None:
    writer = connect(brain_path)
    writer.execute(
        "INSERT INTO memories (agent_id, content) VALUES (1, 'uncommitted whisper')"
    )  # no commit yet
    snap = snapshot(brain_path)
    writer.commit()  # the open transaction still lands fine afterward
    writer.close()
    with connect(snap) as conn:
        assert search(conn, "whisper") == []  # the snapshot never saw it
    with connect(brain_path) as conn:
        assert len(search(conn, "whisper")) == 1  # the original kept it


def test_the_full_restore_drill(brain_path: Path, tmp_path: Path) -> None:
    snap = snapshot(brain_path)
    with connect(brain_path) as conn:  # mutate the original after the snapshot
        conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (1, 'post-snapshot drift')"
        )
        conn.commit()
    restored_path = tmp_path / "restored.db"
    restore(snap, restored_path)
    verify_twin(snap, restored_path)  # logically identical to the snapshot
    with pytest.raises(DurabilityError):
        verify_twin(restored_path, brain_path)  # and provably NOT the drifted original
    with connect(restored_path) as conn:  # the restored Brain lives: recall and learning run
        hits = search(conn, "drill")
        assert len(hits) == 1
        assert reinforce(conn, hits[0].row_id, "used after restore") > 0


def test_quiesced_twin_snapshots_match(brain_path: Path) -> None:
    snap1 = snapshot(brain_path)
    snap2 = snapshot(brain_path)
    assert snap1 != snap2  # collision-suffixed, never overwritten
    verify_twin(snap1, snap2)
    bytes1 = hashlib.sha256(snap1.read_bytes()).hexdigest()
    bytes2 = hashlib.sha256(snap2.read_bytes()).hexdigest()
    assert bytes1 == bytes2  # byte-faithful where physics allows: same quiesced source


def test_restore_is_never_silent(brain_path: Path, tmp_path: Path) -> None:
    snap = snapshot(brain_path)
    target = tmp_path / "occupied.db"
    target.write_text("something already lives here")
    with pytest.raises(FileExistsError):
        restore(snap, target)
    assert target.read_text() == "something already lives here"  # untouched


def test_snapshot_refuses_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        snapshot(tmp_path / "ghost.db")


def test_no_partial_debris(brain_path: Path) -> None:
    snapshot(brain_path)
    snapdir = brain_path.parent / "snapshots"
    assert not list(snapdir.glob("*.partial"))


def test_tampered_snapshot_fails_restore_and_leaves_nothing(
    brain_path: Path, tmp_path: Path
) -> None:
    snap = snapshot(brain_path)
    raw = sqlite3.connect(snap)
    raw.execute("DROP TRIGGER global_settings_gate_insert")
    raw.commit()
    raw.close()
    target = tmp_path / "from-tampered.db"
    with pytest.raises(BrainIntegrityError):
        restore(snap, target)
    assert not target.exists()  # no debris posing as a Brain
