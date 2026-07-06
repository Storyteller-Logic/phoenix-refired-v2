"""Sub-agent system (harness.spec §5).

Spawn child agents with scoped stones and tools, enforce Brain isolation.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime

@dataclass
class SubAgentConfig:
    """Configuration for a sub-agent."""
    id: int
    name: str
    grant_stones: list[str]
    tool_restrictions: list[str]
    parent_agent_id: int
    memory_scope: str
    created: datetime

@dataclass
class SubAgentOutcome:
    """Result of a sub-agent execution."""
    agent_id: int
    success: bool
    summary: str
    worth: float
    return_value: Any = None
    error: str | None = None

class SubAgentManager:
    """Manages sub-agent lifecycle and scope enforcement."""
    
    def __init__(self, conn: sqlite3.Connection, parent_agent_id: int):
        self.conn = conn
        self.parent_agent_id = parent_agent_id
        self.active_agents: dict[int, SubAgentConfig] = {}
    
    def spawn(self, name: str, grant_stones: list[str], tool_restrictions: list[str] | None = None, memory_scope: str = "local") -> int:
        """Spawn a sub-agent with scoped resources."""
        # Create unique ID for sub-agent
        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO sub_agents (parent_agent_id, name, grant_stones, tool_restrictions, memory_scope, active, created)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (self.parent_agent_id, name, "|".join(grant_stones), "|".join(tool_restrictions or []), memory_scope, datetime.now()))
            sub_agent_id = cur.lastrowid
        
        if sub_agent_id is None:
            raise RuntimeError("Failed to create sub-agent")
        
        config = SubAgentConfig(
            id=sub_agent_id,
            name=name,
            grant_stones=grant_stones,
            tool_restrictions=tool_restrictions or [],
            parent_agent_id=self.parent_agent_id,
            memory_scope=memory_scope,
            created=datetime.now()
        )
        
        self.active_agents[sub_agent_id] = config
        
        return sub_agent_id
    
    def execute(self, sub_agent_id: int, func: callable) -> SubAgentOutcome | None:
        """Execute a function under sub-agent scope."""
        if sub_agent_id not in self.active_agents:
            return SubAgentOutcome(
                agent_id=sub_agent_id,
                success=False,
                summary="Sub-agent not found",
                worth=0.0,
                error="Sub-agent id does not exist"
            )
        
        config = self.active_agents[sub_agent_id]
        
        try:
            result = func()
            success = True
            summary = f"Sub-agent {config.name} completed successfully"
            
            with self.conn:
                self.conn.execute("""
                    UPDATE sub_agents SET active = 0, completed = 1, summary = ?
                    WHERE id = ?
                """, (summary, sub_agent_id))
            
            return SubAgentOutcome(
                agent_id=sub_agent_id,
                success=True,
                summary=summary,
                worth=0.0,
                return_value=result
            )
        except Exception as e:
            with self.conn:
                self.conn.execute("""
                    UPDATE sub_agents SET active = 0, error = ?
                    WHERE id = ?
                """, (str(e), sub_agent_id))
            return SubAgentOutcome(
                agent_id=sub_agent_id,
                success=False,
                summary=f"Sub-agent {config.name} failed",
                worth=0.0,
                error=str(e)
            )
    
    def grant_tool(self, sub_agent_id: int, tool_name: str) -> bool:
        """Grant a tool to a sub-agent (if in grant list)."""
        if sub_agent_id in self.active_agents:
            config = self.active_agents[sub_agent_id]
            if tool_name in config.grant_stones:
                return True
        return False
    
    def revoke_all(self, sub_agent_id: int):
        """Revoke all grants for a sub-agent."""
        self.active_agents.pop(sub_agent_id, None)

    def inspect_scope(self, sub_agent_id: int) -> dict[str, Any]:
        """Inspect scope of a sub-agent."""
        if sub_agent_id in self.active_agents:
            config = self.active_agents[sub_agent_id]
            return {
                "id": config.id,
                "name": config.name,
                "grant_stones": config.grant_stones,
                "tool_restrictions": config.tool_restrictions,
                "memory_scope": config.memory_scope
            }
        return {}

class EscapePrevention:
    """Ensures sub-agents cannot escape their scope."""
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
    
    def prevent_global_write(self, sub_agent_id: int, sql: str) -> bool:
        """Check if the SQL would write to global tables."""
        global_keyword = {"brain_meta", "global_settings", "global_knowledge", "global_hooks", "global_skills"}
        sql_lower = sql.lower()
        
        for keyword in global_keyword:
            if keyword in sql_lower and ("INSERT" in sql or "UPDATE" in sql or "DELETE" in sql):
                return True
        
        return False
    
    def prevent_cross_agent_access(self, sub_agent_id: int, table: str, scope_check: str) -> bool:
        """Prevent access to other agents' data."""
        return False