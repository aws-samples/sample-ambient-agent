# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Human input tool - Allows the agent to request clarification from humans
"""


class HumanInputRequiredException(Exception):
    """Exception raised when human input is required"""

    def __init__(self, question: str):
        self.question = question
        super().__init__(f"Human input required: {question}")


def create_human_input_tool_func(execution_state=None):
    """
    Create the human input tool function for LangChain.

    This tool allows the agent to request clarification or additional
    information from a human user. When called, it raises an exception
    that the platform catches and presents to the user.

    Args:
        execution_state: Optional execution state to track metrics

    Returns:
        Function that can be used as a LangChain tool
    """

    def ask_human_wrapper(question: str) -> str:
        """
        Request input or clarification from a human user.

        Use this tool when you need:
        - Clarification on ambiguous requests
        - Additional information not available through other tools
        - User preferences or choices
        - Confirmation before taking important actions

        The question should be clear, specific, and easy for the user to answer.

        Examples of good questions:
        - "Which file format would you prefer: PDF or CSV?"
        - "Should I include historical data from the last 30 days or 90 days?"
        - "I found multiple options. Which one interests you most: 1) Option A, 2) Option B, 3) Option C?"

        Args:
            question: Clear, specific question to ask the human user

        Returns:
            This function raises an exception to interrupt execution
        """
        # Record human interaction in metrics if available
        if execution_state:
            execution_state.execution_metrics.record_human_interaction()

        # Raise exception to stop agent execution and trigger human input
        raise HumanInputRequiredException(question)

    return ask_human_wrapper
