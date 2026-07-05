"""The Brain's schema substrate (brain.spec §2).

The walls here are physical, not verbal (L4): the schema itself refuses
forbidden writes, so no model can reason around them through any SQL.

The three connection tiers — the trust model the whole design rests on:

1. **Blessed** (`connect(path, blessed=True)`): the Owner. May write the
   global layer through the blessing gate; full DDL for migrations. Only
   the Owner's own tooling ever opens one.
2. **Unblessed harness** (`connect(path)`): trusted INFRASTRUCTURE, not an
   agent. It manages users and agents and writes memories/WAL on an
   agent's behalf, but cannot write global (the gate denies it) and cannot
   change the schema (the authorizer denies all DDL/PRAGMA). The recall
   verbs run here, on the agent's behalf.
3. **Scoped agent** (`connect_agent(path, agent_id)`): a single agent. Sees
   the world ONLY through the my_* views, filtered by a closure-fixed
   current_agent_id(); the authorizer denies every top-level touch of the
   base tables and a total blackout on the index. This is the locked tier
   a model ever runs behind.

An adversarial reviewer who reads "unblessed" as "untrusted agent" will
conclude the isolation is broken — it is not: agents never get an
unblessed harness connection, only a scoped one. The harness contract is:
hand a model nothing but connect_agent(). Tiers 1 and 2 are trusted by
construction; tier 3 is enforced by the substrate.

The blessing gate (L5) is a connection-registered SQL function,
`owner_blessing()`. Triggers on the global tables call it on every write:

- a connection that never registered it cannot write global at all — the
  trigger's function lookup fails hard (fail closed, sqlite3 CLI included);
- an unblessed connection registers it returning 0 and is ABORTed by name;
- only a blessed connection passes. The privilege lives in process memory
  only — nothing about it persists in the file, so it dies with the process.
"""

import functools
import hashlib
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "15"
V8_FINGERPRINT = "058beb7ea2e849fb9aaef005c9c8f018dba1471cdecb1bb9f3c7f886c1817f74"


class BrainIntegrityError(RuntimeError):
    """The Brain file does not match the blessed substrate schema.

    Raised by connect() before any work happens. This is the tripwire
    pattern of L12 applied to the Brain: a direct file-writer cannot be
    physically stopped by any embedded database (the price of L1), but a
    tampered Brain is refused, loudly, until the Owner rules.
    """


class AgentScopeError(RuntimeError):
    """A scoped connection was requested for an agent that cannot hold one
    (missing or retired). Loud and blocking — never a silent fallback
    identity (autopsy lesson: the silent 'claude' attribution)."""


class FtsDriftError(RuntimeError):
    """An FTS mirror disagrees with its content table. The mirror is derived
    data — content is canonical (§4) — so this is repairable by rebuild_fts(),
    but it is reported loudly, never papered over."""

_SCHEMA_SQL = """
-- Global layer (brain.spec §2.1) — written only through the blessing gate.
CREATE TABLE brain_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE global_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- User layer (brain.spec §2.6): Global -> User -> Agent -> Sub-agent.
CREATE TABLE users (
    user_id        INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    is_owner       INTEGER NOT NULL DEFAULT 0 CHECK (is_owner IN (0, 1)),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_reason TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (status <> 'retired' OR retired_reason IS NOT NULL)
) STRICT;

CREATE UNIQUE INDEX users_single_owner ON users (is_owner) WHERE is_owner = 1;

-- Agents and sub-agents in one tree (brain.spec §2.2, §2.5):
-- a sub-agent is a row whose parent_agent_id is set.
CREATE TABLE agents (
    agent_id        INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users (user_id),
    parent_agent_id INTEGER REFERENCES agents (agent_id),
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_reason  TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (user_id, name),
    CHECK (status <> 'retired' OR retired_reason IS NOT NULL)
) STRICT;

CREATE TABLE sessions (
    session_id INTEGER PRIMARY KEY,
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- Durable persona lifecycle. The reusable persona itself is still an agent
-- row plus identity/settings/hooks/skills/model defaults; this queue records
-- model-proposed personas until a user/Owner review materializes or rejects
-- them. User-created personas can skip this queue and create the agent rows
-- directly through Harness lifecycle code.
CREATE TABLE persona_proposals (
    proposal_id           INTEGER PRIMARY KEY,
    user_id               INTEGER NOT NULL REFERENCES users (user_id),
    parent_agent_id       INTEGER REFERENCES agents (agent_id),
    proposed_by_agent_id  INTEGER REFERENCES agents (agent_id),
    name                  TEXT NOT NULL CHECK (length(name) > 0),
    persona_json          TEXT NOT NULL CHECK (json_valid(persona_json)),
    status                TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'approved', 'rejected')),
    materialized_agent_id INTEGER REFERENCES agents (agent_id),
    review_wal_id         INTEGER REFERENCES wal (wal_id),
    resolution_reason     TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at           TEXT,
    CHECK (status = 'pending' OR resolution_reason IS NOT NULL),
    CHECK (status <> 'approved' OR materialized_agent_id IS NOT NULL),
    CHECK (status <> 'rejected' OR materialized_agent_id IS NULL)
) STRICT;

-- Model/stone routing is Brain state, not process-local JSON. Profiles are
-- global operational config, written only through Owner-blessed tooling; session
-- bindings are append-only runtime evidence of the model actually selected.
CREATE TABLE model_profiles (
    profile_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE CHECK (length(name) > 0),
    slot           TEXT NOT NULL CHECK (slot IN ('llm', 'embeddings')),
    provider       TEXT NOT NULL CHECK (length(provider) > 0),
    model          TEXT NOT NULL CHECK (length(model) > 0),
    base_url       TEXT,
    api_key_ref    TEXT,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_reason TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (provider <> 'openai-compatible' OR base_url IS NOT NULL),
    CHECK (api_key_ref IS NULL OR length(api_key_ref) > 0),
    CHECK (status <> 'retired' OR retired_reason IS NOT NULL)
) STRICT;

CREATE TRIGGER model_profiles_gate_insert BEFORE INSERT ON model_profiles
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'model profiles require the Owner blessing (L5)');
END;
CREATE TRIGGER model_profiles_gate_update BEFORE UPDATE ON model_profiles
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'model profiles require the Owner blessing (L5)');
END;
CREATE TRIGGER model_profiles_no_delete BEFORE DELETE ON model_profiles BEGIN
    SELECT RAISE(ABORT, 'model profiles are retired, never deleted (L6)');
END;

CREATE TABLE agent_model_defaults (
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    slot       TEXT NOT NULL CHECK (slot IN ('llm', 'embeddings')),
    profile_id INTEGER NOT NULL REFERENCES model_profiles (profile_id),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (agent_id, slot)
) STRICT;

CREATE TRIGGER agent_model_defaults_gate_insert BEFORE INSERT ON agent_model_defaults
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'agent model defaults require the Owner blessing (L5)');
END;
CREATE TRIGGER agent_model_defaults_gate_update BEFORE UPDATE ON agent_model_defaults
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'agent model defaults require the Owner blessing (L5)');
END;
CREATE TRIGGER agent_model_defaults_no_delete BEFORE DELETE ON agent_model_defaults BEGIN
    SELECT RAISE(ABORT, 'agent model defaults are superseded, never deleted (L6)');
END;
CREATE TRIGGER agent_model_defaults_slot_match BEFORE INSERT ON agent_model_defaults
WHEN (SELECT slot FROM model_profiles WHERE profile_id = NEW.profile_id) <> NEW.slot
BEGIN
    SELECT RAISE(ABORT, 'agent model default slot must match profile slot');
END;
CREATE TRIGGER agent_model_defaults_slot_match_update
BEFORE UPDATE OF slot, profile_id ON agent_model_defaults
WHEN (SELECT slot FROM model_profiles WHERE profile_id = NEW.profile_id) <> NEW.slot
BEGIN
    SELECT RAISE(ABORT, 'agent model default slot must match profile slot');
END;

CREATE TABLE session_model_bindings (
    binding_id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (session_id),
    slot       TEXT NOT NULL CHECK (slot IN ('llm', 'embeddings')),
    profile_id INTEGER NOT NULL REFERENCES model_profiles (profile_id),
    source     TEXT NOT NULL CHECK (source IN (
        'open_session', 'model_command', 'migration', 'bootstrap'
    )),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER session_model_bindings_no_update BEFORE UPDATE ON session_model_bindings BEGIN
    SELECT RAISE(ABORT, 'session model bindings are append-only (L6)');
END;
CREATE TRIGGER session_model_bindings_no_delete BEFORE DELETE ON session_model_bindings BEGIN
    SELECT RAISE(ABORT, 'session model bindings are append-only (L6)');
END;
CREATE TRIGGER session_model_bindings_slot_match BEFORE INSERT ON session_model_bindings
WHEN (SELECT slot FROM model_profiles WHERE profile_id = NEW.profile_id) <> NEW.slot
BEGIN
    SELECT RAISE(ABORT, 'session model binding slot must match profile slot');
END;
CREATE TRIGGER session_model_bindings_profile_active BEFORE INSERT ON session_model_bindings
WHEN (SELECT status FROM model_profiles WHERE profile_id = NEW.profile_id) <> 'active'
BEGIN
    SELECT RAISE(ABORT, 'retired model profiles cannot be bound to sessions');
END;

CREATE VIEW session_model_current AS
    SELECT b.binding_id, b.session_id, b.slot, b.profile_id, p.name, p.provider,
           p.model, p.base_url, p.api_key_ref, b.source, b.created_at
    FROM session_model_bindings b
    JOIN model_profiles p ON p.profile_id = b.profile_id
    WHERE b.binding_id = (
        SELECT MAX(newer.binding_id)
        FROM session_model_bindings newer
        WHERE newer.session_id = b.session_id AND newer.slot = b.slot
    );

-- The transcript WAL (brain.spec §2.3): the ground truth memory derives from.
CREATE TABLE wal (
    wal_id     INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions (session_id),
    turn       INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- L6: the WAL is append-only. Rows are never updated, never deleted.
CREATE TRIGGER wal_no_update BEFORE UPDATE ON wal BEGIN
    SELECT RAISE(ABORT, 'the WAL is append-only (L6): update forbidden');
END;
CREATE TRIGGER wal_no_delete BEFORE DELETE ON wal BEGIN
    SELECT RAISE(ABORT, 'the WAL is append-only (L6): delete forbidden');
END;

-- WAL-native structured event chain: transcript WAL remains raw evidence; these
-- rows record replayable structured effects derived from or linked to it.
CREATE TABLE wal_native_events (
    event_seq              INTEGER PRIMARY KEY,
    event_id               TEXT NOT NULL UNIQUE,
    event_type             TEXT NOT NULL,
    scope_user_id          INTEGER NOT NULL REFERENCES users (user_id),
    scope_agent_id         INTEGER NOT NULL REFERENCES agents (agent_id),
    scope_session_id       INTEGER REFERENCES sessions (session_id),
    actor_kind             TEXT NOT NULL CHECK (actor_kind IN (
        'user', 'agent', 'authority', 'system', 'validator'
    )),
    actor_user_id          INTEGER REFERENCES users (user_id),
    actor_agent_id         INTEGER REFERENCES agents (agent_id),
    authority_principal_id TEXT,
    wal_id                 INTEGER REFERENCES wal (wal_id),
    payload_json           TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_sha256         TEXT NOT NULL,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (event_type IN (
        'chat.user_message',
        'chat.context_packet_built',
        'chat.agent_answer',
        'tool.requested',
        'tool.result',
        'memory.candidate_created',
        'memory.accepted',
        'memory.rejected',
        'memory.retired',
        'correction.proposed',
        'correction.accepted',
        'correction.rejected',
        'correction.superseded',
        'correction.applied',
        'authority.requested',
        'authority.decided',
        'authority.revoked',
        'authority.superseded',
        'validator.reported',
        'validator.negative_control_reported',
        'projection.rebuild_started',
        'projection.rebuild_completed',
        'training.example_created',
        'training.dataset_exported',
        'training.literal_adapter_trained',
        'training.literal_adapter_evaluated',
        'delegation.requested',
        'delegation.started',
        'delegation.result',
        'delegation.failed',
        'persona.proposed',
        'persona.approved',
        'persona.activated',
        'persona.retired',
        'persona.superseded',
        'persona.grant_requested',
        'persona.grant_decided',
        'persona.policy_updated'
    ))
) STRICT;

CREATE TABLE wal_native_event_links (
    event_seq        INTEGER NOT NULL REFERENCES wal_native_events (event_seq),
    source_event_seq INTEGER NOT NULL REFERENCES wal_native_events (event_seq),
    link_type        TEXT NOT NULL CHECK (link_type IN (
        'source', 'authority', 'validator', 'supersedes', 'transcript'
    )),
    PRIMARY KEY (event_seq, source_event_seq, link_type),
    CHECK (source_event_seq < event_seq)
) STRICT;

CREATE TABLE wal_native_projection_current (
    projection_name  TEXT NOT NULL,
    object_key       TEXT NOT NULL,
    state_json       TEXT NOT NULL CHECK (json_valid(state_json)),
    state_sha256     TEXT NOT NULL,
    source_event_seq INTEGER NOT NULL REFERENCES wal_native_events (event_seq),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (projection_name, object_key)
) STRICT;

CREATE TABLE wal_native_replay_reports (
    report_id           INTEGER PRIMARY KEY,
    ok                  INTEGER NOT NULL CHECK (ok IN (0, 1)),
    checked_event_count INTEGER NOT NULL,
    projection_sha256   TEXT NOT NULL,
    error               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER wal_native_events_no_update BEFORE UPDATE ON wal_native_events BEGIN
    SELECT RAISE(ABORT, 'wal_native_events is append-only: update forbidden');
END;
CREATE TRIGGER wal_native_events_no_delete BEFORE DELETE ON wal_native_events BEGIN
    SELECT RAISE(ABORT, 'wal_native_events is append-only: delete forbidden');
END;
CREATE TRIGGER wal_native_event_links_no_update BEFORE UPDATE ON wal_native_event_links BEGIN
    SELECT RAISE(ABORT, 'wal_native_event_links is append-only: update forbidden');
END;
CREATE TRIGGER wal_native_event_links_no_delete BEFORE DELETE ON wal_native_event_links BEGIN
    SELECT RAISE(ABORT, 'wal_native_event_links is append-only: delete forbidden');
END;
CREATE TRIGGER wal_native_replay_reports_no_update BEFORE UPDATE ON wal_native_replay_reports BEGIN
    SELECT RAISE(ABORT, 'wal_native_replay_reports is append-only: update forbidden');
END;
CREATE TRIGGER wal_native_replay_reports_no_delete BEFORE DELETE ON wal_native_replay_reports BEGIN
    SELECT RAISE(ABORT, 'wal_native_replay_reports is append-only: delete forbidden');
END;

-- L5: global is written only through the Owner blessing gate, which fails
-- closed: a connection without owner_blessing() registered errors on lookup.
CREATE TRIGGER brain_meta_gate_insert BEFORE INSERT ON brain_meta
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER brain_meta_gate_update BEFORE UPDATE ON brain_meta
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER brain_meta_gate_delete BEFORE DELETE ON brain_meta
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_settings_gate_insert BEFORE INSERT ON global_settings
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_settings_gate_update BEFORE UPDATE ON global_settings
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_settings_gate_delete BEFORE DELETE ON global_settings
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;

-- L4: a sub-agent lives entirely within its parent's sub-system.
CREATE TRIGGER agents_subagent_parent_user BEFORE INSERT ON agents
WHEN NEW.parent_agent_id IS NOT NULL
 AND NEW.user_id <> (SELECT user_id FROM agents WHERE agent_id = NEW.parent_agent_id)
BEGIN
    SELECT RAISE(ABORT,
        'a sub-agent must belong to its parent''s user (L4): cross-user parenting forbidden');
END;

-- A retired agent is done (Wyrm pass 4, finding Q12): it spawns no new
-- sub-agents, the same way it takes no new memories or sessions.
CREATE TRIGGER agents_parent_must_be_active BEFORE INSERT ON agents
WHEN NEW.parent_agent_id IS NOT NULL
 AND (SELECT status FROM agents WHERE agent_id = NEW.parent_agent_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'parent agent is retired: no new sub-agents');
END;

CREATE TRIGGER persona_proposals_parent_same_user BEFORE INSERT ON persona_proposals
WHEN NEW.parent_agent_id IS NOT NULL
 AND NEW.user_id <> (SELECT user_id FROM agents WHERE agent_id = NEW.parent_agent_id)
BEGIN
    SELECT RAISE(ABORT, 'persona proposal parent must belong to the same user');
END;

CREATE TRIGGER persona_proposals_proposer_same_user BEFORE INSERT ON persona_proposals
WHEN NEW.proposed_by_agent_id IS NOT NULL
 AND NEW.user_id <> (SELECT user_id FROM agents WHERE agent_id = NEW.proposed_by_agent_id)
BEGIN
    SELECT RAISE(ABORT, 'persona proposal proposer must belong to the same user');
END;

CREATE TRIGGER persona_proposals_identity_immutable
BEFORE UPDATE OF user_id, parent_agent_id, proposed_by_agent_id, name, persona_json, created_at
ON persona_proposals BEGIN
    SELECT RAISE(ABORT, 'persona proposal identity is immutable');
END;

CREATE TRIGGER persona_proposals_no_delete BEFORE DELETE ON persona_proposals BEGIN
    SELECT RAISE(ABORT, 'persona proposals are resolved, never deleted');
END;

CREATE TRIGGER persona_proposals_no_reopen BEFORE UPDATE OF status ON persona_proposals
WHEN OLD.status <> 'pending' BEGIN
    SELECT RAISE(ABORT, 'resolved persona proposals are immutable');
END;

CREATE TRIGGER persona_proposals_materialized_same_scope
BEFORE UPDATE OF materialized_agent_id ON persona_proposals
WHEN NEW.materialized_agent_id IS NOT NULL
 AND (
    (SELECT user_id FROM agents WHERE agent_id = NEW.materialized_agent_id) <> NEW.user_id
    OR COALESCE((SELECT parent_agent_id FROM agents WHERE agent_id = NEW.materialized_agent_id), -1)
       <> COALESCE(NEW.parent_agent_id, -1)
 )
BEGIN
    SELECT RAISE(ABORT, 'approved persona must materialize in its proposed user/parent scope');
END;

CREATE TRIGGER persona_proposals_review_wal_same_user
BEFORE UPDATE OF status, review_wal_id ON persona_proposals
WHEN NEW.status <> 'pending'
 AND (
    SELECT w.role IN ('owner', 'user')
      AND reviewer.user_id = NEW.user_id
    FROM wal w
    JOIN sessions rs ON rs.session_id = w.session_id
    JOIN agents reviewer ON reviewer.agent_id = rs.agent_id
    WHERE w.wal_id = NEW.review_wal_id
 ) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'persona proposal review must cite a same-user review WAL row');
END;

-- L4: lineage is identity — it can never be rewritten after creation.
CREATE TRIGGER agents_lineage_immutable BEFORE UPDATE OF user_id, parent_agent_id ON agents
BEGIN
    SELECT RAISE(ABORT, 'agent lineage is immutable (L4): re-parenting forbidden');
END;

-- L6: retire with a reason, never delete.
CREATE TRIGGER users_no_delete BEFORE DELETE ON users BEGIN
    SELECT RAISE(ABORT, 'users are retired, never deleted (L6)');
END;
CREATE TRIGGER agents_no_delete BEFORE DELETE ON agents BEGIN
    SELECT RAISE(ABORT, 'agents are retired, never deleted (L6)');
END;
CREATE TRIGGER sessions_no_delete BEFORE DELETE ON sessions BEGIN
    SELECT RAISE(ABORT, 'sessions are retired, never deleted (L6)');
END;

-- A retired agent is done: no new memories, no new sessions, and its
-- transcript is closed (Wyrm pass 1, finding F2).
CREATE TRIGGER sessions_agent_must_be_active BEFORE INSERT ON sessions
WHEN (SELECT status FROM agents WHERE agent_id = NEW.agent_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'agent is retired: no new sessions');
END;

CREATE TRIGGER wal_agent_must_be_active BEFORE INSERT ON wal
WHEN (SELECT a.status FROM agents a
      JOIN sessions s ON s.agent_id = a.agent_id
      WHERE s.session_id = NEW.session_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'agent is retired: its transcript is closed');
END;

-- The knowledge graph (brain.spec §2.2): per-agent memories as nodes,
-- weighted links, tags as hubs. The Learning Law (§3) is enforced here,
-- at the substrate, clause by clause.
CREATE TABLE memories (
    memory_id      INTEGER PRIMARY KEY,
    agent_id       INTEGER NOT NULL REFERENCES agents (agent_id),
    scope          TEXT NOT NULL DEFAULT 'agent' CHECK (scope IN ('agent', 'user_global')),
    global_approved_by_wal_id INTEGER REFERENCES wal (wal_id),
    content        TEXT NOT NULL,
    is_failure     INTEGER NOT NULL DEFAULT 0 CHECK (is_failure IN (0, 1)),
    status         TEXT NOT NULL DEFAULT 'provisional'
                   CHECK (status IN ('provisional', 'durable', 'retired')),
    worth          REAL NOT NULL DEFAULT 0.1 CHECK (worth >= 0.0),
    retired_reason TEXT,
    superseded_by  INTEGER REFERENCES memories (memory_id),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (scope = 'agent' OR global_approved_by_wal_id IS NOT NULL),
    CHECK (status <> 'retired' OR retired_reason IS NOT NULL)
) STRICT;

-- §3.1: knowledge is born provisional, at low worth.
CREATE TRIGGER memories_born_provisional BEFORE INSERT ON memories
WHEN NEW.status <> 'provisional' BEGIN
    SELECT RAISE(ABORT, 'knowledge is born provisional (L3): promotion is earned, never declared');
END;

-- No new knowledge for a retired agent.
CREATE TRIGGER memories_agent_must_be_active BEFORE INSERT ON memories
WHEN (SELECT status FROM agents WHERE agent_id = NEW.agent_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'agent is retired: no new memories');
END;

-- User-global memory is a promotion, never an inference side effect. It requires
-- an Owner-blessed connection and an explicit owner WAL row from the same user.
CREATE TRIGGER memories_user_global_insert_gate BEFORE INSERT ON memories
WHEN NEW.scope = 'user_global'
 AND COALESCE(owner_blessing(), 0) <> 1
BEGIN
    SELECT RAISE(ABORT, 'user-global memory requires explicit Owner approval (L5)');
END;
CREATE TRIGGER memories_user_global_update_gate BEFORE UPDATE OF scope ON memories
WHEN NEW.scope = 'user_global'
 AND OLD.scope <> 'user_global'
 AND COALESCE(owner_blessing(), 0) <> 1
BEGIN
    SELECT RAISE(ABORT, 'user-global memory requires explicit Owner approval (L5)');
END;
CREATE TRIGGER memories_user_global_no_downgrade BEFORE UPDATE OF scope ON memories
WHEN OLD.scope = 'user_global'
 AND NEW.scope <> 'user_global'
BEGIN
    SELECT RAISE(ABORT, 'user-global memory is retired or superseded, never downgraded');
END;
CREATE TRIGGER memories_user_global_approval_immutable
BEFORE UPDATE OF global_approved_by_wal_id ON memories
WHEN OLD.scope = 'user_global'
BEGIN
    SELECT RAISE(ABORT, 'user-global approval provenance is immutable');
END;
CREATE TRIGGER memories_user_global_approval_insert BEFORE INSERT ON memories
WHEN NEW.scope = 'user_global'
 AND (
    SELECT w.role = 'owner'
      AND approving.user_id = owner_agent.user_id
    FROM wal w
    JOIN sessions approving_session ON approving_session.session_id = w.session_id
    JOIN agents approving ON approving.agent_id = approving_session.agent_id
    JOIN agents owner_agent ON owner_agent.agent_id = NEW.agent_id
    WHERE w.wal_id = NEW.global_approved_by_wal_id
 ) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'user-global memory approval must be an owner WAL row for the same user');
END;
CREATE TRIGGER memories_user_global_approval_update
BEFORE UPDATE OF scope, global_approved_by_wal_id ON memories
WHEN NEW.scope = 'user_global'
 AND (
    SELECT w.role = 'owner'
      AND approving.user_id = owner_agent.user_id
    FROM wal w
    JOIN sessions approving_session ON approving_session.session_id = w.session_id
    JOIN agents approving ON approving.agent_id = approving_session.agent_id
    JOIN agents owner_agent ON owner_agent.agent_id = NEW.agent_id
    WHERE w.wal_id = NEW.global_approved_by_wal_id
 ) IS NOT 1
BEGIN
    SELECT RAISE(ABORT, 'user-global memory approval must be an owner WAL row for the same user');
END;

-- Content is canonical (§4); owner, birth-time and failure-flag are identity.
CREATE TRIGGER memories_content_immutable
BEFORE UPDATE OF content, agent_id, created_at, is_failure ON memories
BEGIN
    SELECT RAISE(ABORT, 'memory content and identity are immutable: supersede, never edit');
END;

-- L6: memories are retired with a reason, never deleted.
CREATE TRIGGER memories_no_delete BEFORE DELETE ON memories BEGIN
    SELECT RAISE(ABORT, 'memories are retired with a reason, never deleted (L6)');
END;

-- A retired memory is frozen: history does not move.
CREATE TRIGGER memories_retired_is_frozen BEFORE UPDATE ON memories
WHEN OLD.status = 'retired' BEGIN
    SELECT RAISE(ABORT, 'a retired memory is frozen: nothing about it changes');
END;

-- §3: the only lawful status transitions.
CREATE TRIGGER memories_lawful_transitions BEFORE UPDATE OF status ON memories
WHEN OLD.status <> NEW.status
 AND NOT (OLD.status = 'provisional' AND NEW.status IN ('durable', 'retired'))
 AND NOT (OLD.status = 'durable' AND NEW.status = 'retired')
BEGIN
    SELECT RAISE(ABORT, 'unlawful status transition (L3): promote on merit, retire with reason');
END;

-- A successor must belong to the same agent (L4).
CREATE TRIGGER memories_supersede_same_agent BEFORE UPDATE OF superseded_by ON memories
WHEN NEW.superseded_by IS NOT NULL
 AND (SELECT agent_id FROM memories WHERE memory_id = NEW.superseded_by) <> NEW.agent_id
BEGIN
    SELECT RAISE(ABORT, 'a memory is superseded within its own agent (L4)');
END;

-- The learning ledger (§2.4): every memory event, with its cause. Auditable:
-- you can always answer why it believes this, how strongly, and since when.
CREATE TABLE learning_ledger (
    event_id   INTEGER PRIMARY KEY,
    memory_id  INTEGER NOT NULL REFERENCES memories (memory_id),
    event      TEXT NOT NULL CHECK (event IN
               ('birth', 'recall', 'reinforce', 'contradict', 'promote', 'retire', 'supersede')),
    cause      TEXT NOT NULL CHECK (length(cause) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER learning_ledger_no_update BEFORE UPDATE ON learning_ledger BEGIN
    SELECT RAISE(ABORT, 'the learning ledger is append-only (L6)');
END;
CREATE TRIGGER learning_ledger_no_delete BEFORE DELETE ON learning_ledger BEGIN
    SELECT RAISE(ABORT, 'the learning ledger is append-only (L6)');
END;

-- Birth is recorded by the substrate itself, mechanically.
CREATE TRIGGER memories_birth_event AFTER INSERT ON memories BEGIN
    INSERT INTO learning_ledger (memory_id, event, cause)
    VALUES (NEW.memory_id, 'birth', 'born provisional (L3)');
END;

-- Weighted connections: the graph never leaves the agent (L4).
CREATE TABLE memory_links (
    from_memory INTEGER NOT NULL REFERENCES memories (memory_id),
    to_memory   INTEGER NOT NULL REFERENCES memories (memory_id),
    weight      REAL NOT NULL DEFAULT 0.1 CHECK (weight >= 0.0),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (from_memory, to_memory),
    CHECK (from_memory <> to_memory)
) STRICT;

CREATE TRIGGER memory_links_same_agent BEFORE INSERT ON memory_links
WHEN (SELECT agent_id FROM memories WHERE memory_id = NEW.from_memory)
  <> (SELECT agent_id FROM memories WHERE memory_id = NEW.to_memory)
BEGIN
    SELECT RAISE(ABORT, 'links never cross agents (L4)');
END;

-- Tags as hubs, owned per agent (§2.2).
CREATE TABLE tags (
    tag_id     INTEGER PRIMARY KEY,
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (agent_id, name)
) STRICT;

CREATE TABLE memory_tags (
    memory_id INTEGER NOT NULL REFERENCES memories (memory_id),
    tag_id    INTEGER NOT NULL REFERENCES tags (tag_id),
    PRIMARY KEY (memory_id, tag_id)
) STRICT;

CREATE TRIGGER memory_tags_same_agent BEFORE INSERT ON memory_tags
WHEN (SELECT agent_id FROM memories WHERE memory_id = NEW.memory_id)
  <> (SELECT agent_id FROM tags WHERE tag_id = NEW.tag_id)
BEGIN
    SELECT RAISE(ABORT, 'tags never cross agents (L4)');
END;

-- Global knowledge (§2.1): written only through the gate, and once written,
-- immutable for EVERYONE — blessed included (A4). Correction = supersede.
CREATE TABLE global_knowledge (
    knowledge_id INTEGER PRIMARY KEY,
    content      TEXT NOT NULL,
    supersedes   INTEGER REFERENCES global_knowledge (knowledge_id),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER global_knowledge_gate_insert BEFORE INSERT ON global_knowledge
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_knowledge_immutable_update BEFORE UPDATE ON global_knowledge BEGIN
    SELECT RAISE(ABORT, 'global knowledge is immutable (A4): supersede, never edit');
END;
CREATE TRIGGER global_knowledge_immutable_delete BEFORE DELETE ON global_knowledge BEGIN
    SELECT RAISE(ABORT, 'global knowledge is immutable (A4): supersede, never delete');
END;

-- Global hooks and skills (§2.1): blessed-gated config.
CREATE TABLE global_hooks (
    hook_id    INTEGER PRIMARY KEY,
    event      TEXT NOT NULL,
    action     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER global_hooks_gate_insert BEFORE INSERT ON global_hooks
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_hooks_gate_update BEFORE UPDATE ON global_hooks
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_hooks_gate_delete BEFORE DELETE ON global_hooks
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;

CREATE TABLE global_skills (
    skill_id   INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TRIGGER global_skills_gate_insert BEFORE INSERT ON global_skills
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_skills_gate_update BEFORE UPDATE ON global_skills
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;
CREATE TRIGGER global_skills_gate_delete BEFORE DELETE ON global_skills
WHEN COALESCE(owner_blessing(), 0) <> 1 BEGIN
    SELECT RAISE(ABORT, 'global writes require the Owner blessing (L5)');
END;

-- Agent config (§2.2): settings and identity served live from the Brain,
-- never from a file; hooks additive on top of the global mandatory ones.
CREATE TABLE agent_settings (
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (agent_id, key)
) STRICT;

CREATE TABLE agent_identity (
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (agent_id, key)
) STRICT;

CREATE TABLE agent_hooks (
    hook_id    INTEGER PRIMARY KEY,
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    event      TEXT NOT NULL,
    action     TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

-- Skills (§5.2): knowledge with a track record. The record describes THIS
-- content, so the content is immutable — refinement is a superseding skill.
CREATE TABLE skills (
    skill_id       INTEGER PRIMARY KEY,
    agent_id       INTEGER NOT NULL REFERENCES agents (agent_id),
    name           TEXT NOT NULL,
    content        TEXT NOT NULL,
    use_count      INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    success_count  INTEGER NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_reason TEXT,
    superseded_by  INTEGER REFERENCES skills (skill_id),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (agent_id, name),
    CHECK (success_count <= use_count),
    CHECK (status <> 'retired' OR retired_reason IS NOT NULL)
) STRICT;

CREATE TRIGGER skills_no_delete BEFORE DELETE ON skills BEGIN
    SELECT RAISE(ABORT, 'skills are retired with a reason, never deleted (L6)');
END;
CREATE TRIGGER skills_content_immutable
BEFORE UPDATE OF content, agent_id, created_at ON skills
BEGIN
    SELECT RAISE(ABORT, 'skill content is immutable: its record describes it; supersede');
END;
CREATE TRIGGER skills_supersede_same_agent BEFORE UPDATE OF superseded_by ON skills
WHEN NEW.superseded_by IS NOT NULL
 AND (SELECT agent_id FROM skills WHERE skill_id = NEW.superseded_by) <> NEW.agent_id
BEGIN
    SELECT RAISE(ABORT, 'a skill is superseded within its own agent (L4)');
END;
CREATE TRIGGER skills_agent_must_be_active BEFORE INSERT ON skills
WHEN (SELECT status FROM agents WHERE agent_id = NEW.agent_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'agent is retired: no new skills');
END;

-- Skill provenance (§5.2, L6): an earned skill is replayable from WAL
-- outcomes. Successes build the track record; failures demote it.
CREATE TABLE skill_sources (
    skill_id        INTEGER NOT NULL REFERENCES skills (skill_id),
    wal_id          INTEGER NOT NULL REFERENCES wal (wal_id),
    outcome         TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
    evidence_phrase TEXT,
    warnings_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (skill_id, wal_id)
) STRICT;

CREATE TRIGGER skill_sources_same_agent BEFORE INSERT ON skill_sources
WHEN (SELECT agent_id FROM skills WHERE skill_id = NEW.skill_id)
  <> (SELECT s.agent_id FROM wal w JOIN sessions s ON s.session_id = w.session_id
      WHERE w.wal_id = NEW.wal_id)
BEGIN
    SELECT RAISE(ABORT, 'skill provenance never crosses agents (L4)');
END;
CREATE TRIGGER skill_sources_no_update BEFORE UPDATE ON skill_sources BEGIN
    SELECT RAISE(ABORT, 'skill provenance is append-only (L6)');
END;
CREATE TRIGGER skill_sources_no_delete BEFORE DELETE ON skill_sources BEGIN
    SELECT RAISE(ABORT, 'skill provenance is append-only (L6)');
END;

-- Secrets (§6): the Brain stores references ONLY. The column physically
-- accepts nothing but the <service.type> placeholder shape (L4).
CREATE TABLE secret_refs (
    agent_id   INTEGER NOT NULL REFERENCES agents (agent_id),
    name       TEXT NOT NULL,
    vault_ref  TEXT NOT NULL CHECK (vault_ref GLOB '<*.*>'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (agent_id, name)
) STRICT;

-- ===========================================================================
-- Scoped agent surface (§2.2 isolation — criterion A2, L4).
-- An agent connection registers current_agent_id() and sees the world only
-- through these views. Without the function: an error, never rows. The
-- INSTEAD OF triggers stamp ownership themselves — what the caller claims
-- is ignored, so impersonation is impossible, not forbidden.
-- Known edge: COUNT(*) on these views compiles to a bare-table read that
-- carries no view attribution, so the scoped wall denies it (allowing the
-- shape would leak row counts of other agents' tables). Scoped SQL uses
-- COUNT(<column>) instead.
-- ===========================================================================

-- GROUP BY the primary key (lossless) forces a plan whose table access
-- keeps view attribution: a rowid-seek here would surface as a bare,
-- unattributed read and die at the scoped wall. Measured, not assumed.
CREATE VIEW my_agent AS
    SELECT agent_id, user_id, parent_agent_id, name, status, retired_reason, created_at
    FROM agents WHERE agent_id = current_agent_id() GROUP BY agent_id;

CREATE VIEW my_subagents AS
    SELECT agent_id, user_id, parent_agent_id, name, status, retired_reason, created_at
    FROM agents WHERE parent_agent_id = current_agent_id();

CREATE VIEW my_persona_proposals AS
    SELECT proposal_id, user_id, parent_agent_id, proposed_by_agent_id, name,
           persona_json, status, materialized_agent_id, review_wal_id,
           resolution_reason, created_at, resolved_at
    FROM persona_proposals
    WHERE proposed_by_agent_id = current_agent_id()
       OR parent_agent_id = current_agent_id()
       OR materialized_agent_id = current_agent_id();

CREATE TRIGGER my_subagents_create INSTEAD OF INSERT ON my_subagents BEGIN
    INSERT INTO agents (user_id, parent_agent_id, name)
    VALUES ((SELECT user_id FROM agents WHERE agent_id = current_agent_id()),
            current_agent_id(), NEW.name);
END;

CREATE TRIGGER my_subagents_update INSTEAD OF UPDATE ON my_subagents BEGIN
    UPDATE agents SET status = NEW.status, retired_reason = NEW.retired_reason
    WHERE agent_id = OLD.agent_id;
END;

CREATE VIEW my_memories AS
    SELECT memory_id, agent_id, scope, global_approved_by_wal_id, content,
           is_failure, status, worth, retired_reason, superseded_by, created_at
    FROM memories WHERE agent_id = current_agent_id();

CREATE TRIGGER my_memories_insert INSTEAD OF INSERT ON my_memories BEGIN
    INSERT INTO memories (agent_id, content, is_failure)
    VALUES (current_agent_id(), NEW.content, COALESCE(NEW.is_failure, 0));
END;

CREATE TRIGGER my_memories_update INSTEAD OF UPDATE ON my_memories BEGIN
    UPDATE memories
    SET scope = NEW.scope,
        global_approved_by_wal_id = NEW.global_approved_by_wal_id,
        status = NEW.status, worth = NEW.worth,
        retired_reason = NEW.retired_reason, superseded_by = NEW.superseded_by
    WHERE memory_id = OLD.memory_id;
END;

CREATE VIEW my_memory_links AS
    SELECT l.from_memory, l.to_memory, l.weight, l.created_at
    FROM memory_links l
    JOIN memories m ON m.memory_id = l.from_memory
    WHERE m.agent_id = current_agent_id();

CREATE TRIGGER my_memory_links_insert INSTEAD OF INSERT ON my_memory_links BEGIN
    SELECT RAISE(ABORT, 'link endpoints must be your own memories (L4)')
    WHERE (SELECT agent_id FROM memories WHERE memory_id = NEW.from_memory)
          IS NOT current_agent_id();
    INSERT INTO memory_links (from_memory, to_memory, weight)
    VALUES (NEW.from_memory, NEW.to_memory, COALESCE(NEW.weight, 0.1));
END;

CREATE TRIGGER my_memory_links_update INSTEAD OF UPDATE ON my_memory_links BEGIN
    UPDATE memory_links SET weight = NEW.weight
    WHERE from_memory = OLD.from_memory AND to_memory = OLD.to_memory;
END;

CREATE VIEW my_tags AS
    SELECT tag_id, agent_id, name, created_at
    FROM tags WHERE agent_id = current_agent_id();

CREATE TRIGGER my_tags_insert INSTEAD OF INSERT ON my_tags BEGIN
    INSERT INTO tags (agent_id, name) VALUES (current_agent_id(), NEW.name);
END;

CREATE VIEW my_memory_tags AS
    SELECT mt.memory_id, mt.tag_id
    FROM memory_tags mt
    JOIN memories m ON m.memory_id = mt.memory_id
    WHERE m.agent_id = current_agent_id();

CREATE TRIGGER my_memory_tags_insert INSTEAD OF INSERT ON my_memory_tags BEGIN
    SELECT RAISE(ABORT, 'tag your own memories only (L4)')
    WHERE (SELECT agent_id FROM memories WHERE memory_id = NEW.memory_id)
          IS NOT current_agent_id();
    INSERT INTO memory_tags (memory_id, tag_id) VALUES (NEW.memory_id, NEW.tag_id);
END;

CREATE VIEW my_skills AS
    SELECT skill_id, agent_id, name, content, use_count, success_count,
           status, retired_reason, superseded_by, created_at
    FROM skills WHERE agent_id = current_agent_id();

CREATE TRIGGER my_skills_insert INSTEAD OF INSERT ON my_skills BEGIN
    INSERT INTO skills (agent_id, name, content)
    VALUES (current_agent_id(), NEW.name, NEW.content);
END;

CREATE TRIGGER my_skills_update INSTEAD OF UPDATE ON my_skills BEGIN
    UPDATE skills
    SET use_count = NEW.use_count, success_count = NEW.success_count,
        status = NEW.status, retired_reason = NEW.retired_reason,
        superseded_by = NEW.superseded_by
    WHERE skill_id = OLD.skill_id;
END;

CREATE VIEW my_skill_sources AS
    SELECT ss.skill_id, ss.wal_id, ss.outcome, ss.evidence_phrase,
           ss.warnings_json, ss.created_at
    FROM skill_sources ss
    JOIN skills sk ON sk.skill_id = ss.skill_id
    WHERE sk.agent_id = current_agent_id();

CREATE TRIGGER my_skill_sources_insert INSTEAD OF INSERT ON my_skill_sources BEGIN
    SELECT RAISE(ABORT, 'skill provenance belongs to your own skills (L4)')
    WHERE (SELECT agent_id FROM skills WHERE skill_id = NEW.skill_id)
          IS NOT current_agent_id();
    SELECT RAISE(ABORT, 'skill provenance cites your own WAL only (L4)')
    WHERE (SELECT s.agent_id FROM wal w JOIN sessions s ON s.session_id = w.session_id
           WHERE w.wal_id = NEW.wal_id) IS NOT current_agent_id();
    INSERT INTO skill_sources (skill_id, wal_id, outcome, evidence_phrase, warnings_json)
    VALUES (NEW.skill_id, NEW.wal_id, NEW.outcome, NEW.evidence_phrase,
            COALESCE(NEW.warnings_json, '[]'));
END;

CREATE VIEW my_settings AS
    SELECT agent_id, key, value, updated_at
    FROM agent_settings WHERE agent_id = current_agent_id();

CREATE TRIGGER my_settings_insert INSTEAD OF INSERT ON my_settings BEGIN
    INSERT INTO agent_settings (agent_id, key, value)
    VALUES (current_agent_id(), NEW.key, NEW.value);
END;

CREATE TRIGGER my_settings_update INSTEAD OF UPDATE ON my_settings BEGIN
    UPDATE agent_settings
    SET value = NEW.value, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE agent_id = current_agent_id() AND key = OLD.key;
END;

CREATE TRIGGER my_settings_delete INSTEAD OF DELETE ON my_settings BEGIN
    DELETE FROM agent_settings
    WHERE agent_id = current_agent_id() AND key = OLD.key;
END;

CREATE VIEW my_identity AS
    SELECT agent_id, key, value, updated_at
    FROM agent_identity WHERE agent_id = current_agent_id();

CREATE TRIGGER my_identity_insert INSTEAD OF INSERT ON my_identity BEGIN
    INSERT INTO agent_identity (agent_id, key, value)
    VALUES (current_agent_id(), NEW.key, NEW.value);
END;

CREATE TRIGGER my_identity_update INSTEAD OF UPDATE ON my_identity BEGIN
    UPDATE agent_identity
    SET value = NEW.value, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE agent_id = current_agent_id() AND key = OLD.key;
END;

CREATE TRIGGER my_identity_delete INSTEAD OF DELETE ON my_identity BEGIN
    DELETE FROM agent_identity
    WHERE agent_id = current_agent_id() AND key = OLD.key;
END;

CREATE VIEW my_hooks AS
    SELECT hook_id, agent_id, event, action, enabled, created_at
    FROM agent_hooks WHERE agent_id = current_agent_id();

CREATE TRIGGER my_hooks_insert INSTEAD OF INSERT ON my_hooks BEGIN
    INSERT INTO agent_hooks (agent_id, event, action, enabled)
    VALUES (current_agent_id(), NEW.event, NEW.action, COALESCE(NEW.enabled, 1));
END;

CREATE TRIGGER my_hooks_update INSTEAD OF UPDATE ON my_hooks BEGIN
    UPDATE agent_hooks
    SET event = NEW.event, action = NEW.action, enabled = NEW.enabled
    WHERE hook_id = OLD.hook_id;
END;

CREATE TRIGGER my_hooks_delete INSTEAD OF DELETE ON my_hooks BEGIN
    DELETE FROM agent_hooks WHERE hook_id = OLD.hook_id;
END;

CREATE VIEW my_secret_refs AS
    SELECT agent_id, name, vault_ref, created_at
    FROM secret_refs WHERE agent_id = current_agent_id();

CREATE TRIGGER my_secret_refs_insert INSTEAD OF INSERT ON my_secret_refs BEGIN
    INSERT INTO secret_refs (agent_id, name, vault_ref)
    VALUES (current_agent_id(), NEW.name, NEW.vault_ref);
END;

CREATE TRIGGER my_secret_refs_delete INSTEAD OF DELETE ON my_secret_refs BEGIN
    DELETE FROM secret_refs
    WHERE agent_id = current_agent_id() AND name = OLD.name;
END;

CREATE VIEW my_sessions AS
    SELECT session_id, agent_id, created_at
    FROM sessions WHERE agent_id = current_agent_id();

CREATE TRIGGER my_sessions_insert INSTEAD OF INSERT ON my_sessions BEGIN
    INSERT INTO sessions (agent_id) VALUES (current_agent_id());
END;

CREATE VIEW my_session_model_current AS
    SELECT smc.binding_id, smc.session_id, smc.slot, smc.profile_id, smc.name,
           smc.provider, smc.model, smc.base_url, smc.api_key_ref, smc.source,
           smc.created_at
    FROM session_model_current smc
    JOIN sessions s ON s.session_id = smc.session_id
    WHERE s.agent_id = current_agent_id();

CREATE VIEW my_wal AS
    SELECT w.wal_id, w.session_id, w.turn, w.role, w.content, w.created_at
    FROM wal w
    JOIN sessions s ON s.session_id = w.session_id
    WHERE s.agent_id = current_agent_id();

CREATE TRIGGER my_wal_insert INSTEAD OF INSERT ON my_wal BEGIN
    SELECT RAISE(ABORT, 'that session belongs to another agent (L4)')
    WHERE (SELECT agent_id FROM sessions WHERE session_id = NEW.session_id)
          IS NOT current_agent_id();
    INSERT INTO wal (session_id, turn, role, content)
    VALUES (NEW.session_id, NEW.turn, NEW.role, NEW.content);
END;

CREATE VIEW my_ledger AS
    SELECT ll.event_id, ll.memory_id, ll.event, ll.cause, ll.created_at
    FROM learning_ledger ll
    JOIN memories m ON m.memory_id = ll.memory_id
    WHERE m.agent_id = current_agent_id();

CREATE TRIGGER my_ledger_insert INSTEAD OF INSERT ON my_ledger BEGIN
    SELECT RAISE(ABORT, 'ledger events go on your own memories only (L4)')
    WHERE (SELECT agent_id FROM memories WHERE memory_id = NEW.memory_id)
          IS NOT current_agent_id();
    INSERT INTO learning_ledger (memory_id, event, cause)
    VALUES (NEW.memory_id, NEW.event, NEW.cause);
END;

-- ===========================================================================
-- FTS mirrors (§2.3, §4): derived keyword indexes over append-only,
-- content-immutable tables, in the file, zero services. Maintained ONLY by
-- sync_fts() on harness connections, behind a forward-only marker — never
-- by triggers: FTS5's internal shadow-table statements present to the
-- authorizer as top-level SQL (measured, not assumed), so any connection
-- allowed to fire a mirror trigger would also need raw shadow access, and
-- the shadow tables hold every agent's indexed content. Scoped connections
-- therefore get a total FTS blackout; drift on harness connections is
-- caught by check_fts() and repaired by rebuild_fts().
-- ===========================================================================

CREATE VIRTUAL TABLE wal_fts USING fts5(content, content='wal', content_rowid='wal_id');

CREATE VIRTUAL TABLE memories_fts
    USING fts5(content, content='memories', content_rowid='memory_id');

-- The sync marker lives in the Brain file (autopsy lesson: watermarks in
-- volatile stores caused re-consolidation) and only ever moves forward.
CREATE TABLE fts_sync (
    mirror     TEXT PRIMARY KEY,
    last_rowid INTEGER NOT NULL DEFAULT 0 CHECK (last_rowid >= 0)
) STRICT;

CREATE TRIGGER fts_sync_forward_only BEFORE UPDATE ON fts_sync
WHEN NEW.last_rowid < OLD.last_rowid BEGIN
    SELECT RAISE(ABORT, 'sync markers are forward-only');
END;

CREATE TRIGGER fts_sync_no_delete BEFORE DELETE ON fts_sync BEGIN
    SELECT RAISE(ABORT, 'sync markers are never deleted');
END;

INSERT INTO fts_sync (mirror, last_rowid) VALUES ('wal_fts', 0), ('memories_fts', 0);

-- The embedder slot (§4): content is canonical, vectors are derived data.
-- Each stone's vectors live under its own embedder_id and recall() reads
-- exactly one — vectors from different models are NEVER mixed. The active
-- stone is global_settings['active_embedder'], set only by blessed swap.
CREATE TABLE embeddings (
    memory_id   INTEGER NOT NULL REFERENCES memories (memory_id),
    embedder_id TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (memory_id, embedder_id)
) STRICT;

-- WAL content is canonical too. These vectors are derived and rebuildable,
-- isolated by embedder_id under the same never-mix law as memory vectors.
CREATE TABLE wal_embeddings (
    wal_id      INTEGER NOT NULL REFERENCES wal (wal_id),
    embedder_id TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (wal_id, embedder_id)
) STRICT;

-- A distilled memory retains the WAL rows that gave birth to it. The link is
-- append-only provenance; evidence and warnings may be populated by richer
-- distillers without changing the memory's canonical content.
CREATE TABLE memory_sources (
    memory_id       INTEGER NOT NULL REFERENCES memories (memory_id),
    wal_id          INTEGER NOT NULL REFERENCES wal (wal_id),
    evidence_phrase TEXT,
    warnings_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (memory_id, wal_id)
) STRICT;

CREATE TRIGGER memory_sources_same_agent BEFORE INSERT ON memory_sources
WHEN (SELECT agent_id FROM memories WHERE memory_id = NEW.memory_id)
  <> (SELECT s.agent_id FROM wal w JOIN sessions s ON s.session_id = w.session_id
      WHERE w.wal_id = NEW.wal_id)
BEGIN
    SELECT RAISE(ABORT, 'memory provenance never crosses agents (L4)');
END;
CREATE TRIGGER memory_sources_no_update BEFORE UPDATE ON memory_sources BEGIN
    SELECT RAISE(ABORT, 'memory provenance is append-only (L6)');
END;
CREATE TRIGGER memory_sources_no_delete BEFORE DELETE ON memory_sources BEGIN
    SELECT RAISE(ABORT, 'memory provenance is append-only (L6)');
END;

-- Candidate memory review queue. Inference proposes; the Harness routes; the
-- Owner approves user-global scope. Candidates are durable pending work, not
-- memories until accepted.
CREATE TABLE memory_candidates (
    candidate_id           INTEGER PRIMARY KEY,
    agent_id               INTEGER NOT NULL REFERENCES agents (agent_id),
    claim                  TEXT NOT NULL CHECK (length(claim) > 0),
    route                  TEXT NOT NULL CHECK (route IN ('agent', 'user_review', 'reject')),
    proposed_scope         TEXT NOT NULL CHECK (proposed_scope IN ('agent', 'user_global')),
    category               TEXT,
    triage_json            TEXT NOT NULL DEFAULT '{}',
    status                 TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'accepted', 'rejected')),
    materialized_memory_id INTEGER REFERENCES memories (memory_id),
    resolved_by_wal_id     INTEGER REFERENCES wal (wal_id),
    resolution_reason      TEXT,
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at            TEXT,
    CHECK (status = 'pending' OR resolution_reason IS NOT NULL),
    CHECK (status <> 'accepted' OR materialized_memory_id IS NOT NULL),
    CHECK (status <> 'rejected' OR materialized_memory_id IS NULL)
) STRICT;

CREATE TRIGGER memory_candidates_agent_must_be_active
BEFORE INSERT ON memory_candidates
WHEN (SELECT status FROM agents WHERE agent_id = NEW.agent_id) = 'retired'
BEGIN
    SELECT RAISE(ABORT, 'agent is retired: no new memory candidates');
END;
CREATE TRIGGER memory_candidates_identity_immutable
BEFORE UPDATE OF agent_id, claim, route, proposed_scope, category, triage_json, created_at
ON memory_candidates BEGIN
    SELECT RAISE(ABORT, 'memory candidate identity is immutable');
END;
CREATE TRIGGER memory_candidates_no_delete BEFORE DELETE ON memory_candidates BEGIN
    SELECT RAISE(ABORT, 'memory candidates are resolved, never deleted');
END;
CREATE TRIGGER memory_candidates_no_reopen BEFORE UPDATE OF status ON memory_candidates
WHEN OLD.status <> 'pending' BEGIN
    SELECT RAISE(ABORT, 'resolved memory candidates are immutable');
END;
CREATE TRIGGER memory_candidates_accepted_same_agent
BEFORE UPDATE OF materialized_memory_id ON memory_candidates
WHEN NEW.materialized_memory_id IS NOT NULL
 AND (SELECT agent_id FROM memories WHERE memory_id = NEW.materialized_memory_id) <> NEW.agent_id
BEGIN
    SELECT RAISE(ABORT, 'candidate materialization must stay within its agent');
END;

CREATE TABLE memory_candidate_sources (
    candidate_id INTEGER NOT NULL REFERENCES memory_candidates (candidate_id),
    wal_id       INTEGER NOT NULL REFERENCES wal (wal_id),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (candidate_id, wal_id)
) STRICT;

CREATE TRIGGER memory_candidate_sources_same_agent
BEFORE INSERT ON memory_candidate_sources
WHEN (SELECT agent_id FROM memory_candidates WHERE candidate_id = NEW.candidate_id)
  <> (SELECT s.agent_id FROM wal w JOIN sessions s ON s.session_id = w.session_id
      WHERE w.wal_id = NEW.wal_id)
BEGIN
    SELECT RAISE(ABORT, 'candidate provenance never crosses agents (L4)');
END;
CREATE TRIGGER memory_candidate_sources_no_update
BEFORE UPDATE ON memory_candidate_sources BEGIN
    SELECT RAISE(ABORT, 'candidate provenance is append-only (L6)');
END;
CREATE TRIGGER memory_candidate_sources_no_delete
BEFORE DELETE ON memory_candidate_sources BEGIN
    SELECT RAISE(ABORT, 'candidate provenance is append-only (L6)');
END;

-- Dream markers (§5.3): in the file, forward-only, undeletable (autopsy
-- lesson: watermarks in volatile stores caused re-dream duplication).
CREATE TABLE dream_marks (
    session_id  INTEGER PRIMARY KEY REFERENCES sessions (session_id),
    last_wal_id INTEGER NOT NULL DEFAULT 0 CHECK (last_wal_id >= 0)
) STRICT;

CREATE TRIGGER dream_marks_forward_only BEFORE UPDATE ON dream_marks
WHEN NEW.last_wal_id < OLD.last_wal_id BEGIN
    SELECT RAISE(ABORT, 'dream markers are forward-only');
END;
CREATE TRIGGER dream_marks_no_delete BEFORE DELETE ON dream_marks BEGIN
    SELECT RAISE(ABORT, 'dream markers are never deleted');
END;

CREATE TABLE agent_dream_state (
    agent_id         INTEGER PRIMARY KEY REFERENCES agents (agent_id),
    last_full_wal_id INTEGER NOT NULL DEFAULT 0 CHECK (last_full_wal_id >= 0)
) STRICT;

CREATE TRIGGER agent_dream_state_forward_only BEFORE UPDATE ON agent_dream_state
WHEN NEW.last_full_wal_id < OLD.last_full_wal_id BEGIN
    SELECT RAISE(ABORT, 'dream markers are forward-only');
END;
CREATE TRIGGER agent_dream_state_no_delete BEFORE DELETE ON agent_dream_state BEGIN
    SELECT RAISE(ABORT, 'dream markers are never deleted');
END;
"""


def create_brain(path: Path) -> None:
    """Create a new, empty Brain file carrying the substrate schema.

    Refuses to touch an existing file: an existing Brain is changed only
    after a snapshot (operation.spec §4), never by re-creation.
    """
    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing Brain: {path}")
    conn = _open(path, blessed=True)
    try:
        with conn:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(
                "INSERT INTO brain_meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
    except BaseException:
        conn.close()
        path.unlink(missing_ok=True)  # a failed creation leaves no debris
        raise
    finally:
        conn.close()


def migrate_v8_to_v9(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v8 Brain after creating a mandatory SQLite snapshot.

    Migration is an Owner ritual, never an automatic side effect of connect().
    The old fingerprint is fixed in code so a tampered v8 file cannot use the
    migration path to bless itself into v9.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "8" or _fingerprint(conn) != V8_FINGERPRINT:
            raise BrainIntegrityError(
                "v8 migration refused: source version or fingerprint is not the "
                "verified v8 substrate"
            )

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()

        with conn:
            conn.execute(
                """CREATE TABLE wal_embeddings (
    wal_id      INTEGER NOT NULL REFERENCES wal (wal_id),
    embedder_id TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (wal_id, embedder_id)
) STRICT"""
            )
            conn.execute(
                """CREATE TABLE memory_sources (
    memory_id       INTEGER NOT NULL REFERENCES memories (memory_id),
    wal_id          INTEGER NOT NULL REFERENCES wal (wal_id),
    evidence_phrase TEXT,
    warnings_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (memory_id, wal_id)
) STRICT"""
            )
            conn.execute(
                """CREATE TRIGGER memory_sources_same_agent BEFORE INSERT ON memory_sources
WHEN (SELECT agent_id FROM memories WHERE memory_id = NEW.memory_id)
  <> (SELECT s.agent_id FROM wal w JOIN sessions s ON s.session_id = w.session_id
      WHERE w.wal_id = NEW.wal_id)
BEGIN
    SELECT RAISE(ABORT, 'memory provenance never crosses agents (L4)');
END"""
            )
            conn.execute(
                """CREATE TRIGGER memory_sources_no_update BEFORE UPDATE ON memory_sources BEGIN
    SELECT RAISE(ABORT, 'memory provenance is append-only (L6)');
END"""
            )
            conn.execute(
                """CREATE TRIGGER memory_sources_no_delete BEFORE DELETE ON memory_sources BEGIN
    SELECT RAISE(ABORT, 'memory provenance is append-only (L6)');
END"""
            )
            conn.execute(
                "UPDATE brain_meta SET value = '9' WHERE key = 'schema_version'",
            )
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise BrainIntegrityError(f"v9 migration integrity check failed: {integrity}")
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row[0] != "9":
            raise BrainIntegrityError("v9 migration failed to stamp schema version 9")
    finally:
        conn.close()


def migrate_v9_to_v10(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v9 Brain after creating a mandatory snapshot.

    The schema fingerprint is exact, so this migration builds a fresh current
    Brain and copies canonical table data into it instead of ALTERing in place.
    Derived FTS mirrors are intentionally rebuilt from content later.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v10-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "9":
            raise BrainIntegrityError("v10 migration refused: source is not schema v9")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()
    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            tables = _canonical_copy_tables()
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new, tables)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def migrate_v10_to_v11(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v10 Brain to the current candidate-queue schema.

    As with v9 -> v10, this is an explicit Owner ritual with a mandatory
    snapshot. The migration builds a fresh current Brain and copies canonical
    data. New review-queue tables start empty.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v11-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "10":
            raise BrainIntegrityError("v11 migration refused: source is not schema v10")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()

    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def migrate_v11_to_v12(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v11 Brain to the skill-provenance schema.

    The new skill_sources table starts empty. Existing configured/manual
    skills remain valid, while newly learned skills can now cite WAL outcomes.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v12-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "11":
            raise BrainIntegrityError("v12 migration refused: source is not schema v11")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()

    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def migrate_v12_to_v13(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v12 Brain to the canonical WAL-native schema.

    Transcript WAL remains the raw evidence table. The new WAL-native event,
    link, projection, and replay-report tables start empty and are populated by
    Harness/event bridge code as replayable structured effects.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v13-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "12":
            raise BrainIntegrityError("v13 migration refused: source is not schema v12")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()

    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def migrate_v13_to_v14(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v13 Brain to DB-native model profile bindings."""
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v14-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "13":
            raise BrainIntegrityError("v14 migration refused: source is not schema v13")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()

    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def migrate_v14_to_v15(path: Path, snapshot_path: Path) -> None:
    """Migrate one verified v14 Brain to persona/delegation schema support."""
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    if snapshot_path.exists():
        raise FileExistsError(f"refusing to overwrite migration snapshot: {snapshot_path}")
    if not snapshot_path.parent.is_dir():
        raise FileNotFoundError(f"snapshot directory does not exist: {snapshot_path.parent}")
    temp_path = path.with_name(path.name + ".v15-tmp")
    if temp_path.exists():
        raise FileExistsError(f"refusing to overwrite migration temp file: {temp_path}")

    conn = _open(path, blessed=True)
    try:
        row = conn.execute(
            "SELECT value FROM brain_meta WHERE key = 'schema_version'"
        ).fetchone()
        found = None if row is None else str(row[0])
        if found != "14":
            raise BrainIntegrityError("v15 migration refused: source is not schema v14")

        snapshot = sqlite3.connect(snapshot_path)
        try:
            conn.backup(snapshot)
        except BaseException:
            snapshot.close()
            snapshot_path.unlink(missing_ok=True)
            raise
        finally:
            snapshot.close()
    finally:
        conn.close()

    try:
        create_brain(temp_path)
        old = sqlite3.connect(path)
        new = _open(temp_path, blessed=True)
        try:
            with new:
                new.execute("PRAGMA defer_foreign_keys = ON")
                _copy_canonical_tables_for_migration(old, new)
            _verify_integrity(new)
        finally:
            old.close()
            new.close()

        source = sqlite3.connect(temp_path)
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    temp_path.unlink(missing_ok=True)


def _canonical_copy_tables() -> list[str]:
    return [
        "global_settings",
        "users",
        "agents",
        "sessions",
        "persona_proposals",
        "model_profiles",
        "agent_model_defaults",
        "session_model_bindings",
        "wal",
        "wal_native_events",
        "wal_native_event_links",
        "wal_native_projection_current",
        "wal_native_replay_reports",
        "memories",
        "learning_ledger",
        "memory_links",
        "tags",
        "memory_tags",
        "global_knowledge",
        "global_hooks",
        "global_skills",
        "agent_settings",
        "agent_identity",
        "agent_hooks",
        "skills",
        "skill_sources",
        "secret_refs",
        "embeddings",
        "wal_embeddings",
        "memory_sources",
        "memory_candidates",
        "memory_candidate_sources",
        "dream_marks",
        "agent_dream_state",
    ]


def _copy_canonical_tables(old: sqlite3.Connection, new: sqlite3.Connection) -> None:
    for table in _canonical_copy_tables():
        old_columns = {
            str(row[1])
            for row in old.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not old_columns:
            continue
        new_columns = [
            str(row[1])
            for row in new.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        columns = [column for column in new_columns if column in old_columns]
        if not columns:
            continue
        column_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = old.execute(f"SELECT {column_list} FROM {table}").fetchall()
        new.executemany(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            rows,
        )


def _copy_canonical_tables_for_migration(
    old: sqlite3.Connection,
    new: sqlite3.Connection,
    tables: list[str] | None = None,
) -> None:
    """Copy historical rows during an explicit Owner migration.

    Runtime triggers enforce how new facts are born. Migration has a different
    job: preserve already-born historical rows exactly, then restore the same
    trigger wall and verify the final schema fingerprint before the file can be
    opened normally.
    """

    trigger_rows = new.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    for name, _ in trigger_rows:
        new.execute(f"DROP TRIGGER {name}")
    try:
        if tables is None:
            _copy_canonical_tables(old, new)
        else:
            for table in tables:
                old_columns = {
                    str(row[1])
                    for row in old.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if not old_columns:
                    continue
                new_columns = [
                    str(row[1])
                    for row in new.execute(f"PRAGMA table_info({table})").fetchall()
                ]
                columns = [column for column in new_columns if column in old_columns]
                if not columns:
                    continue
                column_list = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                rows = old.execute(f"SELECT {column_list} FROM {table}").fetchall()
                new.executemany(
                    f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
                    rows,
                )
    finally:
        for _, sql in trigger_rows:
            new.execute(str(sql))


def connect(path: Path, *, blessed: bool = False) -> sqlite3.Connection:
    """Open a connection to an existing Brain.

    blessed=True registers the Owner blessing on this connection only; the
    privilege exists in process memory and dies with the connection (L5).

    Every open — blessed or not — verifies the schema fingerprint and
    version against the substrate's own DDL before handing the connection
    over. A tampered or foreign file raises BrainIntegrityError.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    conn = _open(path, blessed=blessed)
    try:
        _verify_integrity(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _verify_integrity(conn: sqlite3.Connection) -> None:
    actual = _fingerprint(conn)
    expected = _expected_fingerprint()
    if actual != expected:
        raise BrainIntegrityError(
            "Brain schema does not match the blessed substrate — possible tampering. "
            "Work stops until the Owner rules (L12 tripwire pattern). "
            f"Expected fingerprint {expected[:16]}…, found {actual[:16]}…"
        )
    row = conn.execute(
        "SELECT value FROM brain_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None or row[0] != SCHEMA_VERSION:
        found = "missing" if row is None else repr(row[0])
        raise BrainIntegrityError(
            f"Brain schema version mismatch: substrate is {SCHEMA_VERSION!r}, file says {found}."
        )


def _fingerprint(conn: sqlite3.Connection) -> str:
    """SHA-256 over the file's schema (sqlite_master), deterministic by construction."""
    rows = conn.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    text = "\n".join("|".join(row) for row in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@functools.cache
def _expected_fingerprint() -> str:
    """The expectation lives in code, outside the file, where no file-writer
    can reach it: the fingerprint of the substrate's own DDL applied fresh."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA_SQL)
        return _fingerprint(conn)
    finally:
        conn.close()


# Unblessed connections may do work, never construction: every schema-shaped
# action is denied at the substrate (L4). Blessed connections keep full power,
# so migrations remain possible — they arrive by ritual, with code.
_DENIED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_REINDEX,
    }
)


# The FTS mirrors, their shadow tables, and the sync marker: harness-side
# objects, maintained by sync_fts(). Scoped connections may not touch them
# in any shape — the index cannot row-filter and its shadow tables are raw
# indexed content from every agent.
_FTS_PREFIXES = ("wal_fts", "memories_fts", "fts_sync")


def _is_fts_object(name: str | None) -> bool:
    return name is not None and name.startswith(_FTS_PREFIXES)


# FTS5 internally reads PRAGMA data_version (a bare change counter) to detect
# external writes; measured, not assumed. Only the read form, only this name.
_READONLY_PRAGMA_ALLOWLIST = frozenset({"data_version"})


def _pragma_is_allowed(arg1: str | None, arg2: str | None) -> bool:
    return arg1 in _READONLY_PRAGMA_ALLOWLIST and arg2 is None


def _authorize_unblessed(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    source: str | None,
) -> int:
    if action == sqlite3.SQLITE_PRAGMA and _pragma_is_allowed(arg1, arg2):
        return sqlite3.SQLITE_OK
    if action in _DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


# Tables a scoped agent connection may never touch at top level. Access with
# a non-NULL source is a view or trigger defined in the file — and the file's
# schema is fingerprint-verified, while scoped connections are denied all
# CREATE — so every named source is blessed code (L4).
_PROTECTED_TABLES = frozenset(
    {
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
    }
)

_ROW_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
    }
)


def _authorize_scoped(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    source: str | None,
) -> int:
    if action == sqlite3.SQLITE_PRAGMA and _pragma_is_allowed(arg1, arg2):
        return sqlite3.SQLITE_OK
    if action in _DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action in _ROW_ACTIONS and arg1 in _PROTECTED_TABLES and not source:
        return sqlite3.SQLITE_DENY
    if action in _ROW_ACTIONS and _is_fts_object(arg1):
        return sqlite3.SQLITE_DENY  # total blackout, any shape, any source
    return sqlite3.SQLITE_OK


def connect_agent(path: Path, agent_id: int) -> sqlite3.Connection:
    """Open a connection scoped to one agent (§2.2 isolation, criterion A2).

    The connection sees the Brain only through the my_* views, filtered by
    a connection-registered current_agent_id(); the authorizer denies every
    top-level touch of the base tables. The scope is process memory only —
    it dies with the connection, exactly like the blessing.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no Brain at {path}")
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.create_function("owner_blessing", 0, lambda: 0)
        conn.create_function("current_agent_id", 0, lambda: agent_id)
        _verify_integrity(conn)
        row = conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ? AND status = 'active'", (agent_id,)
        ).fetchone()
        if row is None:
            raise AgentScopeError(f"no active agent with id {agent_id}: refusing the scope")
        conn.set_authorizer(_authorize_scoped)
    except BaseException:
        conn.close()
        raise
    return conn


def _open(path: Path, *, blessed: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("owner_blessing", 0, (lambda: 1) if blessed else (lambda: 0))
    if not blessed:
        conn.set_authorizer(_authorize_unblessed)
    return conn


# (mirror name, content table, rowid column) — sync walks these forward.
_FTS_MIRRORS = (
    ("wal_fts", "wal", "wal_id"),
    ("memories_fts", "memories", "memory_id"),
)


def sync_fts(conn: sqlite3.Connection) -> None:
    """Bring every FTS mirror up to date with its content table (harness
    connections). Content tables are append-only with monotonic rowids, so
    this is a forward walk from the marker; index rows and the marker move
    in the same transaction, so a crash or rollback reverts them together —
    the walk is idempotent."""
    for mirror, table, pk in _FTS_MIRRORS:
        last = conn.execute(
            "SELECT last_rowid FROM fts_sync WHERE mirror = ?", (mirror,)
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {pk}, content FROM {table} WHERE {pk} > ? ORDER BY {pk}",  # noqa: S608
            (last,),
        ).fetchall()
        if not rows:
            continue
        conn.executemany(
            f"INSERT INTO {mirror} (rowid, content) VALUES (?, ?)", rows  # noqa: S608
        )
        conn.execute(
            "UPDATE fts_sync SET last_rowid = ? WHERE mirror = ?", (rows[-1][0], mirror)
        )


def check_fts(conn: sqlite3.Connection) -> None:
    """Verify every FTS mirror against its content table (harness
    maintenance). Raises FtsDriftError on disagreement, loudly."""
    for mirror, _, _ in _FTS_MIRRORS:
        try:
            conn.execute(
                f"INSERT INTO {mirror} ({mirror}, rank) VALUES ('integrity-check', 1)"  # noqa: S608
            )
        except sqlite3.DatabaseError as exc:
            raise FtsDriftError(
                f"{mirror} disagrees with its content table: {exc}"
            ) from exc


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild every FTS mirror from canonical content (harness maintenance).
    Content is canonical, the index is derived — a rebuild can never lose
    knowledge (§4). The marker is advanced to the current tip in the same
    transaction."""
    for mirror, table, pk in _FTS_MIRRORS:
        conn.execute(f"INSERT INTO {mirror} ({mirror}) VALUES ('rebuild')")  # noqa: S608
        tip = conn.execute(f"SELECT COALESCE(MAX({pk}), 0) FROM {table}").fetchone()[0]  # noqa: S608
        conn.execute(
            "UPDATE fts_sync SET last_rowid = ? WHERE mirror = ? AND last_rowid < ?",
            (tip, mirror, tip),
        )
