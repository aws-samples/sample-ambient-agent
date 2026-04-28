# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Human input tool - request clarification from a human user.

The tool returns a sentinel string that the orchestrator in
`core.agent_core` detects after the LangGraph run finishes. A tool cannot
raise to signal an interrupt here because LangGraph's tool node captures
exceptions as error observations and feeds them back to the model.
"""

from core.execution_control import current_execution_state

# Stable sentinel; changing it will break interrupt detection.
HUMAN_INPUT_SENTINEL = "__HUMAN_INPUT_REQUIRED__::"


def create_human_input_tool_func():
    """Create the ask_human tool function."""

    def ask_human_wrapper(question: str) -> str:
        """Request input or clarification from a human user.

        Use this tool when you need clarification on an ambiguous request,
        a choice between options, or confirmation before a high-stakes
        action. Return a single clear, specific question.
        """
        state = current_execution_state.get()
        if state is not None:
            state.execution_metrics.record_human_interaction()
        return f"{HUMAN_INPUT_SENTINEL}{question}"

    return ask_human_wrapper
