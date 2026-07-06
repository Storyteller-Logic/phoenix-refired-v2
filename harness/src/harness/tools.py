"""Tools, the gate, and dispatch (harness.spec §5).

A model can REQUEST a tool call; only the harness can RUN one. Dispatch is
the single path from request to execution, and the gate sits in that path
(L4): destructive tools are refused unless the agent's Brain settings grant
them — enforced here, outside the model's reach, with no dev bypass (the
autopsy where a free-for-all session deleted live commands).

Tool results come from the harness, never the model's text. A model
narrating a tool call it never made produces no ToolResult; only a real
dispatch does. Narration can therefore never enter the record as a result
(the autopsy where a model hallucinated tool output).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class UnknownTool(KeyError):
    """A requested tool name is not in the registry — loud, never silent."""


class ToolDenied(PermissionError):
    """A destructive tool was requested without a grant. Refused at the gate
    before the callable is ever reached (L4)."""


@dataclass(frozen=True)
class Tool:
    name: str
    fn: Callable[..., Any]
    tags: set[str] = field(default_factory=set)
    destructive: bool = False
    description: str = ""
    parameters: dict[str, Any] | None = None

    def spec(self) -> dict[str, Any]:
        """The OpenAI tool/function spec handed to an inference stone."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """A model's REQUEST to run a tool. A request, not an execution."""

    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """The harness's record of a REAL execution. The only thing that may
    enter the transcript as a tool result."""

    tool: str
    ran: bool
    output: Any


class FabricationDetector:
    """Detects when a model narrates tool calls that were never dispatched."""

    def __init__(self):
        self.dispatched: list[ToolCall] = []

    def record_dispatch(self, call: ToolCall) -> None:
        """Track a tool call that was actually dispatched."""
        self.dispatched.append(call)

    def check_narration(self, messages: list[dict]) -> list[str]:
        """
        Given assistant messages, returns a list of tool call references
        that appear in narration but were not dispatched.
        """
        # Extract the assistant message content
        assistant_content = []
        for msg in messages:
            if msg.get("role") == "assistant":
                assistant_content.append(msg["content"])

        if not assistant_content:
            return []

        full_text = " ".join(assistant_content)

        # Simple approach: look for patterns like "call <tool>(<args>)" or "invoke <tool>"
        # Real implementation would be more sophisticated
        fabricated = []

        # We look for "create\nannounced.completion" and other patterns
        import re

        # Pattern for any text that looks like a tool call reference
        # Match keywords: call, invoke, run, execute, request (with optional suffixes)
        pattern = r"\b(?:call|invoke|run|execute|request)(?:s|ed)?\s+([a-zA-Z_][a-zA-Z0-9_]*)\b"
        matches = re.findall(pattern, full_text.lower())

        for tool_name in matches:
            # Check if this exact call is in dispatched (by name only)
            found = any(dispatched.name == tool_name for dispatched in self.dispatched)
            if not found:
                fabricated.append(tool_name)

        return fabricated


class ToolRegistry:
    """Tools registered by name, each with tags and a destructive flag."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise UnknownTool(f"no tool named {name!r}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI tool specs for every registered tool."""
        return [tool.spec() for tool in self._tools.values()]


class ToolGate:
    """Per-agent tool policy, read from the Brain. Non-destructive tools are
    allowed; a destructive tool needs an explicit grant
    (`tool.grant.<name> = 'allow'`) in the agent's settings. The grant lives
    in the Brain, durable, outside what the model edits during a turn.

    The gate keys on the agent_id it is constructed with — and ONLY that.
    A sub-agent's gate uses the SUB-AGENT's agent_id, so the parent's grants
    do NOT transfer to a child: a child runs a destructive tool only if the
    grant is in the CHILD's settings. (Verified: tests/test_wyrm_gemma.py.)"""

    def __init__(self, conn: sqlite3.Connection, agent_id: int) -> None:
        self._conn = conn
        self._agent_id = agent_id

    def allows(self, tool: Tool) -> bool:
        if not tool.destructive:
            return True
        row = self._conn.execute(
            "SELECT value FROM agent_settings WHERE agent_id = ? AND key = ?",
            (self._agent_id, f"tool.grant.{tool.name}"),
        ).fetchone()
        return row is not None and str(row[0]) == "allow"


def dispatch(registry: ToolRegistry, gate: ToolGate, call: ToolCall) -> ToolResult:
    """The ONLY path from a requested tool call to execution. Unknown tools
    fail loud; ungranted destructive tools are denied before the callable
    runs; allowed tools run and the harness captures the real result. There
    is no dev/bypass/force parameter — the gate holds in every mode."""
    tool = registry.get(call.name)  # UnknownTool if absent
    if not gate.allows(tool):
        raise ToolDenied(
            f"tool {tool.name!r} is destructive and not granted to this agent; "
            "grant it in the Brain to allow it"
        )
    output = tool.fn(**call.args)
    return ToolResult(tool=tool.name, ran=True, output=output)
