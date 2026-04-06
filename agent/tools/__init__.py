# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Tools package for the example agent.

This package contains example tools that demonstrate how to create
custom tools for your agent. You can add your own tools here.
"""

from .calculator import create_calculator_tool_func
from .human_input import create_human_input_tool_func
from .s3_reader import create_s3_tool_func, create_s3_list_tool_func

__all__ = [
    "create_calculator_tool_func",
    "create_human_input_tool_func",
    "create_s3_tool_func",
    "create_s3_list_tool_func",
]
