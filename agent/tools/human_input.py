# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Human input tool - Allows the agent to request clarification from humans.

Background:
-----------
With the legacy LangChain `AgentExecutor`, raising an exception from a tool
would propagate up to the agent loop and could be caught by the invoker to
signal that human input was required. With the modern `create_agent`
(LangGraph-based) approach, exceptions raised inside a tool are captured by
the tool node, wrapped as a `ToolMessage`, and fed back to the model as an
error observation. That means we can no longer use an exception as a control
signal to interrupt execution cleanly.

Instead, the tool now returns a structured sentinel string. The orchestrator
in `core.agent_core` inspects each tool message after invocation; if it finds
this sentinel, it treats the job as interrupted and surfaces the question to
the platform.
"""

# Sentinel prefix the orchestrator uses to detect a human-input request in
# tool output. Keep this stable; changing it will break interrupt detection.
HUMAN_INPUT_SENTINEL = "__HUMAN_INPUT_REQUIRED__::"


class HumanInputRequiredException(Exception):
    """Retained for backward compatibility.

    The orchestrator still catches this exception if it bubbles up (e.g., from
    callers outside the agent graph), but the preferred signal within the
    agent graph is the sentinel string returned by `ask_human_wrapper`.
    """

    def __init__(self, question: str):
        self.question = question
        super().__init__(f"Human input required: {question}")


def create_human_input_tool_func(execution_state=None):
    """
    Create the human input tool function for LangChain / LangGraph.

    This tool lets the agent request clarification or additional information
    from a human user. When called, it returns a sentinel string that the
    orchestrator recognizes and uses to stop the agent loop and forward the
    question to the platform.

    Args:
        execution_state: Optional execution state used to track metrics.

    Returns:
        Callable suitable for wrapping as a LangChain `Tool`.
    """

    def ask_human_wrapper(question: str) -> str:
        """Request input or clarification from a human user.

        Use this tool when you need:
        - Clarification on ambiguous requests
        - Additional information not available through other tools
        - User preferences or choices
        - Confirmation before taking important actions

        The question should be clear, specific, and easy for the user to
        answer.

        Args:
            question: Clear, specific question to ask the human user.

        Returns:
            A sentinel string containing the question. The orchestrator
            interprets this as an interrupt request and will not feed the
            result back to the model.
        """
        if execution_state is not None:
            execution_state.execution_metrics.record_human_interaction()

        # Return a sentinel string. The orchestrator detects this prefix and
        # converts it into an interrupted response for the platform.
        return f"{HUMAN_INPUT_SENTINEL}{question}"

    return ask_human_wrapper
