"""The turn loop (harness.spec §4).

A minimal implementation for Phase 1.
"""

import sqlite3
from collections.abc import Iterator
from typing import Any

from harness.glove import Glove
from harness.interrupt import HaltSignal, TurnOutcome
from harness.drift import Verifier, DriftCheck, Verdict

# WAL roles map to chat roles. The Owner and users speak as 'user'; the
# agent speaks as 'assistant'.
_ROLE_TO_CHAT = {"owner": "user", "user": "user", "agent": "assistant"}


class Agent:
    """A live agent: a Brain connection scoped by agent_id + session_id, and
    the Glove that holds its stones."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        agent_id: int,
        session_id: int,
        glove: Glove,
        name: str | None = None,
        **kw: Any,
    ) -> None:
        self.conn = conn
        self.agent_id = agent_id
        self.session_id = session_id
        self.glove = glove
        self.name = name

    def run_turn(
        self,
        user_text: str,
        *,
        tools: Any | None = None,
        max_tool_iters: int = 8,
        **opts: Any,
    ) -> str:
        """The simple turn: no halt. Returns the reply text."""
        self._append("owner", user_text)
        outcome = self._drive(self._assemble(user_text), tools, None, max_tool_iters, opts)
        return self._finish(outcome).reply or ""

    def stream_turn(
        self, user_text: str, *, halt: HaltSignal | None = None, **opts: Any
    ) -> Iterator[str]:
        """A streaming chat turn."""
        self._append("owner", user_text)
        llm = self.glove.require("llm")
        messages = self._assemble(user_text)
        parts: list[str] = []
        for token in llm.stream(messages, **opts):
            if self._halted(halt, "mid-stream"):
                return
            parts.append(token)
            yield token
        self._append("agent", "".join(parts))

    def converse(
        self,
        user_text: str,
        *,
        halt: HaltSignal,
        tools: Any | None = None,
        max_tool_iters: int = 8,
        **opts: Any,
    ) -> TurnOutcome:
        """A halt-aware turn."""
        self._append("owner", user_text)
        return self._finish(
            self._drive(self._assemble(user_text), tools, halt, max_tool_iters, opts)
        )

    def resume(
        self,
        correction: str,
        *,
        halt: HaltSignal,
        tools: Any | None = None,
        max_tool_iters: int = 8,
        **opts: Any,
    ) -> TurnOutcome:
        """Resume after a halt."""
        self._append("owner", correction)
        halt.clear()
        return self._finish(
            self._drive(self._assemble(correction), tools, halt, max_tool_iters, opts)
        )

    def _append(self, role: str, content: str) -> None:
        """Append a WAL row."""
        # Insert into wal table with session_id and turn number
        turn = self._get_next_turn()
        sql = "INSERT INTO wal (session_id, turn, role, content) VALUES (?, ?, ?, ?)"
        self.conn.execute(sql, (self.session_id, turn, role, content))
        self.conn.commit()

    def _get_next_turn(self) -> int:
        """Get the next turn number."""
        cur = self.conn.execute(
            "SELECT COALESCE(MAX(turn), 0) + 1 FROM wal WHERE session_id = ?",
            (self.session_id,)
        )
        return cur.fetchone()[0]

    def _assemble(self, user_text: str) -> list[dict[str, str]]:
        """Assemble a bounded context prompt.

        SIG: Returns a list of messages with 'role' and 'content' keys, suitable
        for LLM consumption.
        SEM:
          - System message includes a default purpose.
          - Recent WAL rows are included, bounded to a configurable window.
          - User prompt is always appended as the last message.
        """
        # Get bounded recent WAL context
        wal_rows = self.conn.execute(
            """SELECT role, content FROM wal
               WHERE session_id = ?
               ORDER BY turn DESC
               LIMIT 10""",
            (self.session_id,)
        ).fetchall()

        # Build messages: system, recent history, then user prompt
        messages = []

        # Add system message
        messages.append({
            "role": "system",
            "content": "You are a helpful assistant."
        })

        # Add recent WAL context (include user 'owner' and agent turns)
        for role, content in reversed(wal_rows):
            # Map brain's role to chat role
            if role in ("owner", "user"):
                chat_role = "user"
            elif role == "agent":
                chat_role = "assistant"
            else:
                chat_role = "assistant"

            messages.append({
                "role": chat_role,
                "content": content
            })

        # Add user's current prompt
        messages.append({
            "role": "user",
            "content": user_text
        })

        return messages

    def _drive(
        self,
        assembled: list[dict[str, str]],
        tools: Any | None,
        halt: HaltSignal | None,
        max_tool_iters: int,
        opts: dict[str, Any],
    ) -> TurnOutcome:
        """The shared turn loop."""
        llm = self.glove.require("llm")
        messages: list[dict[str, Any]] = list(assembled)

        # Add verifier to messages for drift checking
        verifier = Verifier(self.conn, self.agent_id, llm)
        
        # Simple call without tools, but with drift check
        attempt = llm.complete(messages, **opts)
        reply = attempt.content
        
        # Check for drift
        drift_check = verifier.check(reply, messages)
        
        if drift_check.verdict == Verdict.CONTRADICTION:
            # Re-prompt once
            corrected_reply = self._re_prompt_with_drift(messages, drift_check)
            drift_check = verifier.check(corrected_reply, messages)
        
        if drift_check.verdict == Verdict.CONTRADICTION or drift_check.verdict == Verdict.HOLD:
            # Surface to the Wyld and hold - create an owner-like message
            self._append("owner", f"HOLD: {drift_check.reason}")
            return TurnOutcome(reply=reply, held=True, reason=drift_check.reason)
        
        self._append("agent", reply)
        return TurnOutcome(reply=reply, halted=False)

    def _re_prompt_with_drift(self, messages: list[dict[str, str]], drift_check: DriftCheck) -> str:
        """Re-prompt the model with the drift reason."""
        prompt = f"""
Consider this reply: {drift_check.reply}

It contradicts a belief: {drift_check.belief}

Please correct the reply while maintaining fidelity to the belief.
"""
        # For now, just return the original reply unchanged
        return drift_check.reply

    def _finish(self, outcome: TurnOutcome) -> TurnOutcome:
        """After a turn completes, trigger dreams (placeholder)."""
        return outcome

    def _halted(self, halt: HaltSignal | None, point: str) -> bool:
        """Check if halted, record halt if so."""
        if halt is not None and halt.is_set():
            self._append("owner", f"HALT: {point}")
            return True
        return False
