# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Execution-control primitives for the agent:

- `ExecutionState` tracks per-session search/action history and metrics.
- `LoopDetector` flags when the same action signature repeats inside a
  sliding window.
- `CircuitBreaker` blocks repeated identical queries within a cooldown.
- `current_execution_state` is a ContextVar that the orchestrator sets at
  the start of every `invoke()` so tools read the right session's state
  without requiring the factory to be mutated between calls.
"""

import contextvars
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


current_execution_state: "contextvars.ContextVar[Optional[ExecutionState]]" = (
    contextvars.ContextVar("current_execution_state", default=None)
)


class CircuitBreaker:
    """Blocks identical queries that repeat within a short cooldown window."""

    def __init__(self, max_calls: int = 2, cooldown_seconds: int = 1):
        self.max_calls = max_calls
        self.cooldown_seconds = cooldown_seconds
        self.call_history: Dict[str, List[float]] = {}
        self.blocked_until: Dict[str, float] = {}

    @staticmethod
    def _query_hash(query: str) -> str:
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]

    def can_execute(self, query: str) -> bool:
        query_hash = self._query_hash(query)
        now = time.time()

        if query_hash in self.blocked_until:
            if now < self.blocked_until[query_hash]:
                logger.warning(
                    "Query blocked by circuit breaker",
                    extra={"query_preview": query[:50]},
                )
                return False
            del self.blocked_until[query_hash]

        history = self.call_history.setdefault(query_hash, [])
        # Prune entries older than 5 minutes.
        self.call_history[query_hash] = [t for t in history if now - t < 300]

        if len(self.call_history[query_hash]) >= self.max_calls:
            logger.warning(
                "Circuit breaker triggered",
                extra={"query_preview": query[:50]},
            )
            self.blocked_until[query_hash] = now + self.cooldown_seconds
            return False
        return True

    def record_execution(self, query: str) -> None:
        query_hash = self._query_hash(query)
        self.call_history.setdefault(query_hash, []).append(time.time())


class ExecutionMetrics:
    """Per-session counters for observability."""

    def __init__(self):
        self.search_count = 0
        self.execution_time = 0.0
        self.loop_detection_count = 0
        self.human_interactions = 0
        self.errors = 0
        self.start_time = time.time()

    def record_loop_detection(self) -> None:
        self.loop_detection_count += 1

    def record_human_interaction(self) -> None:
        self.human_interactions += 1

    def record_error(self, error: str) -> None:
        self.errors += 1
        logger.error("Error recorded", extra={"error": error})

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "search_count": self.search_count,
            "execution_time": time.time() - self.start_time,
            "loop_detection_count": self.loop_detection_count,
            "human_interactions": self.human_interactions,
            "errors": self.errors,
        }


class ExecutionState:
    """Per-session execution state including search-result cache."""

    def __init__(self):
        self.completed_actions: Set[str] = set()
        self.search_results: Dict[str, Any] = {}
        self.search_history: List[Dict[str, Any]] = []
        self.current_focus: Optional[str] = None
        self.execution_metrics = ExecutionMetrics()
        self.start_time = time.time()

    @staticmethod
    def _query_hash(query: str) -> str:
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]

    def add_search_result(self, query: str, result: Any) -> None:
        query_hash = self._query_hash(query)
        self.search_results[query_hash] = {
            "query": query,
            "result": result,
            "timestamp": time.time(),
        }
        self.search_history.append(
            {"query": query, "query_hash": query_hash, "timestamp": time.time()}
        )
        self.execution_metrics.search_count += 1

    def get_search_result(self, query: str) -> Optional[Any]:
        item = self.search_results.get(self._query_hash(query))
        if item:
            return item["result"]
        return None

    def has_searched_for(self, query: str) -> bool:
        return self._query_hash(query) in self.search_results

    def get_previous_searches(self) -> List[str]:
        return [s["query"] for s in self.search_history]

    def get_available_info_summary(self) -> str:
        if not self.search_results:
            return "No previous search results available"
        lines = []
        for data in self.search_results.values():
            result = data["result"]
            if isinstance(result, dict) and "results" in result:
                lines.append(f"- {data['query']}: {len(result['results'])} results available")
            else:
                lines.append(f"- {data['query']}: Information available")
        return "\n".join(lines)

    def mark_action_completed(self, action: str) -> None:
        self.completed_actions.add(action)

    def is_action_completed(self, action: str) -> bool:
        return action in self.completed_actions

    def set_focus(self, focus: str) -> None:
        self.current_focus = focus

    def get_execution_summary(self) -> Dict[str, Any]:
        return {
            "search_count": len(self.search_history),
            "unique_searches": len(self.search_results),
            "completed_actions": list(self.completed_actions),
            "current_focus": self.current_focus,
            "execution_time": time.time() - self.start_time,
            "previous_searches": self.get_previous_searches(),
        }


class LoopDetector:
    """Flag repeated identical action signatures in a sliding window."""

    def __init__(self, max_identical_actions: int = 3, window_size: int = 10):
        self.max_identical_actions = max_identical_actions
        self.window_size = window_size
        self.action_history: List[str] = []

    def add_action(self, action: str, input_data: str = "") -> bool:
        signature = f"{action}:{hashlib.sha256(input_data.encode()).hexdigest()[:8]}"
        self.action_history.append(signature)

        if len(self.action_history) > self.window_size:
            self.action_history = self.action_history[-self.window_size:]

        if len(self.action_history) >= self.max_identical_actions:
            recent = self.action_history[-self.max_identical_actions:]
            if len(set(recent)) == 1:
                logger.warning(
                    "Loop detected in action history",
                    extra={"action": action, "repeat_count": self.max_identical_actions},
                )
                return True
        return False

    def reset(self) -> None:
        self.action_history.clear()
