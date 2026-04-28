# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Tool factory - builds LangChain `Tool` objects from YAML configuration.

Tools read per-invocation execution state from the
`current_execution_state` ContextVar (see `core.execution_control`), which
the orchestrator sets at the top of each `invoke()`. This keeps the
factory stateless across invocations so concurrent sessions inside the
same container cannot leak state into each other's tools.
"""

import logging
import os
from typing import Any, Dict, List, Tuple

import yaml
from langchain_core.tools import Tool

from tools import (
    create_calculator_tool_func,
    create_human_input_tool_func,
    create_s3_list_tool_func,
    create_s3_tool_func,
)

logger = logging.getLogger(__name__)


class ToolFactory:
    """Build Tool objects from a config dict."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def create_tools(self) -> List[Tool]:
        tools: List[Tool] = []
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
        tool_type = tool_config.get("type")
        builders = {
            "calculator": self._create_calculator_tool,
            "human_input": self._create_human_input_tool,
            "s3_reader": self._create_s3_reader_tool,
            "s3_list": self._create_s3_list_tool,
        }
        builder = builders.get(tool_type)
        if not builder:
            logger.warning(
                "Unknown tool type",
                extra={"tool_name": tool_name, "tool_type": tool_type},
            )
            return None
        return builder(tool_config)

    @staticmethod
    def _create_calculator_tool(tool_config: Dict[str, Any]) -> Tool:
        return Tool(
            name=tool_config.get("name", "calculator"),
            description=tool_config.get(
                "description", "Perform mathematical calculations"
            ),
            func=create_calculator_tool_func(),
        )

    @staticmethod
    def _create_human_input_tool(tool_config: Dict[str, Any]) -> Tool:
        return Tool(
            name=tool_config.get("name", "ask_human"),
            description=tool_config.get(
                "description", "Ask a human for input or clarification"
            ),
            func=create_human_input_tool_func(),
        )

    @staticmethod
    def _create_s3_reader_tool(tool_config: Dict[str, Any]) -> Tool:
        return Tool(
            name=tool_config.get("name", "read_s3_file"),
            description=tool_config.get("description", "Read files from Amazon S3"),
            func=create_s3_tool_func(),
        )

    @staticmethod
    def _create_s3_list_tool(tool_config: Dict[str, Any]) -> Tool:
        return Tool(
            name=tool_config.get("name", "list_s3_files"),
            description=tool_config.get(
                "description", "List files in Amazon S3 buckets"
            ),
            func=create_s3_list_tool_func(),
        )


def load_config() -> Dict[str, Any]:
    """Load YAML config from `agent/config.yaml`."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    agent_dir = os.path.dirname(current_dir)
    config_path = os.path.join(agent_dir, "config.yaml")

    if not os.path.exists(config_path):
        template_path = os.path.join(agent_dir, "config.template.yaml")
        if os.path.exists(template_path):
            logger.warning(
                "Using config template", extra={"template_path": template_path}
            )
            config_path = template_path
        else:
            raise FileNotFoundError(
                f"Neither config.yaml nor config.template.yaml found in {agent_dir}."
            )

    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def create_tools_from_config() -> Tuple[List[Tool], ToolFactory]:
    """Build the list of Tool objects plus the factory instance."""
    config = load_config()
    factory = ToolFactory(config)
    tools = factory.create_tools()
    return tools, factory


__all__ = ["ToolFactory", "load_config", "create_tools_from_config"]
