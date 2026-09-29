# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Tests that calling `ask_human` ends the LangGraph run.

The orchestrator only inspects the message list after
`agent_graph.invoke(...)` returns. If the graph kept going after
`ask_human`, the model would see the sentinel as an ordinary tool result
and could call further tools (for example, act on a proposal before the
human approved it) before the job is ever marked `interrupted`. These
tests pin down that no model turn follows an `ask_human` call.
"""

from __future__ import annotations

from typing import Any, List

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import Tool

from core.tool_factory import ToolFactory
from tools.human_input import HUMAN_INPUT_SENTINEL


class _ScriptedToolCallingModel(GenericFakeChatModel):
    """Fake chat model that replays scripted AI messages and accepts tools."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ScriptedToolCallingModel":
        return self


def _tool_call_message(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _build_graph(scripted: List[AIMessage], side_effects: List[str]):
    model = _ScriptedToolCallingModel(messages=iter(scripted))
    # Build ask_human through the same config path the agent uses.
    ask_human = ToolFactory(
        {"tools": {"human_input": {"type": "human_input", "name": "ask_human"}}}
    ).create_tools()[0]
    act = Tool(
        name="apply_change",
        description="Apply a change that should only happen after approval.",
        func=lambda arg: side_effects.append(arg) or "applied",
    )
    return create_agent(model=model, tools=[ask_human, act], system_prompt="test")


def test_ask_human_ends_the_run_before_the_next_model_turn():
    side_effects: List[str] = []
    graph = _build_graph(
        [
            _tool_call_message(
                "ask_human", {"question": "Approve the change?"}, "call-1"
            ),
            # If the graph does not stop, the model gets another turn and
            # acts without waiting for the human.
            _tool_call_message("apply_change", {"arg": "change"}, "call-2"),
            AIMessage(content="Done."),
        ],
        side_effects,
    )

    result = graph.invoke({"messages": [HumanMessage(content="Review this file")]})
    messages = result["messages"]

    assert side_effects == []
    assert isinstance(messages[-1], ToolMessage)
    assert str(messages[-1].content) == f"{HUMAN_INPUT_SENTINEL}Approve the change?"
    assert sum(isinstance(m, AIMessage) for m in messages) == 1
