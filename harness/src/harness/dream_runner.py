"""Dream infrastructure (harness.spec §4).

Background synthesis and reflection without blocking active turns.
"""

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from datetime import datetime, timedelta
import heapq
import logging

@dataclass(order=True)
class DreamTask:
    """A scheduled dream task."""
    due: datetime
    id: int
    name: str
    type: str
    priority: int = 0
    completed: bool = False
    result: Any = None

@dataclass
class DreamResult:
    """Result of a dream run."""
    task_id: int
    summary: str
    lessons: list[str]
    worth: float
    timestamp: datetime

class DreamRunner:
    """Runs dreams in background without blocking turns."""
    
    def __init__(self, conn: sqlite3.Connection, model_supplier: Any | None = None):
        self.conn = conn
        self.model_supplier = model_supplier
        self.dream_queue: list[tuple[datetime, int, DreamTask]] = []
        self.running = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._next_id = 0
        self.logger = logging.getLogger(__name__)
        
    def schedule(self, name: str, dream_type: str, due_in_minutes: int = 30) -> int:
        """Schedule a dream task."""
        with self._lock:
            self._next_id += 1
            task = DreamTask(
                due=datetime.now() + timedelta(minutes=due_in_minutes),
                id=self._next_id,
                name=name,
                type=dream_type
            )
            heapq.heappush(self.dream_queue, (task.due, task.id, task))
            try:
                self._save_task(task)
            except Exception as e:
                self.logger.error(f"Failed to save dream task: {e}")
            return task.id
    
    def run_dream(self, task: DreamTask) -> DreamResult | None:
        """Run a single dream task (background)."""
        try:
            with self._lock:
                task.completed = True
            
            # Get recent context
            with self.conn:
                cursor = self.conn.execute("""
                    SELECT role, content FROM wal_entries 
                    WHERE agent_id = ?
                    ORDER BY timestamp DESC, sequence DESC
                    LIMIT 50
                """, (task.id,))
                context = [{"role": role, "content": content} for role, content in cursor.fetchall()]
            
            # Minimal synthesis - in production, use Gemma Heretic
            summary = f"Synthesized {len(context)} messages for {task.name}"
            lessons = ["Dream completed", f"Context size: {len(context)}"]
            worth = 0.0
            
            result = DreamResult(
                task_id=task.id,
                summary=summary,
                lessons=lessons,
                worth=worth,
                timestamp=datetime.now()
            )
            
            try:
                self._save_result(task.id, result)
            except Exception as e:
                self.logger.error(f"Failed to save dream result: {e}")
            return result
            
        except Exception as e:
            self.logger.error(f"Dream {task.id} failed: {e}")
            try:
                self._log_error(task.id, str(e))
            except Exception as log_err:
                pass  # Don't fail on log error
            return None
    
    def start_background_loop(self):
        """Start background dream processor."""
        self.running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the background loop."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5.0)
    
    def _background_loop(self):
        """Background loop for dream tasks."""
        while self.running:
            with self._lock:
                due_tasks = []
                while self.dream_queue and self.dream_queue[0][0] <= datetime.now():
                    due_tasks.append(heapq.heappop(self.dream_queue)[2])
                
                # Process tasks in priority order (higher priority first)
                due_tasks.sort(key=lambda t: -t.priority)
                for task in due_tasks:
                    if not self.running:
                        break
                    if task.completed:
                        continue
                    result = self.run_dream(task)
                    if result:
                        task.result = result
            
            time.sleep(5.0)
    
    def _save_task(self, task: DreamTask):
        """Save dream task to database."""
        try:
            with self.conn:
                self.conn.execute("""
                    INSERT INTO dream_tasks (id, agent_id, name, type, due_at, created_at, completed)
                    VALUES (?, 1, ?, ?, ?, ?, 0)
                """, (task.id, task.name, task.type, task.due, datetime.now()))
        except Exception as e:
            self.logger.error(f"Failed to save dream task {task.id}: {e}")
    
    def _save_result(self, task_id: int, result: DreamResult):
        """Save dream result to database."""
        try:
            with self.conn:
                lessons_str = "|".join(result.lessons)
                self.conn.execute("""
                    INSERT INTO dream_results (task_id, summary, lessons, worth, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (task_id, result.summary, lessons_str, result.worth, result.timestamp))
        except Exception as e:
            self.logger.error(f"Failed to save dream result {task_id}: {e}")
    
    def _log_error(self, task_id: int, error: str):
        """Log an error for a dream task."""
        try:
            with self.conn:
                self.conn.execute("""
                    INSERT INTO dream_errors (task_id, error, timestamp)
                    VALUES (?, ?, ?)
                """, (task_id, error, datetime.now()))
        except Exception as e:
            self.logger.error(f"Failed to log error: {e}")