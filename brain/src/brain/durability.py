"""Snapshot and restore (operation.spec §4, criterion A8 first half).

The Brain is snapshotted before ANY change to it — no exceptions, no
"small" changes. Snapshots are consistent copies of a live file (SQLite
backup API: open connections are fine, uncommitted work is invisible).
Restore is explicit and never silent: it refuses to overwrite, verifies
the restored file through the substrate's integrity gate, and removes its
own debris on failure.
"""

import hashlib
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from brain.substrate import connect


class DurabilityError(RuntimeError):
    """Two Brains that were claimed twins are not, or a snapshot failed."""


def snapshot(path: Path, dest_dir: Path | None = None) -> Path:
    """Write a consistent snapshot of the Brain at `path`.

    Defaults to a `snapshots/` directory beside the Brain. The copy is
    staged as `.partial` and atomically renamed — a crash mid-copy leaves
    no half-snapshot posing as real. Returns the snapshot path.
    """
    source = connect(path)  # integrity-verified: only healthy Brains are snapshotted
    try:
        snapdir = dest_dir if dest_dir is not None else path.parent / "snapshots"
        snapdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        n = 0
        while True:
            suffix = f"-{n}" if n else ""
            target = snapdir / f"{path.stem}-{stamp}{suffix}.db"
            if not target.exists():
                break
            n += 1
        partial = target.with_suffix(".partial")
        dest = sqlite3.connect(partial)
        try:
            source.backup(dest)
        finally:
            dest.close()
        partial.replace(target)
        return target
    finally:
        source.close()


def restore(snapshot_path: Path, target_path: Path) -> None:
    """Restore a snapshot to a NEW path. Refuses an existing target —
    replacing a live Brain is the Owner's explicit act, never this
    function's side effect. The restored file must pass the substrate's
    integrity gate or it is removed and the error raised."""
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"no snapshot at {snapshot_path}")
    if target_path.exists():
        raise FileExistsError(f"restore target already exists: {target_path}")
    shutil.copy2(snapshot_path, target_path)
    try:
        connect(target_path).close()  # fingerprint + version gate
    except BaseException:
        target_path.unlink(missing_ok=True)
        raise


def _logical_hash(conn: sqlite3.Connection) -> str:
    """Hash the full content of every real table (FTS shadow tables
    included; virtual-table wrappers excluded — their truth lives in the
    shadows). Same hash = logically identical Brain."""
    digest = hashlib.sha256()
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND COALESCE(sql, '') NOT LIKE 'CREATE VIRTUAL%' ORDER BY name"
        )
    ]
    for table in tables:
        digest.update(table.encode("utf-8"))
        # no ORDER BY rowid: some shadow tables are WITHOUT ROWID — sort the
        # rendered rows instead, deterministic for any table shape.
        rows = sorted(repr(row) for row in conn.execute(f"SELECT * FROM {table}"))  # noqa: S608
        for row_text in rows:
            digest.update(row_text.encode("utf-8"))
    return digest.hexdigest()


def verify_twin(a: Path, b: Path) -> None:
    """Prove two Brain files are logically identical; raise loudly if not."""
    conn_a = sqlite3.connect(f"file:{a}?mode=ro", uri=True)
    conn_b = sqlite3.connect(f"file:{b}?mode=ro", uri=True)
    try:
        hash_a = _logical_hash(conn_a)
        hash_b = _logical_hash(conn_b)
    finally:
        conn_a.close()
        conn_b.close()
    if hash_a != hash_b:
        raise DurabilityError(
            f"not twins: {a} and {b} differ in content "
            f"({hash_a[:16]}… vs {hash_b[:16]}…)"
        )
