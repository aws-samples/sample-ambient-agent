# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Execution control components for preventing infinite loops and managing agent state
"""

import hashlib
import time
from typing import Dict, List, Set, Any, Optional
import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker to prevent infinite loops and excessive API calls"""

    def __init__(self, max_calls: int = 2, cooldown_seconds: int = 1):
        self.max_calls = max_calls
        self.cooldown_seconds = cooldown_seconds
        self.call_history: Dict[str, List[float]] = {}
        self.blocked_until: Dict[str, float] = {}

    def _get_query_hash(self, query: str) -> str:
        """Generate a hash for the query to track identical calls"""
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]

    def can_execute(self, query: str) -> bool:
        """Check if the query can be executed based on circuit breaker rules"""
        query_hash = self._get_query_hash(query)
        current_time = time.time()

        # Check if currently blocked
        if query_hash in self.blocked_until:
            if current_time < self.blocked_until[query_hash]:
                logger.warning(
                    "Query blocked by circuit breaker",
                    extra={"query_preview": query[:50]},
                )
                return False
            else:
                # Cooldown period has passed, remove block
                del self.blocked_until[query_hash]

        # Check call history
        if query_hash not in self.call_history:
            self.call_history[query_hash] = []

        # Clean old calls (older than 5 minutes)
        self.call_history[query_hash] = [
            call_time
            for call_time in self.call_history[query_hash]
            if current_time - call_time < 300  # 5 minutes
        ]

        # Check if we've exceeded max calls
        if len(self.call_history[query_hash]) >= self.max_calls:
            logger.warning(
                "Circuit breaker triggered", extra={"query_preview": query[:50]}
            )
            self.blocked_until[query_hash] = current_time + self.cooldown_seconds
            return False

        return True

    def record_execution(self, query: str):
        """Record that a query was executed"""
        query_hash = self._get_query_hash(query)
        current_time = time.time()

        if query_hash not in self.call_history:
            self.call_history[query_hash] = []

        self.call_history[query_hash].append(current_time)
        logger.debug("Execution recorded", extra={"query_preview": query[:50]})


class ExecutionState:
    """Track execution state to prevent loops and manage context"""

    def __init__(self):
        self.completed_actions: Set[str] = set()
        self.search_results: Dict[str, Any] = {}
        self.search_history: List[Dict[str, Any]] = []
        self.current_focus: Optional[str] = None
        self.execution_metrics = ExecutionMetrics()
        self.start_time = time.time()

    def _get_query_hash(self, query: str) -> str:
        """Generate a hash for the query to track identical calls"""
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]

    def add_search_result(self, query: str, result: Any):
        """Store search results for reuse"""
        query_hash = hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]
        self.search_results[query_hash] = {
            "query": query,
            "result": result,
            "timestamp": time.time(),
        }

        self.search_history.append(
            {"query": query, "query_hash": query_hash, "timestamp": time.time()}
        )

        self.execution_metrics.search_count += 1
        logger.debug("Search result stored", extra={"query_preview": query[:50]})

    def get_search_result(self, query: str) -> Optional[Any]:
        """Retrieve previous search results if available"""
        query_hash = hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]
        if query_hash in self.search_results:
            logger.info(
                "Reusing previous search result", extra={"query_preview": query[:50]}
            )
            return self.search_results[query_hash]["result"]
        return None

    def has_searched_for(self, query: str) -> bool:
        """Check if we've already searched for this query"""
        query_hash = hashlib.sha256(query.lower().strip().encode()).hexdigest()[:8]
        return query_hash in self.search_results

    def get_previous_searches(self) -> List[str]:
        """Get list of previous search queries"""
        return [search["query"] for search in self.search_history]

    def get_available_info_summary(self) -> str:
        """Get a summary of available information"""
        if not self.search_results:
            return "No previous search results available"

        summaries = []
        for query_hash, data in self.search_results.items():
            query = data["query"]
            # Create a brief summary of the result
            result = data["result"]
            if isinstance(result, dict) and "results" in result:
                result_count = len(result["results"])
                summaries.append(f"- {query}: {result_count} results available")
            else:
                summaries.append(f"- {query}: Information available")

        return "\n".join(summaries)

    def mark_action_completed(self, action: str):
        """Mark an action as completed"""
        self.completed_actions.add(action)
        logger.debug("Action marked as completed", extra={"action": action})

    def is_action_completed(self, action: str) -> bool:
        """Check if an action has been completed"""
        return action in self.completed_actions

    def set_focus(self, focus: str):
        """Set the current focus of the conversation"""
        self.current_focus = focus
        logger.debug("Conversation focus set", extra={"focus": focus})

    def get_execution_summary(self) -> Dict[str, Any]:
        """Get a summary of execution state"""
        return {
            "search_count": len(self.search_history),
            "unique_searches": len(self.search_results),
            "completed_actions": list(self.completed_actions),
            "current_focus": self.current_focus,
            "execution_time": time.time() - self.start_time,
            "previous_searches": self.get_previous_searches(),
        }


class ExecutionMetrics:
    """Track execution metrics for monitoring and optimization"""

    def __init__(self):
        self.search_count = 0
        self.execution_time = 0
        self.loop_detection_count = 0
        self.human_interactions = 0
        self.errors = 0
        self.start_time = time.time()

    def record_loop_detection(self):
        """Record that a loop was detected"""
        self.loop_detection_count += 1
        logger.warning("Loop detected and prevented")

    def record_human_interaction(self):
        """Record a human interaction"""
        self.human_interactions += 1
        logger.debug("Human interaction recorded")

    def record_error(self, error: str):
        """Record an error"""
        self.errors += 1
        logger.error("Error recorded", extra={"error": error})

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        current_time = time.time()
        return {
            "search_count": self.search_count,
            "execution_time": current_time - self.start_time,
            "loop_detection_count": self.loop_detection_count,
            "human_interactions": self.human_interactions,
            "errors": self.errors,
            "search_efficiency": (
                self.search_count / max(1, self.search_count)
                if self.search_count > 0
                else 0
            ),
        }


class LoopDetector:
    """Detect and prevent infinite loops in agent execution"""

    def __init__(self, max_identical_actions: int = 3, window_size: int = 10):
        self.max_identical_actions = max_identical_actions
        self.window_size = window_size
        self.action_history: List[str] = []

    def add_action(self, action: str, input_data: str = "") -> bool:
        """
        Add an action to history and check for loops
        Returns True if loop detected, False otherwise
        """
        action_signature = (
            f"{action}:{hashlib.sha256(input_data.encode()).hexdigest()[:8]}"
        )
        self.action_history.append(action_signature)

        # Keep only recent actions
        if len(self.action_history) > self.window_size:
            self.action_history = self.action_history[-self.window_size :]

        # Check for loops in recent history
        if len(self.action_history) >= self.max_identical_actions:
            recent_actions = self.action_history[-self.max_identical_actions :]
            if len(set(recent_actions)) == 1:  # All actions are identical
                logger.warning(
                    "Loop detected in action history",
                    extra={
                        "action": action,
                        "repeat_count": self.max_identical_actions,
                    },
                )
                return True

        return False

    def reset(self):
        """Reset the loop detector"""
        self.action_history.clear()
