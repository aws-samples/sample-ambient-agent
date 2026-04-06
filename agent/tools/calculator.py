# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Calculator tool - A simple example tool for mathematical operations
"""

import math
from typing import Union

from simpleeval import EvalWithCompoundTypes


def calculate(expression: str) -> Union[float, str]:
    """
    Safely evaluate a mathematical expression.

    Supports basic operations: +, -, *, /, **, (), and common functions like sqrt, abs, etc.

    Args:
        expression: Mathematical expression as a string (e.g., "2 + 2", "sqrt(16)", "10 ** 2")

    Returns:
        Result of the calculation or error message

    Examples:
        >>> calculate("2 + 2")
        4.0
        >>> calculate("10 * 5 + 3")
        53.0
        >>> calculate("sqrt(16)")
        4.0
    """
    try:
        expression = expression.strip()

        s = EvalWithCompoundTypes(
            functions={
                "abs": abs,
                "round": round,
                "min": min,
                "max": max,
                "sum": sum,
                "pow": pow,
                "sqrt": math.sqrt,
                "sin": math.sin,
                "cos": math.cos,
                "tan": math.tan,
                "log": math.log,
                "log10": math.log10,
                "exp": math.exp,
            },
            names={
                "pi": math.pi,
                "e": math.e,
            },
        )

        result = s.eval(expression)

        if isinstance(result, (int, float)):
            if abs(result) < 1e-10:
                return 0.0
            return round(result, 10)

        return result

    except ZeroDivisionError:
        return "Error: Division by zero"
    except SyntaxError:
        return f"Error: Invalid mathematical expression: '{expression}'"
    except NameError as e:
        return f"Error: Unknown function or variable: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


def create_calculator_tool_func():
    """
    Create the calculator tool function for LangChain.

    Returns:
        Function that can be used as a LangChain tool
    """

    def calculator_wrapper(expression: str) -> str:
        """
        Perform mathematical calculations.

        Supports:
        - Basic operations: +, -, *, /, ** (power)
        - Parentheses for grouping
        - Math functions: sqrt, sin, cos, tan, log, log10, exp, abs, round, min, max
        - Constants: pi, e

        Examples:
        - "2 + 2" → 4
        - "10 * 5 + 3" → 53
        - "sqrt(16)" → 4
        - "2 ** 8" → 256
        - "sin(pi / 2)" → 1

        Args:
            expression: Mathematical expression to evaluate

        Returns:
            Result of the calculation or error message
        """
        result = calculate(expression)

        if isinstance(result, str) and result.startswith("Error"):
            return result

        return f"Result: {result}"

    return calculator_wrapper
