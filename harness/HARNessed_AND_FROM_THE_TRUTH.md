# The Harness: Complete Understanding from Phoenix Brain & Truth Specs

## Executive Summary

The Harness is the runtime body for the Brain (a portable SQLite database). It owns the turn loop, model slots, tool execution, gates, drift checks, dreams, sub-agents, and interruption. The Brain is native to the loop — not an MCP add-on, prompt nudge, hook trick, or sidecar memory.

## Core Philosophy

- **The System Wants to Correctly Serve the Owner**: Not to "win" or "show up" the owner.
- **CORRECT work, not COMPLETE work**: If it isn't correct, it isn't complete.
- **Verification through adversarial testing**: Build → adversarial pass → independent model reviews → fresh re-run → converge.
- **Failure modes from autopsies**: Every design encodes lessons from past failures.

## The Six Inversions (Rejections of Claude Code)

1. **No .md identity files** — Identity served live from the Brain every turn.
2. **No .jsonl transcripts** — The WAL in the Brain is the only transcript.
3. **No compaction tax** — Full transcript never fed back; there is no compact event.
4. **A dedicated interface** — CLI walk and Web UI are the doors.
5. **Per-agent identities** — Not a single global ownership model.
6. **Deliberate startup** — Model → Agent → Session → Chat.

## The Infinity Glove: Model Slots

- **Slots by type**: LLM (inference), Embeddings, and future types (vision, image gen, speech).
- **Every slot is swappable** mid-project without losing durable state.
- **Standard interfaces**: OpenAI-compatible for inference; defined interfaces for others.
- **Local-first**: Every slot accepts owned hardware (llama.cpp / vLLM). Vendor stones are optional.
- **Minimum stones**: LLM + embedder. Missing optional stones degrade gracefully.
- **Saved named profiles + "New" option** — Any OpenAI-compatible endpoint or vendor API token.
- **Per-agent preference**: Agent's settings may name preferred stones.

## The Turn Loop (Context-Window-Free)

**Each turn the model receives:**
- The last 5–10 WAL messages + the user's prompt
- Relevant memories (recall, worth-sorted) + the agent's identity, hooks, and active skills
- Everything else reachable by tools: `search()` over the WAL and Brain

**Turn order, every turn, crash-safe:**
1. User prompt → **WAL append** (durable before anything else)
2. Assemble: recent WAL slice + recall + identity/hooks/skills from the Brain
3. Inference (the LLM stone)
4. Tool dispatch loop — bounded iterations, results from the harness only
5. Reply → **WAL append**
6. Learning events recorded (recall traces, reinforcements, outcomes)
7. Dream check — silent, background, never blocking

**Interruption — day one requirement:**
- Owner can **halt** at any point mid-turn
- Halt recorded in WAL, in-flight tool calls stopped before side effects
- Loop resumes only when correction has been given and injected
- A halted turn is a normal turn, not an error

## Tools and Gates

- **Tool registry**: every tool registered with tags
- **Destructive tools (write, edit, shell) are gated** per agent settings
- **Gate enforced at dispatch** — outside the model's reach
- **Tool results come from the harness, never from the model's text**
- Every tool call and result lands in the WAL; outcomes feed the learning law
- **Web fetch and web search** are first-class tools; results are knowledge candidates

## Drift Control

- **Identity re-injection**: who the agent is, hooks and governing knowledge, come from the Brain every turn
- **Spec anchoring**: the active task and its acceptance criteria are re-injected each turn
- **Verification**: after an answer, the harness checks it against the agent's highest-worth governing beliefs
- **On contradiction**: re-prompt once with conflict surfaced; if persists, **surface to the Wyld and hold** — never silently proceed
- **Lesson surfacing**: when context matches a recorded failure, the lesson is injected before the model acts

## Dreams (Harness Side)

- **Harness owns triggers**, Brain owns the law
- **Cadence counters per session**
- **Execution silent and in background** using an inference stone
- **Never blocks conversation**
- **Agent-requested and Wyld-requested dreams honored immediately**
- **Markers forward-only and idempotent**

## Sub-agents (Harness Side)

- **Parent agent requests a sub-agent**
- **Harness instantiates with bounded loop**, only tools and stones parent grants
- **Scope entirely inside parent**
- **Results return to parent**
- **Sub-agent transcript lands in parent's WAL scope**
- **Substrate makes escape impossible**, not forbidden

## Acceptance Criteria (ALL must pass)

1. **Local-only boot**: no vendor reachable, boots on owned stones, completes real work end to end
2. **Stone-swap drill**: swap LLM mid-project; next turn continues with full identity+memory, nothing durable lost
3. **No-compaction proof**: 200+ turn session, bounded per-turn prompt, all history via search
4. **Identity proof**: fresh session answers who/what/doing from Brain alone
5. **Gate proof**: ungranted destructive tool cannot execute even when model tries
6. **Fabrication proof**: model narrating unexecuted tool call is caught
7. **Drift drill**: misleading prompts surface contradiction instead of complying
8. **Dream silence**: dreams run during live conversation with zero blocking; idempotent
9. **Sub-agent escape suite**: adversarial sub-agent cannot write global/touch another agent/exceed tools
10. **Interrupt drill**: halt mid-inference and mid-dispatch, clean, WAL-recorded, no orphans, resumes with correction
11. **The Parity Gate**: same model, same tasks — Harness ≥ Claude Code

## Mission Trip Gaps (Wiring Needed)

From harness PROGRESS.md, several items are marked PARTIAL or "to wire":

### H1: Context-Window-Free Turn Loop
- Need to wire hooks/skills injection
- Need to wire dream check into the loop

### H4: Six Inversions
- Need to ensure no .md identity files (lazarus progress references brand new identity_not_moded_files)
- Other inversions marked but not fully implemented

### A1: Local-only boot
- Needs full end-to-end boot with turn loop

### A2: Stone-swap drill
- Needs integration with turn loop + Brain integration

### A3: No-compaction proof
- Needs 200+ turn live session

### A4: Identity proof
- Needs fresh session answering live

### A7: Drift drill
- Needs real NLI/LLM verifier stone wired at integration

### A8: Dream silence
- Needs with real inference stone distilling during live multi-turn session

### A11: Parity Gate (L9)
- The final acceptance gate — requires harness specification

## From Projects/Harness Specification (Gideion Labs)

This is a **different specification** for an orchestration system managing multiple agents, with:

### Core Requirements

**Agent Wake-Up Sequence** (Yuki 20006)
- Assemble seed JSON from identity, active task, running_context, memories, personal skills
- Deliver as single JSON, dormancy only by explicit task assignment
- Three states: dormant, dreaming, active

**Heartbeat Emission and Monitoring** (Yuki)
- Key `agent:heartbeat:{agent_id}` to Redis with 30s TTL, refreshed every 15s
- Harness monitors heartbeat for all agents with tasks in `in_progress`
- Heartbeat expiry on in_progress task → proceed to freeze check

**Load Failure Detection and Retry** (Yuki)
- Attempt 1 → if fail, attempt 2 immediately
- Attempt 2 success → flag Elena (late arrival)
- Attempt 2 fail → halt signal to Elena

**Wrong-Agent Idle Detection** (Yuki)
- ACK from agent_id that does not match assigned_to → treat as idle

**Five-Minute ACK Timeout Monitor** (Yuki)
- No ACK or clarification within 300 seconds → write `ack_timeout` notification

**Freeze Check on Heartbeat Expiry** (Yuki)
- Check `agent:state:{agent_id}`
- State = dreaming → heartbeat expiry expected, wait for active state
- State = active → freeze notification
- State key absent → treat as freeze

**Dream Trigger Logic** (Yuki)
- Monitor context pressure per active agent
- When agent signals approaching context limit:
  - Verify `running_context` is non-null
  - Set state = dreaming
  - Trigger mid-session dream
  - On completion: set state = active, advance watermark
- End-session dream triggered by task complete, idle window, or explicit session close

**Pre-Dream running_context Flush** (Yuki)
- Before any dream, verify `running_context` is non-null on active task
- If NULL, block dream trigger and signal agent to write it

**2048 Token Enforcement** (Yuki or Felix)
- Every write to tasks, memories, skills, agent_skills, and ACK messages must pass token count
- Exceed 2048 tokens → reject

### ACK and Message Format (Felix 20003)

**Required fields:**
- `agent_id`
- `task_id`
- `type`: `beginning_work` or `needs_clarification`
- `summary`
- `plan` (ordered list, beginning_work only)
- `gaps` (list with resolution and bridged_by)
- `questions` (list, needs_clarification only)
- `confidence` (float 0.0–1.0)
- `timestamp`

**Logic:**
- `plan` non-empty on beginning_work
- `questions` non-empty on needs_clarification
- `summary + plan` combined must not exceed 2048 tokens

### Approval Model Integration (Elena 10002)

**Inbox Monitor**:
- Read from `elena:inbox` Redis Stream
- Handler behavior per message type

**Clarification Response Format**:
- Fields: `task_id`, `type`, `answered_questions`, `additional_context`, `remaining_gaps`, `timestamp`

**Review Gate**:
- When agent sets status = review, harness notifies Elena
- Elena reads task record and delivery artifact
- If approved: UPDATE status to approved then done, notify agent
- If rejected: UPDATE status to in_progress, write rejection message

## The Archive (L8)

The old systems are autopsies, not ancestors — nothing is migrated by default unless explicitly approved. Build must be greenfield.

## Creative Tension: Phoenix Brain Specs vs. Gideion Labs Harness

The **blessed specs in `/mnt/hdd/truth/`** (`harness.spec.md`, `brain.spec.md`, `operation.spec.md`) define the core system we've been building.

The **Gideion Labs Harness** in `/mnt/hdd/projects/Harness/` appears to be an **orchestration layer** that uses Phoenix Brain as its memory substrate. It focuses on:

1. **Multiple agents** in a company structure
2. **Approval workflows** with Elena as human-in-the-loop
3. **Redis Streams** for messaging
4. **Scope management** across parent/sub-agent hierarchies

## Summary

The Harness is the runtime body for the Brain. It runs agents outside Claude Code with the Brain built in, not bolted on. It holds the models, runs the turns, injects the knowledge, enforces the laws, and never thinks for the agent.

**Ownership**: built for a single owner — the Wyld — who alone holds global authority.

**Parity Gate**: The final acceptance is that the Harness performs as well as or better than Claude Code on the Owner's real tasks.

