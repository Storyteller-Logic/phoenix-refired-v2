# BRAIN — Progress Ledger

Working ledger for `/mnt/hdd/truth/brain.spec.md` (BLESSED — the spec is locked;
this file is where the builder marks WHAT was completed and HOW it was proven,
per operation.spec §3). An unmarked item is unproven work, whatever the code
looks like. Never mark an item without showing the proof that ran.

Build target: one SQLite file, zero services. Material: Python 3.12+ / uv,
pinned lockfile, strict type-checking. All proofs run on throwaway copies;
the live Brain (once it exists) is snapshotted before ANY change.

---

## Requirements (from brain.spec §1–§6)

| # | Requirement | Status | Proven how |
|---|---|---|---|
| R1 | One single database file; embedded, zero services (§1) | PARTIAL — believed complete, Wyrm chain to judge | One file, stdlib only (iter 1); the file-survives-mind-survives property proven by the bare-machine drill (iter 11, see A9/A3): the file traveled alone to an empty dir and a fresh process got everything from it. |
| R2 | Global base layer: settings/knowledge/hooks/skills, read-only to agents, writes only through the blessing gate — connection-local, fails closed, dies with the process (§2.1) | PARTIAL | Gate mechanism PROVEN 2026-06-10: triggers call connection-registered `owner_blessing()`; raw connection (no registration) hard-errors = fail closed; unblessed ABORTs; blessed writes; new connection to same file denied = dies with process. Proofs: 4 `test_blessing_*` tests. ALL global tables now exist and gated (settings, knowledge, hooks, skills — `test_global_tables_refuse_unblessed_writes` / `accept_blessed_writes`). "Read-only to agents" (read-isolation) still owed = A2. |
| R3 | Agent sub-systems: settings, identity (served live from the Brain), memories, hooks, skills, secret references, sessions (§2.2) | PARTIAL | ALL seven ownership tables now exist and proven 2026-06-10: settings/identity/hooks (`test_agent_config_tables_work_and_enforce_fks` — identity rows updatable live, FKs enforced), memories (iter 3), skills (4 proofs), secret_refs (placeholder shape), sessions (iter 1). Remaining for full R3: read-isolation between agents (A2). |
| R4 | Substrate-enforced isolation: cross-agent read/write, self-promotion to global, identity theft IMPOSSIBLE, not forbidden (§2.2, L4) | PARTIAL — substrate work believed complete, Wyrm chain to judge | Write walls (iter 1/3) + full read/write isolation via scoped connections (iter 5, see A2). User-level isolation note: agent connections are the only scoped surface; users isolate transitively because every connection is per-agent and agents belong to one user. A user-scoped surface (if the walk needs one) would be a harness-layer addition. |
| R5 | Sessions with append-only, full-text-searchable WAL; never update/delete/compact (§2.3, L6) | PARTIAL — substrate work believed complete, Wyrm chain to judge | Append-only PROVEN (iter 1). Full-text-searchable PROVEN (iter 6): `wal_fts` FTS5 mirror, `search()` spans WAL + memories, self-syncing. |
| R6 | Learning ledger: every memory event recorded with cause — auditable belief (§2.4) | PARTIAL — believed complete except 'recall' events, Wyrm chain to judge | Ledger table (iter 3): append-only, seven lawful events, cause required, mechanical birth rows. Verbs (iter 7): every reinforce/contradict/promote/retire/supersede writes its event with the caller's cause, atomically with the data change. "Why does it believe this, how strongly, since when" is answerable: worth + ordered ledger trail proven in tests. Remaining: 'recall' events recorded at recall time (waits for recall()). |
| R7 | Agents/sub-agents: Owner creates top-level (users create in their space), agents craft sub-agents scoped entirely within the parent; retired with reason, never deleted (§2.5) | — | |
| R8 | User layer: Global → User → Agent → Sub-agent; system naming = **Owner**; users substrate-isolated from each other; every agent has exactly one owning user (§2.6, Amendment 1) | PARTIAL | All four layers in schema; every agent `REFERENCES users NOT NULL`; exactly one owner enforced by partial unique index (`test_exactly_one_owner`); naming proven — DDL contains "owner", never the local name (`test_schema_uses_owner_naming_never_local_name`); retire-never-delete on users/agents/sessions (`test_retire_never_delete`). Cross-user read isolation not yet built. |
| R9 | Learning Law: provisional birth, reinforce/contradict/promote/retire, no time decay, net-signal reweighting, failures first-class (§3) | PARTIAL — verbs + substrate believed complete, Wyrm chain to judge | Substrate half (iter 3, 13 proofs). Behavioral half PROVEN 2026-06-10 (`tests/test_learning.py`, 11 proofs): symmetric ±0.1 steps so balanced signal is an exact wash; promote refuses unearned merit (zero reinforce events) and non-provisional rows; 2-net contradictions auto-retire with reason + ledger trail while 1 does not and reinforcement buys resilience; deliberate retire links successor with retire+supersede events; verbs refuse ghosts/retired loudly (`LearningError`); failures use identical machinery and resurface via search; verb data change + ledger event are transaction-atomic (rollback proof). Nothing reads the clock. |
| R10 | Two recall verbs: `search()` keyword FTS over memories+WAL; `recall()` semantic, worth-sorted, relevance floor; retrieve generously (§4) | PARTIAL — both verbs built; real-stone bench (A1) outstanding | `search()` PROVEN iter 6. `recall()` PROVEN 2026-06-10 (`tests/test_recall.py`, 11 proofs): cosine over the active stone's vectors, relevance floor SELECTS then earned worth RANKS — a 5×-reinforced irrelevant memory does not surface while a once-reinforced relevant one ranks first; agent filter; retired never surface; limit 10 (recall@10); every hit writes a 'recall' ledger event with the query as cause. Floor calibration measured for the toy space (0.3 in a clean gap); 0.5 default = real-stone starting value. |
| R11 | Content canonical, vectors derived; embedder is a swappable slot; swap → automatic full re-embed, never mixed vectors (§4) | PARTIAL — slot mechanics believed complete, Wyrm chain to judge | PROVEN 2026-06-10: `embeddings` keyed (memory_id, embedder_id); active stone = blessed `set_embedder()` (unblessed dies at the gate) which auto-re-embeds every non-retired memory from content in the same call; `recall()` refuses any stone but the active one (never mixed, by refusal not policy); late memories self-heal at next recall; scoped connections cannot read vectors. The real e5 stone + Owner bench = harness time. |
| R12 | Ingestion: every turn, code change, fetch, search enters provisional (§5.1) | — | |
| R13 | Skills with use/success records; form autonomously, surface by weight, demote on failure (§5.2) | PARTIAL | Substrate PROVEN 2026-06-10: per-agent skills with use/success counters (CHECK: non-negative, success ≤ use), content immutable (record describes it; supersede same-agent only), retire-with-reason never delete, retired agents take no new skills (4 proofs in `test_agent_subsystems.py`). Autonomous formation + surfacing-by-weight = behavior layer, not yet built. |
| R14 | Dreams: silent background; constant ledger always on; Pass 1 every N turns; full dream housekeeping; requestable by agent and Owner; markers forward-only and idempotent (§5.3) | PARTIAL — Brain-side believed complete, Wyrm chain to judge | PROVEN 2026-06-10 (`tests/test_dreams.py`, 10 proofs): Pass 1 distills new WAL rows only into provisional agent-owned memories (births mechanical), idempotent + incremental + silent on empty; full dream = ledger-canonical reconciliation (repairs worth drift from view-written events), net +2 promotes / net −2 retires (mirror of the verbs' rule) with ledger events, idempotent; markers per-session + per-agent, forward-only and undeletable at the substrate, scoped-blackout; cadence computed from durable state only (defaults 5/15, per-agent agent_settings overrides proven). Constant ledger = the verbs (iter 7), always on. Harness side remaining: background scheduling/threading + the model-backed Distiller stone; "requestable" = harness exposing these calls. |
| R15 | Secrets: Vaultwarden custody, Brain stores references only, fetched at use in local scope, substituted before WAL write, outage = loud blocking failure (§6) | PARTIAL | Substrate half PROVEN 2026-06-10: `secret_refs.vault_ref` physically accepts only `<service.type>` placeholder shape — raw-secret-shaped strings abort at CHECK (`test_secret_refs_accept_placeholders_only`). Honest limit noted: shape enforcement can't prove a string isn't a secret, only that the reference convention is forced. Vaultwarden fetch/substitution/loud-failure = harness behavior, not yet built. |

## Acceptance criteria (brain.spec §7 — "functional" = ALL pass)

| # | Criterion | Status | Proven how |
|---|---|---|---|
| A1 | Recall bench: Owner's question set 15/15 in recall@10 | — | |
| A2 | Isolation: adversarial suite cannot make agent A read/write/capture/impersonate agent B, nor write global without blessing — zero exceptions | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | PROVEN 2026-06-10 (`tests/test_isolation.py`, 10 adversarial proofs): scoped `connect_agent()` sees only own rows through 14 `my_*` views (B's rows present and invisible); ALL 14 base tables unreachable top-level (authorizer denies read+write); impersonation dies (forged agent_id lands as self, update can't move rows); capture dies (links/tags on B's memories, WAL into B's session, ledger on B's memory all RAISE; retiring B's memory via view touches nothing); global readable never writable; sub-agents land under self with own user; no current_agent_id() = error never rows; ghost/retired agents refused (`AgentScopeError`); count+existence oracles denied (COUNT(*)/rowid probes); attack wall holds scoped. Caveat for Wyrm: COUNT(*) on my_* views denied by design — use COUNT(col). |
| A3 | Model-swap drill: swap inference model mid-project; zero durable rows lost; new model answers identity+project from the Brain alone | PARTIAL — Brain-side proven; gauntlet re-run owed | Brain-side PROVEN 2026-06-10 (`test_bare_machine_and_model_swap_drill`): a fresh process with zero shared state (the "new model") opened the Brain file ALONE in an empty directory and answered identity ("Keeper", from agent_identity served live) and project questions (both verbs found "parity gate" knowledge) purely from the file; durable count before == after. Final A3 = real LLM swapped mid-project in the gauntlet (harness time). |
| A4 | Provenance: global/blessed nodes provably immutable | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | PROVEN 2026-06-10: `global_knowledge` UPDATE/DELETE abort for EVERYONE — blessed included (`test_global_knowledge_is_immutable_even_blessed`); correction is a blessed superseding INSERT. The blessing opens the door for INSERT; nothing opens UPDATE/DELETE. |
| A5 | Blessing gate: fail-closed proof — unblessed connection cannot write global, even under deliberate attack | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | Core fail-closed proven 3 ways + privilege dies with connection (see R2). Deliberate-attack wall PROVEN 2026-06-10 (`tests/test_attack_wall.py`, 7 proofs): unblessed connections DENIED all DDL/PRAGMA/ATTACH/ANALYZE via authorizer (11 attacks tried, gate stands after); blessed migration path intact; schema fingerprint + version verified at every `connect()` against expectation held in code — raw-file tampering, foreign files, wrong versions all REFUSED with `BrainIntegrityError`. Residual stated honestly: a direct file-writer is DETECTED at next open, not prevented — physically impossible to prevent in any embedded zero-service file (the price of L1/A9); same tripwire pattern the truth itself uses (L12). |
| A6 | WAL integrity: update/delete impossible at schema level; FTS mirror cannot drift | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | Schema half proven (iter 1). Mirror half PROVEN 2026-06-10: mirrors maintained only by `sync_fts()` behind a forward-only, undeletable, in-file marker; index rows + marker move in one transaction (rollback proof); double-run = no double-post; injected phantom caught by `check_fts()` and repaired by `rebuild_fts()`; scoped connections get a total FTS blackout (shadow tables are raw cross-agent content). Honest residual: harness connections CAN write the index directly (sync needs to) — drift there is detectable-not-preventable, like the raw-file case in A5. |
| A7 | Learning works: reinforced outranks unreinforced twin; contradicted falls; failure-lesson resurfaces in context | PARTIAL — believed complete at slot level, Wyrm chain to judge | Worth arithmetic (iter 7) + ranking clause CLOSED at recall level (iter 9): `test_recall_floor_then_worth` proves the reinforced memory outranks within the floor while high-worth-irrelevant noise is drowned. Failure-lesson resurfacing proven via search (iter 7). Re-confirm with real stone at harness time. |
| A8 | Durability: backup/restore byte-faithful; crash mid-dream → no duplicates, no loss | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | Backup/restore half PROVEN 2026-06-10 (`tests/test_durability.py`, 8 proofs): live-snapshot via backup API (open uncommitted transaction invisible to copy, still commits after); full drill — snapshot→mutate→restore→`verify_twin` logical-hash identical to snapshot, provably ≠ drifted original, restored Brain answers search and runs learning verbs; **byte-faithful proven literally**: twin snapshots of a quiesced source are sha256-identical; restore refuses existing targets, removes debris on tampered-snapshot failure; no `.partial` residue; snapshots integrity-gated both directions. Crash-mid-dream half PROVEN iter 10: simulated crash (rollback) mid-Pass-1 reverts memories AND markers together — zero rows, marker unmoved; re-run does the work exactly once; second run zero (`test_crash_mid_pass1_no_duplicates_no_loss`). Full dream same single-transaction property, idempotent by proof. |
| A9 | Zero services: works with nothing installed but harness + embedded deps (Vaultwarden the single blessed exception, secrets only) | ✅ GREEN — WYRM-CONVERGED 2026-06-10 | PROVEN 2026-06-10 two ways: (1) static AST audit — every import in src/brain/ is stdlib or brain itself, zero exceptions (`test_brain_imports_are_stdlib_only`; pyproject `dependencies = []`); (2) bare-machine drill — `/usr/bin/python3 -I` (no venv, no site, no env), one Brain file alone in an empty dir: integrity gate, search, recall, reinforce, dream pass, secret-reference read all ran. No service of any kind exists to depend on; Vaultwarden-offline is the Brain's permanent condition (references only, never a call). |
| A10 | Embedder-swap drill: swap embedder → auto re-embed from content → recall bench still passes, zero knowledge lost | PARTIAL | Drill MECHANICS PROVEN 2026-06-10 (`test_stone_swap_drill`): stone A answering → `set_embedder(B)` → automatic full re-embed (count verified) → recall under B returns the equivalent set → memory count unchanged (zero knowledge lost) → stone A refused afterward. Final A10 = re-run of this drill with real stones at harness time. |

**Component DONE = all 10 green AND the Wyrm chain has converged on it
(operation.spec §2.1): builder's own adversarial pass → gemma-opus → qwen,
findings verified never assumed, fresh unsteered re-runs after changes.**

---

## Build log (newest first — WHAT was done, HOW it was proven)

### 2026-06-10 — Iteration 16: WYRM CHAIN LINK 5 — CONVERGED (operation.spec §2.1)

**THE CONFIRMING PASS:** both models reviewed the v8 code again, fresh and
unsteered. Gemma 7 findings, Qwen ~7 distinct (looped past 7 to the cap).
Raw: `reviews/confirm-{gemma-opus,qwen}-link5.md`; verdict
`reviews/CONVERGED-link5.md`.

**VERIFIED, NEVER ASSUMED:** every distinct claim probed against live code —
all refuted or non-defects (full table in CONVERGED-link5.md). Notable: a
"recall writes events for sub-floor hits" claim initially tripped a crude
string test (the docstring fooled it); reading the actual control flow
confirmed floor→sort→limit→ledger order — REFUTED. create_brain overwrite,
SQL-CREATE-FUNCTION, retired-agent connection, user_id forgery — all refuted
by direct probe.

**RESULT: CONVERGED.** No new real defect. The Wyrm chain over the Brain
substrate is complete: builder pass (4 fixed) → Gemma (1 hardening) → Qwen
(1 hardening) → fresh re-run (1 fixed) → confirming re-run (0 new). ~70 raw
findings across 5 links; not one high-severity claim survived a probe.

**LEDGER:** the substrate-provable acceptance criteria marked GREEN /
WYRM-CONVERGED — A2, A4, A5, A6, A8, A9. A1/A3/A7/A10 remain Brain-side
proven with harness-time confirmation owed (they need the real embedding/
inference stones and the Owner's ingested answers). The Brain is as complete
as it can be before the Harness exists. **117/117 GREEN.**

**NEXT (blessed build order, operation.spec §5):** the Harness, at
`/mnt/hdd/harness/` per `harness.spec.md`.

### 2026-06-10 — Iteration 15: WYRM CHAIN LINK 4 — the fresh re-run (operation.spec §2.1)

**THE RE-RUN:** both models reviewed the CHANGED code again, fresh and
unsteered (no memory, no pointers to past defects — the §2.1 requirement).
Raw outputs in `reviews/rerun-{gemma,qwen}-link4.md`; full verified verdict
in `reviews/VERDICT-link4.md`. Gemma reported 4, Qwen 14 (no repetition loop
this time).

**VERIFIED, NEVER ASSUMED:** every load-bearing claim probed against live
code. ALL high-severity claims REFUTED — the upsert bypass (gate holds),
user_id forgery (stamped to self), cross-agent WAL (denied); plus full_dream
idempotency, retired-agent dreams, _logical_hash determinism, and two
arithmetic-impossible promote/retire claims — all refuted. See the table in
VERDICT-link4.md.

**ONE REAL DEFECT, Q12 — FIXED (schema v8):** a sub-agent could be born
under a RETIRED parent via the base table (the harness path), same family
as link-1's F2. New trigger `agents_parent_must_be_active`. 2 RED-first
proofs (`test_wyrm_link4.py`): guard fires; unrelated top-level agents
unaffected. **117/117 GREEN**, mypy strict, ruff clean.

**CONVERGENCE: NOT YET.** Because this fresh pass found a real defect, the
chain has not converged. §2.1 demands one more fresh re-run against v8 that
surfaces NOTHING new before the Brain-side criteria are marked complete.
That confirming pass is the next iteration. **The Brain is NOT marked done.**

### 2026-06-10 — Iteration 14: WYRM CHAIN LINK 3 — the Qwen review (operation.spec §2.1)

**THE REVIEW:** `qwen` (Qwen3-Coder-Next) via the live seat, fresh and
unsteered, fed only `brain.spec.md` + the CURRENT `src/brain/*.py` (no
pointers to Gemma's findings). Raw output committed to
`reviews/qwen-link3.md`. It made two high-severity claims, self-dismissed
most of its list, then degenerated into a repetition loop (identical
paragraph ×23 to the token cap — a real model failure, recorded not hidden).

**VERIFIED, NEVER ASSUMED:** F1 (global_settings UPDATE allegedly ungated)
REFUTED by probe — unblessed UPDATE is denied. F2 (lineage trigger allegedly
not firing on parent-only change) REFUTED by probe — re-parenting denied.
F3–F10 self-dismissed by the reviewer. F11 (embed_pending doesn't validate
vector dimensions) CONFIRMED, low severity.

**REAL CHANGE EARNED:** a dimension-consistency guard in `embed_pending` —
a faulty stone returning ragged vectors is rejected at the source with a
clear message, before any insert, instead of crashing confusingly later at
recall. 2 RED-first proofs in `test_wyrm_link3.py`. **115/115 GREEN**, mypy
strict, ruff clean.

**NEXT:** the final fresh re-run (link 4 of §2.1) — chain runs again clean,
no memory, no pointers; if it surfaces nothing new, the Brain's acceptance
list is converged and the Brain-side criteria can be marked complete.

### 2026-06-10 — Iteration 13: WYRM CHAIN LINK 2 — the Gemma-Opus review (operation.spec §2.1)

**THE REVIEW:** `gemma-opus` via the live llama-swap seat, fresh and
unsteered — fed only `brain.spec.md` + `src/brain/*.py`, no prior findings,
no tests, no ledger. Raw output committed verbatim to
`reviews/gemma-opus-link2.md`. It returned 10 findings and a severe verdict:
"isolation fundamentally broken."

**VERIFIED, NEVER ASSUMED:** every finding probed against live code (probes
in the iteration's bash log). The verdict rests on misreading the THREE-tier
connection trust model (blessed / unblessed-harness / scoped-agent) — it
read "unblessed" as "untrusted agent." Probe results: F1 refuted (scoped
current_agent_id is closure-fixed, my_memories isolated); F3 factually wrong
(learning_ledger IS protected — prints True); F4/F5 refuted (scoped
search/recall DENIED); F6 refuted (non-active stone refused); F2 refuted
(own settings only); F7/F8 harness-time concern not a Brain defect; F9
refuted for its actual use; F10 by-design.

**REAL CHANGES EARNED (not from accepting the verdict, but from the kernel
under it):** (1) `search()`/`recall()` now refuse a scoped connection
INTENTIONALLY with a clear "needs a harness connection" message, instead of
the incidental authorizer trip the probe revealed — 3 RED-first proofs in
`test_wyrm_link2.py`. (2) The three-tier trust model is now documented in
the substrate module docstring, closing the gap the whole (mistaken) verdict
exploited. **113/113 GREEN**, mypy strict, ruff clean.

**NEXT LINK:** Qwen reviews fresh and unsteered; same verify-don't-assume
protocol; then a final fresh re-run after changes.

### 2026-06-10 — Iteration 12: WYRM CHAIN LINK 1 — the builder's adversarial pass (operation.spec §2.1)

**THE PASS:** I attacked my own substrate. Every candidate finding was
VERIFIED BY LIVE PROBE before being treated as real — none assumed. Four
confirmed:
- **F1 (L6):** users/agents could retire with NO reason (memories/skills
  had the CHECK; users/agents didn't).
- **F2:** a retired agent's existing sessions still accepted WAL rows.
- **F3:** the global `full_dream(conn)` advanced no cadence markers —
  "due" lied forever.
- **F4:** `create_brain` dying mid-creation left a poisoned partial file.

**THE FIXES (schema v7):** reason-required CHECKs on users+agents;
`wal_agent_must_be_active` trigger (transcript closes at retirement);
`dream_pass1` skips retired agents silently; global full_dream advances
every active agent's marker; create_brain unlinks its debris and re-raises.

**HOW PROVEN:** 6 proofs written FIRST and run RED against the unfixed
substrate (all six bit), fixes applied, then **110/110 GREEN**, mypy strict
(it caught one annotation in the new test), ruff clean, no live db.

**NOTED, DELIBERATELY UNCHANGED (spec-silent; for Gemma/Qwen and the
Owner):** memory_links/tags deletable on harness connections; unblessed
can write vectors under inactive embedder ids (never surface); unblessed
can jump the fts_sync marker (caught by check_fts); an agent can reinforce
its own memories via my_ledger (own graph only; global promotion remains
impossible).

**NEXT LINK:** Gemma-Opus reviews the substrate fresh and unsteered, then
Qwen; findings verified against live code; fixes re-run the chain fresh.

### 2026-06-10 — Iteration 11: the operational drills (A9, A3 Brain-side)

**WHAT:** No new src code — the drills exercise the whole machine under
hostile conditions (`tests/test_drills.py`). (1) Static AST audit: every
import in the brain package ∈ stdlib ∪ {brain}. (2) Bare-machine drill: a
snapshot moved ALONE into an empty dir, opened by `/usr/bin/python3 -I`
(isolated: no venv, no PYTHONPATH, no user site) with only the source tree
— integrity gate, search, recall (stone reconstructed harness-side, id
matched), reinforce, dream pass, secret-ref read, all ran; the dir
contained nothing but the file. (3) The same fresh process is A3's "new
model": answered identity from agent_identity rows and project questions
through both verbs, from the file alone; durable count unchanged.

**HOW PROVEN:** Both drills passed on FIRST run — the honest shape for a
drill iteration (machinery already proven; the drill verifies it under
hostile conditions). Full suite **104/104 GREEN**, mypy strict (after
fixing a Sequence annotation it caught in the fixture), ruff clean, no
live db, `dependencies = []`.

**NOT claimed:** A1 (Owner's 15 questions + real stone), real-LLM A3 in
the gauntlet, real-stone A10/A7 re-runs — all harness-time. The Brain-side
surface is COMPLETE: every remaining open item needs either the Owner's
bench, the harness, or the Wyrm chain.

### 2026-06-10 — Iteration 10: dreams (R14 Brain-side, A8 crash-mid-dream)

**WHAT:** Schema v6 (5→6): `dream_marks` (per-session Pass-1 marker) +
`agent_dream_state` (per-agent full-dream marker), both forward-only and
undeletable by trigger, both scoped-blackout. New `src/brain/dreams.py`:
`Distiller` protocol (model-backed stone = harness material; toy in
proofs), `dream_pass1()` (distill→insert provisional→advance marker, one
transaction), `full_dream()` (**ledger is canonical, worth is derived**:
recompute worth from the event trail — this is what reconciles events
written by scoped agents through my_ledger with no arithmetic attached —
then net +2 promotes / net −2 retires, the promote rule being the exact
mirror of the verbs' retire rule), `pass1_due()`/`full_dream_due()`
(cadence from WAL counts vs markers; §5.3 defaults 5/15; per-agent
agent_settings overrides). No volatile counters anywhere.

**HOW PROVEN:** 10 proofs FIRST (RED: no module), then **102/102 GREEN**,
mypy strict + ruff clean, dependencies still []. One proof bug found and
fixed during the run: tests had used `PRAGMA database_list` to recover the
file path — denied by the Brain's own pragma wall (the wall caught its own
test; path now comes from the fixture). Crash drill: rollback mid-Pass-1
reverts memories and markers together; re-run distills exactly once.

**NOT claimed:** background scheduling/threading (harness runtime), the
model-backed Distiller, A3/A9 drills, A1 bench, Wyrm chain.

### 2026-06-10 — Iteration 9: embedder slot + recall() (R10/R11 close at slot level, A10 mechanics, A7 ranking)

**WHAT:** Schema v5 (4→5): `embeddings (memory_id, embedder_id, vector BLOB)`
PK-paired so stones never share rows; added to the scoped wall. In
`recall.py`: `Embedder` protocol, `set_embedder()` (blessed act — the gate
itself enforces it; runs the automatic full re-embed), `embed_pending()`
(self-healing), `recall()` (cosine; floor selects, worth ranks; retired
never surface; limit 10 = recall@10; 'recall' ledger events — R6's last
event type now live), `RecallError` for empty slot / wrong stone / mixed
dims. Boundary: the Brain owns the SLOT; the real e5 stone is Harness Glove
material. Zero dependencies added — vectors are float32 blobs, cosine is
plain Python; `dependencies = []` verified in the gate run.

**HOW PROVEN:** 11 proofs FIRST (RED: imports missing), then **92/92
GREEN**, mypy strict + ruff clean. Floor calibration was MEASURED, not
assumed: the toy stones' similarity bands (related ≥0.365, unrelated ≤0.277
across both spaces) put the test floor at 0.3 in the clean gap; the 0.5
default is the real stone's starting value. The drill: swap → auto
re-embed count verified → equivalent recall set under the new stone → zero
memories lost → old stone refused. The floor-then-worth proof is the
criterion's exact sentence: a 5×-reinforced soup memory does not surface
for "gauntlet walk", and the reinforced walk-twin ranks first.

**NOT claimed:** A1 (needs the Owner's 15 questions + real stone), real-
stone re-runs of A7/A10, dreams (R14, A8 second half), A3/A9 drills.

### 2026-06-10 — Iteration 8: snapshot/restore (A8 first half, operation.spec §4 tooling)

**WHAT:** New `src/brain/durability.py` — `snapshot()` (live-safe backup-API
copy to `snapshots/`, UTC-stamped, collision-suffixed, staged `.partial` +
atomic rename), `restore()` (refuses existing targets, integrity-gates the
result, removes debris on failure), `verify_twin()` (full-content logical
hash over every real table incl. FTS shadows). No schema change. This is
the §4 snapshot-before-any-change tool the go-live step will require.

**HOW PROVEN:** 8 proofs FIRST (RED: no module), then **82/82 GREEN**, mypy
strict + ruff clean. One mid-build fix: `wal_fts_idx` is WITHOUT ROWID, so
the logical hash orders rendered rows instead of rowids. Byte-faithfulness
proven LITERALLY: sha256 of twin snapshots from a quiesced source are
identical. The uncommitted-transaction proof shows snapshots hold committed
truth only while the open writer commits fine afterward. A tampered
snapshot fails restore through the fingerprint gate and leaves no file.

**NOT claimed:** crash-mid-dream half of A8 (waits for dreams), offsite
copies (operation.spec §4: wait until the Brain is verified working).

### 2026-06-10 — Iteration 7: learning verbs (R9 behavioral half, A7 worth half, R6 coupling)

**WHAT:** New `src/brain/learning.py` — `reinforce` / `contradict` /
`promote` / `retire` + `LearningError`. No schema change (v4 stands). The
Law's arithmetic from its own words, no invented numbers: symmetric ±0.1
steps (balanced signal = exact wash, §3.7); "repeated contradiction
retires" = contradictions exceeding reinforcements by 2 net (§3.3, with
worth floored at 0); promotion refused without ≥1 reinforce event ("merit
is earned, never declared", §3.4); deliberate retire carries reason +
optional same-agent successor with a supersede event (§3.5). Every verb
writes its ledger event in the same transaction as its data change. No verb
reads the clock (§3.6). Failures: zero special-casing (§3.8).

**HOW PROVEN:** 11 proofs FIRST, run RED (no module), then implementation,
then **74/74 GREEN on the first run**, mypy strict + ruff clean, no live db.
Highlights: worth-sorted query puts the reinforced twin first; reinforcement
buys resilience (2R+2C survives, 0R+2C retires); frozen-after-retire holds
against the verbs too; rollback reverts worth and ledger together.

**NOT claimed:** recall() + 'recall' ledger events + recall bench (A1, full
A7 close), dreams (§5.3), durability drills (A3/A8/A10), embedder slot (R11,
A10), Vaultwarden runtime (R15 behavior), zero-services drill (A9).

### 2026-06-10 — Iteration 6: FTS mirror + search() verb (A6 second half, R10 first half)

**WHAT:** Schema v4 (3→4). FTS5 external-content mirrors `wal_fts` +
`memories_fts`; `fts_sync` forward-only marker table (in the file — the
volatile-watermark autopsy lesson); `sync_fts()` / `check_fts()` /
`rebuild_fts()` maintenance verbs; new `brain/recall.py` with `search()` —
self-syncing keyword search over memories + WAL, BM25, agent filter,
sanitized input. Scoped wall extended to a TOTAL FTS blackout; pragma
allowlist (`data_version`, read form only) added for FTS5 internals.

**HOW PROVEN:** Proofs RED first (ImportError), then **63/63 GREEN**, mypy
strict + ruff clean. **A design failed mid-iteration and was replaced,
recorded honestly:** the first build used AFTER INSERT mirror triggers; the
authorizer trace (measured) showed FTS5's internal shadow-table statements
present as top-level SQL — no source attribution — so any connection allowed
to fire the trigger needed raw shadow access, and shadow tables hold every
agent's indexed content (cross-agent leak). Triggers removed; mirrors now
sync-only on harness connections behind the atomic forward-only marker.
Three denial messages accepted in the blackout proof (statement / column /
vtable-constructor) — all are refusals. Drift proofs: phantom caught,
rebuild repairs, double-sync clean, rollback keeps index+marker consistent.

**NOT claimed:** recall() semantic verb (needs the embedder slot — harness
Glove territory), learning verbs/worth arithmetic (A7), recall bench (A1),
dreams, durability drills (A3/A8/A10).

### 2026-06-10 — Iteration 5: read/write isolation — scoped agent connections (A2, R4)

**WHAT:** Schema v3 (2→3). `connect_agent(path, agent_id)`: registers
connection-local `current_agent_id()` + `owner_blessing()=0`, refuses missing/
retired agents (`AgentScopeError` — no silent identity fallback, the autopsy
lesson). 14 `my_*` views filter every visible row by the registered id; ~20
INSTEAD OF triggers stamp ownership themselves (caller claims ignored) and
RAISE on foreign endpoints (links/tags/wal/ledger). Scoped authorizer denies
everything the unblessed wall denies PLUS any top-level touch of the 14
protected base tables; access through a named source is allowed because a
scoped connection cannot create views/triggers, so every named source is
fingerprint-verified schema code.

**HOW PROVEN:** 10 adversarial proofs written FIRST from the attacker's
chair, run RED (ImportError), then implementation, then **52/52 GREEN**, mypy
strict + ruff clean. Mid-build discovery (measured via authorizer trace, not
assumed): rowid-seek table access carries NO view attribution — SQLite emits
a bare `(READ, table, '', source=None)`. Denying that shape is load-bearing
(it kills `COUNT(*)`-count and `WHERE rowid=`-existence oracles on protected
tables — both now proven denied), but it also broke `my_agent`, whose filter
on the PK compiled to a seek. Fix: lossless `GROUP BY agent_id` in the view
(measured: keeps attribution; `+column` hint did NOT survive flattening — a
fix that failed and was replaced). Cost accepted: `COUNT(*)` on my_* views is
denied; scoped SQL counts a column. A full-life proof confirms isolation
doesn't strangle lawful work (memories→ledger→promote→links→sessions→wal→
settings→identity→secrets all work scoped; substrate birth-event fires).

**NOT claimed:** recall/search verbs + FTS (A1/A6 second half), learning
verbs/worth arithmetic (A7), dreams, durability drills (A3/A8/A10), Wyrm
chain (waits for the full green list).

### 2026-06-10 — Iteration 4: agent sub-systems + global layer complete at schema level (R2/R3/R13/R15, A4)

**WHAT:** Schema v2 (1→2; still no live Brain, nothing migrated). Global
layer completed: `global_knowledge` (gated INSERT; UPDATE/DELETE abort even
blessed — A4; correction = superseding row), `global_hooks`, `global_skills`
(blessed-gated CRUD). Agent sub-systems completed: `agent_settings`,
`agent_identity` (served live from rows, updatable), `agent_hooks`, `skills`
(counters CHECK-bounded, content immutable, supersede same-agent, retire with
reason, retired agents take none), `secret_refs` (vault_ref column physically
accepts only `<service.type>` placeholders). Session spec:
`~/.claude/state/specs/brain-agent-subsystems.spec.md`.

**HOW PROVEN:** 9 proofs FIRST, run RED (no such table), then schema, then
**42/42 GREEN** (9 new + all 33 prior), mypy strict clean. Ruff caught one
over-long line in a trigger message — shortened, all gates re-run clean. No
live `*.db` in the tree.

**NOT claimed:** read-isolation (A2 — next: every ownership table now exists,
so scoped connections can land once over the full surface), recall/search
verbs + FTS, learning verbs/worth arithmetic (A7), dreams, Vaultwarden
runtime behavior.

### 2026-06-10 — Iteration 3: knowledge graph + learning ledger (R6, R9 substrate)

**WHAT:** Schema v1 (version 0→1; no live Brain existed, nothing migrated).
New tables: `memories` (agent-owned nodes; content canonical; `is_failure`
first-class; status provisional/durable/retired; worth born low),
`memory_links` (weighted, same-agent only, no self-links), `tags` +
`memory_tags` (per-agent hubs), `learning_ledger` (seven lawful events, cause
required, append-only, `birth` auto-written by trigger). Learning Law walls:
born-provisional, immutable content/owner, retire-with-reason-never-delete,
lawful transitions only, retired-is-frozen, supersede-same-agent, retired
agents take no new memories/sessions. Session spec:
`~/.claude/state/specs/brain-knowledge-graph.spec.md`.

**HOW PROVEN:** 13 proofs written FIRST, run RED (no such table: memories),
then schema, then **33/33 GREEN** (13 new + all 20 prior), mypy strict clean,
ruff clean, no live `*.db` in the tree. One proof was corrected during the
run: un-retiring a memory is stopped by two walls and the test had pinned the
wrong wall's message — fixed to accept either abort (the act is what's
forbidden, not the wording). Iteration-1 version test made version-agnostic
(`SCHEMA_VERSION` import instead of hardcoded "0").

**NOT claimed:** the learning verbs API (reinforce/contradict/promote/retire
with worth arithmetic and ledger coupling), recall/search, FTS, settings/
identity/hooks/skills/secret-refs tables, read-isolation (A2), A7 behavior
proofs.

### 2026-06-10 — Iteration 2: the deliberate-attack wall (A5)

**WHAT:** Two defenses added to `substrate.py`. (1) An authorizer on every
unblessed connection denies ALL schema-shaped actions at the substrate:
CREATE/DROP/ALTER of tables/indexes/triggers/views (temp included), every
PRAGMA, ATTACH/DETACH, ANALYZE, REINDEX. Blessed connections keep full power
— migrations arrive by ritual, with code. (2) A schema-fingerprint tripwire:
every `connect()` (blessed or not) hashes `sqlite_master` and compares it to
the expectation computed from the substrate's own DDL — held in code, outside
the file, where no file-writer can reach it — and checks `schema_version`.
Mismatch → `BrainIntegrityError`, loud and blocking. Session spec:
`~/.claude/state/specs/brain-attack-wall.spec.md`.

**HOW PROVEN:** Proofs first, run RED (ImportError: no `BrainIntegrityError`),
then implementation, then **20/20 GREEN** (7 new + all 13 from iteration 1),
mypy strict clean, ruff clean. New proofs: 11-attack assault all denied and
the gate still standing after; normal work (inserts/CTEs/transactions)
unbroken; blessed scratch-index migration works; raw-connection trigger-drop
caught at next open blessed AND unblessed; foreign file refused; wrong
version refused; two pristine Brains open cleanly (no false positives).

**SCOPE NOTE:** Read-isolation was deferred ON PURPOSE: built now it would
cover only the skeleton tables and be rebuilt when memories/settings/secrets
tables arrive. Order ahead: remaining §2.2 tables → read-isolation over the
full surface (A2).

### 2026-06-10 — Iteration 1: project foundation + schema substrate v0

**WHAT:** Created this working dir, git repo, and ledger. Stood up the uv
project (Python 3.12.3, pinned `uv.lock`, zero runtime deps, dev: pytest 9 /
mypy 2.1 strict / ruff 0.15). Built `src/brain/substrate.py`: schema v0 —
STRICT tables for all four layers (brain_meta + global_settings / users /
agents incl. sub-agents via parent_agent_id / sessions + wal), append-only
WAL triggers, fail-closed blessing gate (connection-registered
`owner_blessing()` checked by triggers on every global write), sub-agent
same-user wall, immutable lineage, retire-never-delete, single-owner index.
Session spec: `~/.claude/state/specs/brain-foundation.spec.md`.

**HOW PROVEN:** Leash followed end to end — proofs written FIRST and run RED
(`ModuleNotFoundError: No module named 'brain'`), implementation written,
then **13/13 proofs GREEN** (`uv run pytest -v`), `uv run mypy` strict clean,
`uv run ruff check` clean. All proofs ran on tmp-file copies; **no live
brain.db exists** (verified: zero `*.db` under /mnt/hdd/brain outside .venv).
Snapshot rule not yet triggered — there is no Brain to snapshot.

**NOT claimed:** read-isolation between users/agents (needs scoped
connections/authorizer — next), FTS mirror, memories/learning ledger/skills/
dreams/secrets tables, the deliberate-attack suite for A5 (DROP TRIGGER via
unblessed SQL must be made impossible or detected), Wyrm chain (runs when the
component's full acceptance list is green).
