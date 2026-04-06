# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Tool factory for dynamically creating tools from YAML configuration.

This factory creates LangChain tools based on the configuration file,
making it easy to add, remove, or modify tools without changing the agent code.
"""

import yaml
import logging
from typing import Dict, List, Any
from langchain_core.tools import Tool

from tools import (
    create_calculator_tool_func,
    create_human_input_tool_func,
    create_s3_tool_func,
    create_s3_list_tool_func,
)
from tools.human_input import HumanInputRequiredException

logger = logging.getLogger(__name__)


class ToolFactory:
    """
    Factory for creating tools from configuration.

    This class reads the tool configuration and creates LangChain Tool objects
    that can be used by the agent. It provides a clean separation between
    tool definitions and agent logic.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the tool factory.

        Args:
            config: Configuration dictionary loaded from YAML
        """
        self.config = config
        self.execution_state = None  # Will be set by the agent

    def set_execution_state(self, execution_state):
        """
        Set the execution state for tools to use.

        Args:
            execution_state: ExecutionState instance for tracking metrics
        """
        self.execution_state = execution_state

    def create_tools(self) -> List[Tool]:
        """
        Create all tools from configuration.

        Returns:
            List of LangChain Tool objects
        """
        tools = []

        for tool_name, tool_config in self.config.get("tools", {}).items():
            if not tool_config.get("enabled", True):
                logger.info("Skipping disabled tool", extra={"tool_name": tool_name})
                continue

            tool = self._create_tool(tool_name, tool_config)
            if tool:
                tools.append(tool)
                logger.info("Tool created", extra={"tool_name": tool_name})

        return tools

    def _create_tool(self, tool_name: str, tool_config: Dict[str, Any]) -> Tool:
        """
        Create a single tool from configuration.

        Args:
            tool_name: Name of the tool in config
            tool_config: Tool configuration dictionary

        Returns:
            LangChain Tool object or None if tool type is unknown
        """
        tool_type = tool_config.get("type")

        if tool_type == "calculator":
            return self._create_calculator_tool(tool_config)
        elif tool_type == "human_input":
            return self._create_human_input_tool(tool_config)
        elif tool_type == "s3_reader":
            return self._create_s3_reader_tool(tool_config)
        elif tool_type == "s3_list":
            return self._create_s3_list_tool(tool_config)
        else:
            logger.warning("Unknown tool type", extra={"tool_type": tool_type})
            return None

    def _create_calculator_tool(self, tool_config: Dict[str, Any]) -> Tool:
        """Create the calculator tool."""
        calculator_func = create_calculator_tool_func()

        return Tool(
            name=tool_config.get("name", "calculator"),
            description=tool_config.get(
                "description", "Perform mathematical calculations"
            ),
            func=calculator_func,
        )

    def _create_human_input_tool(self, tool_config: Dict[str, Any]) -> Tool:
        """Create the human input tool."""
        human_input_func = create_human_input_tool_func(self.execution_state)

        return Tool(
            name=tool_config.get("name", "ask_human"),
            description=tool_config.get(
                "description", "Ask a human for input or clarification"
            ),
            func=human_input_func,
        )

    def _create_s3_reader_tool(self, tool_config: Dict[str, Any]) -> Tool:
        """Create the S3 file reader tool."""
        s3_func = create_s3_tool_func()

        return Tool(
            name=tool_config.get("name", "read_s3_file"),
            description=tool_config.get("description", "Read files from Amazon S3"),
            func=s3_func,
        )

    def _create_s3_list_tool(self, tool_config: Dict[str, Any]) -> Tool:
        """Create the S3 file listing tool."""
        s3_list_func = create_s3_list_tool_func()

        return Tool(
            name=tool_config.get("name", "list_s3_files"),
            description=tool_config.get(
                "description", "List files in Amazon S3 buckets"
            ),
            func=s3_list_func,
        )


def load_config() -> Dict[str, Any]:
    """
    Load configuration from YAML file.

    Returns:
        Configuration dictionary
    """
    import os

    # Get the directory where this file is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level to the agent directory
    agent_dir = os.path.dirname(current_dir)
    # Build path to config.yaml
    config_path = os.path.join(agent_dir, "config.yaml")

    # If config.yaml doesn't exist, try config.template.yaml
    if not os.path.exists(config_path):
        template_path = os.path.join(agent_dir, "config.template.yaml")
        if os.path.exists(template_path):
            logger.warning(
                "Using config template", extra={"template_path": template_path}
            )
            config_path = template_path
        else:
            raise FileNotFoundError(
                f"Neither config.yaml nor config.template.yaml found in {agent_dir}. "
                "Please copy config.template.yaml to config.yaml and customize it."
            )

    with open(config_path, "r") as file:
        return yaml.safe_load(file)


def create_tools_from_config() -> tuple[List[Tool], ToolFactory]:
    """
    Create tools from configuration file.

    This is the main entry point for creating tools. It loads the config,
    creates the factory, and returns both the tools and the factory instance.

    Returns:
        Tuple of (tools list, factory instance)
    """
    config = load_config()
    factory = ToolFactory(config)
    tools = factory.create_tools()
    return tools, factory


# Export the exception for use by the agent
__all__ = [
    "ToolFactory",
    "load_config",
    "create_tools_from_config",
    "HumanInputRequiredException",
]
