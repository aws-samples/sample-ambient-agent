# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Core Package - Platform Integration Code

This package contains the platform integration code that rarely needs
modification. It includes:

- agent_core.py: Main agent orchestrator with BedrockAgentCore integration
- tool_factory.py: Factory for creating tools from configuration
- execution_control.py: Execution state management + loop detection + the
  `current_execution_state` ContextVar tools read at invocation time

For most customizations, you should:

1. Add/modify tools in the tools/ directory
2. Update config.yaml to enable/disable tools
3. Leave this core package unchanged

Note on human-in-the-loop: the agent no longer raises an exception when
a tool needs human input. The `ask_human` tool returns a sentinel string
(see `tools/human_input.py::HUMAN_INPUT_SENTINEL`) which the orchestrator
detects on the `ToolMessage` stream and converts into an `interrupted`
response. The old `HumanInputRequiredException` was removed because
LangGraph's tool node catches tool-raised exceptions and feeds them back
to the model, which broke the contract. That's why it is not re-exported
here any more.
"""

from .agent_core import app, Agent
from .execution_control import (
    ExecutionState,
    LoopDetector,
    current_execution_state,
)
from .tool_factory import (
    ToolFactory,
    create_tools_from_config,
    load_config,
)

__all__ = [
    "app",
    "Agent",
    "ToolFactory",
    "create_tools_from_config",
    "load_config",
    "ExecutionState",
    "LoopDetector",
    "current_execution_state",
]
