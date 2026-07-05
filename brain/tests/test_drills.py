"""The operational drills (brain.spec §7.3, §7.9 — A3 Brain-side, A9).

The bare-machine drill runs the system interpreter in isolated mode with
nothing but the source tree and one Brain file in an empty directory — the
closest a test can get to "a machine with nothing installed". The same
subprocess doubles as the model-swap drill's "new model": a fresh mind
that must answer identity and project questions from the file alone.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from brain.durability import snapshot
from brain.recall import set_embedder
from brain.substrate import connect, create_brain

SRC = Path(__file__).resolve().parent.parent / "src"
BARE_PYTHON = "/usr/bin/python3"


def test_brain_imports_are_stdlib_only() -> None:
    """A9 static half: the Brain package cannot have grown a dependency."""
    allowed = set(sys.stdlib_module_names) | {"brain"}
    offenders: list[str] = []
    for source_file in sorted((SRC / "brain").glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            offenders.extend(
                f"{source_file.name}: {name}" for name in names if name not in allowed
            )
    assert offenders == [], f"non-stdlib imports in the Brain: {offenders}"


_DRILL_SCRIPT = '''
import hashlib, json, os, sys
sys.path.insert(0, os.environ["BRAIN_SRC"])
from pathlib import Path
from collections.abc import Sequence

from brain.substrate import connect
from brain.recall import recall, search
from brain.learning import reinforce
from brain.dreams import dream_pass1


class ToyEmbedder:  # the harness side carries the stone; ids must match
    def __init__(self, embedder_id: str, salt: int = 0, dim: int = 32) -> None:
        self.embedder_id = embedder_id
        self._salt = salt
        self._dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self._dim
            for tok in text.lower().split():
                d = hashlib.md5(f"{self._salt}:{tok}".encode()).hexdigest()
                v[int(d[:8], 16) % self._dim] += 1.0
            out.append(v)
        return out


class ToyDistiller:
    def distill(self, transcript):
        return [c.split("remember:", 1)[1].strip() for _, c in transcript if "remember:" in c]


report = {}
path = Path(os.environ["BRAIN_FILE"])
report["only_file_in_dir"] = [p.name for p in path.parent.iterdir()] == [path.name]

conn = connect(path)  # the integrity gate runs here; failure = loud crash
report["integrity"] = "pass"

# the new model answers WHO AM I from the Brain alone (identity served live)
row = conn.execute(
    "SELECT value FROM agent_identity WHERE key = 'name'"
).fetchone()
report["identity_name"] = row[0] if row else None

# and answers PROJECT questions from durable memory, both verbs
report["search_hits"] = [h.content for h in search(conn, "foundation parity gate")]
hits = recall(conn, ToyEmbedder("toy-a", salt=1), "foundation parity gate", floor=0.3)
report["recall_hits"] = [h.content for h in hits]

report["durable_count"] = conn.execute(
    "SELECT COUNT(memory_id) FROM memories WHERE status = 'durable'"
).fetchone()[0]

# the machinery runs on the bare machine: a verb, a dream, a secret ref
if hits:
    report["reinforced_worth"] = reinforce(conn, hits[0].memory_id, "used by the new model")
agent_id = conn.execute("SELECT agent_id FROM agents LIMIT 1").fetchone()[0]
cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
conn.execute(
    "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', "
    "'remember: the swap drill ran on a bare machine')",
    (cur.lastrowid,),
)
report["dreamed"] = dream_pass1(conn, ToyDistiller(), agent_id)
report["secret_ref"] = conn.execute(
    "SELECT vault_ref FROM secret_refs LIMIT 1"
).fetchone()[0]
conn.commit()
conn.close()
print(json.dumps(report))
'''


@pytest.fixture()
def populated_brain(tmp_path: Path) -> tuple[Path, int]:
    """A Brain mid-project: identity, durable project knowledge, history."""
    path = tmp_path / "live" / "brain.db"
    path.parent.mkdir()
    create_brain(path)
    with connect(path, blessed=True) as conn:
        cur = conn.execute("INSERT INTO users (name, is_owner) VALUES ('owner', 1)")
        cur = conn.execute(
            "INSERT INTO agents (user_id, name) VALUES (?, 'keeper')", (cur.lastrowid,)
        )
        agent_id = cur.lastrowid
        assert agent_id is not None
        conn.execute(
            "INSERT INTO agent_identity (agent_id, key, value) VALUES (?, 'name', 'Keeper')",
            (agent_id,),
        )
        cur = conn.execute(
            "INSERT INTO memories (agent_id, content) VALUES (?, "
            "'the project builds a foundation that must pass the parity gate')",
            (agent_id,),
        )
        memory_id = cur.lastrowid
        conn.execute(
            "INSERT INTO learning_ledger (memory_id, event, cause) "
            "VALUES (?, 'reinforce', 'proven in use')",
            (memory_id,),
        )
        conn.execute(
            "UPDATE memories SET worth = 0.2, status = 'durable' WHERE memory_id = ?",
            (memory_id,),
        )
        conn.execute(
            "INSERT INTO secret_refs (agent_id, name, vault_ref) "
            "VALUES (?, 'api', '<service.api_key>')",
            (agent_id,),
        )
        cur = conn.execute("INSERT INTO sessions (agent_id) VALUES (?)", (agent_id,))
        conn.execute(
            "INSERT INTO wal (session_id, turn, role, content) VALUES (?, 1, 'owner', "
            "'we are building the foundation')",
            (cur.lastrowid,),
        )
        class _Stone:
            embedder_id = "toy-a"

            def embed(self, texts: Sequence[str]) -> list[list[float]]:
                import hashlib

                out = []
                for text in texts:
                    v = [0.0] * 32
                    for tok in text.lower().split():
                        d = hashlib.md5(f"1:{tok}".encode()).hexdigest()
                        v[int(d[:8], 16) % 32] += 1.0
                    out.append(v)
                return out

        set_embedder(conn, _Stone())
        conn.commit()
        durable = conn.execute(
            "SELECT COUNT(memory_id) FROM memories WHERE status = 'durable'"
        ).fetchone()[0]
    return path, int(durable)


def test_bare_machine_and_model_swap_drill(
    populated_brain: tuple[Path, int], tmp_path: Path
) -> None:
    path, durable_before = populated_brain
    # the file travels ALONE into an empty directory
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    snap = snapshot(path, dest_dir=tmp_path / "snaps")
    brain_file = bare_dir / "brain.db"
    snap.rename(brain_file)
    script = tmp_path / "drill.py"
    script.write_text(_DRILL_SCRIPT, encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [BARE_PYTHON, "-I", str(script)],
        env={"BRAIN_SRC": str(SRC), "BRAIN_FILE": str(brain_file)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"bare-machine drill died:\n{result.stderr}"
    report = json.loads(result.stdout)
    # A9: nothing but the file and the stdlib, and everything ran
    assert report["only_file_in_dir"] is True
    assert report["integrity"] == "pass"
    assert report["dreamed"] == 1
    assert report["secret_ref"] == "<service.api_key>"
    # A3 Brain-side: the fresh mind answered from the file alone
    assert report["identity_name"] == "Keeper"
    assert any("parity gate" in hit for hit in report["search_hits"])
    assert any("parity gate" in hit for hit in report["recall_hits"])
    assert report["durable_count"] == durable_before  # zero durable rows lost
    assert report["reinforced_worth"] == pytest.approx(0.3)
