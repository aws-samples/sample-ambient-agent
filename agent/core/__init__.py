# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Core Package - Platform Integration Code

This package contains the platform integration code that rarely needs modification.
It includes:
- agent_core.py: Main agent implementation with BedrockAgentCore integration
- tool_factory.py: Factory for creating tools from configuration
- execution_control.py: Execution state management and loop prevention

For most customizations, you should:
1. Add/modify tools in the tools/ directory
2. Update config.yaml to enable/disable tools
3. Leave this core package unchanged
"""

from .agent_core import app, Agent
from .tool_factory import (
    ToolFactory,
    create_tools_from_config,
    HumanInputRequiredException,
)
from .execution_control import ExecutionState, LoopDetector

__all__ = [
    "app",
    "Agent",
    "ToolFactory",
    "create_tools_from_config",
    "HumanInputRequiredException",
    "ExecutionState",
    "LoopDetector",
]
