# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Agent core using the modern LangChain / LangGraph `create_agent` API.

This module replaces the legacy `create_react_agent` + `AgentExecutor`
pipeline (which is being deprecated) with `langchain.agents.create_agent`.
The resulting agent is a compiled LangGraph that:

- Takes `{"messages": [...]}` as input and returns a state with a full
  `messages` list that includes the user's question, any tool calls the
  model emitted, the tool outputs, and the final assistant message.
- Manages the scratchpad internally - no `{tools}`, `{tool_names}`, or
  `{agent_scratchpad}` placeholders are needed in the system prompt.

The orchestrator preserves the existing platform contract:
- Input payload shape (prompt, session_id, job_id, metadata, human_response,
  original_prompt).
- Output payload shape (result, status, execution_metrics, execution_trace,
  execution_summary).
- Loop detection, execution-state tracking, and human-in-the-loop interrupt
  behavior. Human input requests are now signalled via a sentinel string
  returned by the `ask_human` tool (see `tools.human_input`) rather than a
  raised exception, because LangGraph's tool node captures tool exceptions
  as error observations instead of propagating them.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

from bedrock_agentcore import BedrockAgentCoreApp
from langchain_aws import ChatBedrock
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)

# `create_agent` lives in `langchain.agents` in langchain v1+. Import it at
# module load so we fail fast if the package is missing.
from langchain.agents import create_agent

from core.execution_control import ExecutionState, LoopDetector
from core.tool_factory import (
    HumanInputRequiredException,
    create_tools_from_config,
    load_config,
)
from tools.human_input import HUMAN_INPUT_SENTINEL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Agent:
    """Agent wrapper with session state, loop prevention, and trace capture."""

    def __init__(self):
        self.config = load_config()
        self.session_conversations: Dict[str, list] = {}
        self.session_states: Dict[str, ExecutionState] = {}
        self.session_loop_detectors: Dict[str, LoopDetector] = {}

        # Initialize Bedrock model
        self.bedrock_model = ChatBedrock(
            model_id=self.config["aws"]["bedrock"]["model_id"],
            region_name=self.config["aws"]["bedrock"]["region_name"],
        )

        # Build tools (and keep a reference to the factory so we can swap the
        # execution state per session, same as the legacy implementation).
        self.tools, self.tool_factory = create_tools_from_config()

        # System prompt text (plain guidance - no ReAct placeholders).
        self.system_prompt = self.config["prompts"]["system_template"]

        # Build the compiled LangGraph agent once. `create_agent` is
        # stateless at construction time; session state is tracked by this
        # wrapper and injected into each invocation as message history.
        self.agent_graph = create_agent(
            model=self.bedrock_model,
            tools=self.tools,
            system_prompt=self.system_prompt,
        )

        logger.info(
            "Agent initialized successfully",
            extra={
                "model_id": self.config["aws"]["bedrock"]["model_id"],
                "tool_count": len(self.tools),
            },
        )

    # ------------------------------------------------------------------
    # Session bootstrap
    # ------------------------------------------------------------------

    def _ensure_session(self, session_id: str) -> None:
        """Create per-session state containers if this is a new session."""
        if session_id not in self.session_conversations:
            self.session_conversations[session_id] = []
            self.session_states[session_id] = ExecutionState()
            self.session_loop_detectors[session_id] = LoopDetector(
                max_identical_actions=3, window_size=10
            )
        # Always rebind the tool factory to the current session's state so
        # tool invocations record metrics against the right session.
        self.tool_factory.set_execution_state(self.session_states[session_id])

    # ------------------------------------------------------------------
    # Input/context construction
    # ------------------------------------------------------------------

    def _build_execution_context_block(
        self,
        session_id: str,
        task_metadata: Optional[Dict[str, Any]],
    ) -> str:
        """Build the execution context block appended to the user message.

        Mirrors the legacy `_format_input_with_state` output so downstream
        prompts / models continue to see previous searches and S3 hints.
        """
        execution_state = self.session_states.get(session_id)

        s3_file_info = ""
        if task_metadata and "triggerPayload" in task_metadata:
            payload = task_metadata["triggerPayload"]
            if payload.get("eventSource") == "s3":
                bucket = payload.get("bucket")
                key = payload.get("key")
                if bucket and key:
                    s3_file_info = (
                        "\nS3 FILE DETECTED:\n"
                        f"- Bucket: {bucket}\n"
                        f"- Key: {key}\n"
                        f"- Size: {payload.get('size', 'unknown')} bytes\n"
                        f"- Upload Time: {payload.get('eventTime', 'unknown')}\n"
                        "\nIMPORTANT: Use the read_s3_file tool with the S3 URI "
                        f"s3://{bucket}/{key}\n"
                    )

        if not execution_state and not s3_file_info:
            return ""

        context_parts: List[str] = []
        if execution_state:
            previous_searches = execution_state.get_previous_searches()
            available_info = execution_state.get_available_info_summary()
            current_focus = execution_state.current_focus

            if previous_searches:
                context_parts.append("PREVIOUS SEARCHES IN THIS SESSION:")
                for i, search in enumerate(previous_searches[-5:], 1):
                    context_parts.append(f"{i}. {search}")

            if (
                available_info
                and available_info != "No previous search results available"
            ):
                context_parts.append("\nAVAILABLE INFORMATION:")
                context_parts.append(available_info)

            if current_focus:
                context_parts.append(f"\nCURRENT FOCUS: {current_focus}")

        if not context_parts and not s3_file_info:
            return ""

        joined = "\n".join(context_parts) if context_parts else ""
        block = "EXECUTION CONTEXT:\n" + joined if joined else ""
        if s3_file_info:
            block = block + s3_file_info if block else s3_file_info
        return block

    def _build_messages_for_invocation(
        self,
        session_id: str,
        user_message: str,
        task_metadata: Optional[Dict[str, Any]],
    ) -> List[BaseMessage]:
        """Build the list of messages passed to the LangGraph agent.

        The system prompt is provided to `create_agent` itself and is
        prepended by the graph at invocation time, so we only need to supply
        the prior conversation (as Human/AI messages) plus the current user
        message optionally decorated with execution context.
        """
        messages: List[BaseMessage] = []

        # Replay prior conversation (last 6 turns, same as legacy behaviour)
        conversation = self.session_conversations.get(session_id, [])
        for msg in conversation[-6:]:
            if msg["type"] == "human":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))

        # Wrap the current user message with any execution context
        context_block = self._build_execution_context_block(session_id, task_metadata)
        if context_block:
            final_user_content = (
                f"{context_block}\n\n"
                "IMPORTANT: Use the information from previous searches above. "
                "Do NOT repeat searches you've already done unless you need "
                "different information.\n\n"
                f"USER REQUEST: {user_message}"
            )
        else:
            final_user_content = user_message

        messages.append(HumanMessage(content=final_user_content))
        return messages

    # ------------------------------------------------------------------
    # Trace extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_execution_trace(
        messages: List[BaseMessage],
    ) -> List[Dict[str, Any]]:
        """Build an execution trace by pairing tool calls with tool results.

        The returned trace is a list of dicts with the same shape the legacy
        code produced: {thought, action, action_input, observation}. The
        `thought` is populated from any text the assistant emitted alongside
        its tool calls, since modern tool-calling models do not produce a
        separate ReAct-style "Thought:" line.
        """
        trace: List[Dict[str, Any]] = []

        # Map tool_call_id -> ToolMessage content for quick lookup
        tool_results: Dict[str, str] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_results[msg.tool_call_id] = str(msg.content)

        last_assistant_text: str = ""
        for msg in messages:
            if isinstance(msg, AIMessage):
                # Capture any inline text the model emitted to use as the
                # "thought" for tool calls on this same message.
                assistant_text = ""
                if isinstance(msg.content, str):
                    assistant_text = msg.content
                elif isinstance(msg.content, list):
                    # Some providers return structured content blocks
                    parts = []
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    assistant_text = "\n".join(p for p in parts if p)

                if assistant_text:
                    last_assistant_text = assistant_text.strip()

                tool_calls = getattr(msg, "tool_calls", None) or []
                for call in tool_calls:
                    name = call.get("name", "unknown")
                    args = call.get("args", {})
                    call_id = call.get("id", "")
                    observation = tool_results.get(call_id, "")
                    trace.append(
                        {
                            "thought": last_assistant_text,
                            "action": name,
                            "action_input": str(args),
                            "observation": str(observation)[:500],
                        }
                    )

        return trace

    @staticmethod
    def _extract_final_output(messages: List[BaseMessage]) -> str:
        """Return the content of the last AIMessage that has no tool calls."""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None) or []
                if tool_calls:
                    continue
                if isinstance(msg.content, str):
                    return msg.content
                if isinstance(msg.content, list):
                    parts = []
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    joined = "\n".join(p for p in parts if p)
                    if joined:
                        return joined
        return ""

    @staticmethod
    def _detect_human_input_request(messages: List[BaseMessage]) -> Optional[str]:
        """If any tool message carries the human-input sentinel, return the question."""
        for msg in messages:
            if isinstance(msg, ToolMessage):
                content = str(msg.content)
                if content.startswith(HUMAN_INPUT_SENTINEL):
                    return content[len(HUMAN_INPUT_SENTINEL):].strip()
        return None

    # ------------------------------------------------------------------
    # Conversation + loop helpers (unchanged behaviour)
    # ------------------------------------------------------------------

    def _record_tool_search_history(
        self, session_id: str, messages: List[BaseMessage]
    ) -> None:
        """Record tool calls in the execution state so the loop detector and
        previous-search context reflect what the agent did in this run."""
        execution_state = self.session_states.get(session_id)
        if not execution_state:
            return

        for msg in messages:
            if isinstance(msg, AIMessage):
                for call in getattr(msg, "tool_calls", None) or []:
                    # Track tool invocations generically as "searches" so the
                    # rest of the platform (UI + context reuse) keeps working.
                    args = call.get("args", {})
                    query_repr = (
                        args.get("query")
                        or args.get("input")
                        or args.get("question")
                        or str(args)
                    )
                    execution_state.add_search_result(
                        f"{call.get('name', 'tool')}: {query_repr}",
                        {"tool": call.get("name"), "args": args},
                    )

    def _save_conversation(
        self,
        session_id: str,
        human_message: Optional[str] = None,
        ai_message: Optional[str] = None,
    ) -> None:
        if session_id not in self.session_conversations:
            return
        now = str(datetime.now())
        if human_message:
            self.session_conversations[session_id].append(
                {"type": "human", "content": human_message, "timestamp": now}
            )
        if ai_message:
            self.session_conversations[session_id].append(
                {"type": "ai", "content": ai_message, "timestamp": now}
            )
        logger.info(
            "Conversation saved",
            extra={
                "session_id": session_id,
                "message_count": len(self.session_conversations[session_id]),
            },
        )

    def _needs_clarification(self, output: str) -> bool:
        indicators = [
            "need more information",
            "could you clarify",
            "please specify",
            "more details needed",
            "unclear about",
        ]
        output_lower = output.lower()
        return any(ind in output_lower for ind in indicators)

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    def invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Main agent invocation, platform-facing contract preserved."""
        user_message = payload.get("prompt", "Hello! How can I help you today?")
        session_id = payload.get("session_id", "default")
        job_id = payload.get("job_id", None)
        task_metadata = payload.get("metadata", None)
        human_response = payload.get("human_response", None)
        original_prompt = payload.get("original_prompt", None)

        try:
            self._ensure_session(session_id)
            execution_state = self.session_states[session_id]
            loop_detector = self.session_loop_detectors[session_id]

            # If this is a continuation after human input, reconstruct the
            # focused prompt using the most recent options message (same
            # logic as the legacy path).
            if human_response and original_prompt:
                conversation = self.session_conversations.get(session_id, [])
                last_ai_message = ""
                for msg in reversed(conversation):
                    if (
                        msg["type"] == "ai"
                        and "1." in msg["content"]
                        and "2." in msg["content"]
                    ):
                        last_ai_message = msg["content"]
                        break
                if not last_ai_message:
                    for msg in reversed(conversation):
                        if msg["type"] == "ai":
                            last_ai_message = msg["content"]
                            break

                formatted_input = (
                    "CONVERSATION CONTINUATION:\n\n"
                    f"Original question: {original_prompt}\n\n"
                    "I previously asked for clarification with these specific options:\n"
                    f"{last_ai_message}\n\n"
                    f"The human responded with: '{human_response}'\n\n"
                    "IMPORTANT INSTRUCTIONS:\n"
                    f"- The human's response '{human_response}' refers to the numbered option from my previous message above\n"
                    "- Interpret their response in the context of those options\n"
                    "- Do NOT repeat searches already performed in this session"
                )

                if human_response.strip().isdigit():
                    import re

                    option_num = int(human_response.strip())
                    options = re.findall(r"(\d+)\.\s*([^\n]+)", last_ai_message)
                    if options and 1 <= option_num <= len(options):
                        selected_option = options[option_num - 1][1].strip()
                        execution_state.set_focus(selected_option)
                    else:
                        execution_state.set_focus(f"option {human_response}")
                else:
                    execution_state.set_focus(human_response)
            else:
                if any(
                    phrase in user_message.lower()
                    for phrase in [
                        "ask me by using the human in the loop tool",
                        "ask me using the human in the loop tool",
                        "use the human in the loop tool to ask me",
                        "human in the loop tool what types",
                        "ask me what types",
                        "ask me with ask_human",
                    ]
                ):
                    formatted_input = (
                        f"{user_message}\n\n"
                        "IMPORTANT: The human has explicitly requested that I use "
                        "the ask_human tool to get their input on what to explore "
                        "further. I MUST use the ask_human tool after gathering "
                        "initial information."
                    )
                else:
                    formatted_input = user_message

            # Loop-detect BEFORE executing so we avoid wasted compute.
            loop_detected = loop_detector.add_action("invoke", formatted_input)
            if loop_detected:
                execution_state.execution_metrics.record_loop_detection()
                conversation = self.session_conversations.get(session_id, [])
                last_ai_response = None
                for msg in reversed(conversation):
                    if msg["type"] == "ai":
                        last_ai_response = msg["content"]
                        break

                if last_ai_response:
                    logger.warning(
                        "Loop detected; returning previous response",
                        extra={"session_id": session_id},
                    )
                    return {
                        "result": last_ai_response,
                        "agent_response": last_ai_response,
                        "status": "completed",
                        "session_id": session_id,
                        "job_id": job_id,
                        "loop_detected": True,
                        "loop_prevented": True,
                        "execution_metrics": execution_state.execution_metrics.get_metrics(),
                    }
                return {
                    "result": (
                        "Loop detected. The agent was about to repeat the same "
                        "action multiple times. Please try rephrasing your request "
                        "or provide more specific guidance."
                    ),
                    "status": "error",
                    "session_id": session_id,
                    "job_id": job_id,
                    "loop_detected": True,
                    "execution_metrics": execution_state.execution_metrics.get_metrics(),
                }

            # Execute the LangGraph agent
            start_time = datetime.now()
            messages_in = self._build_messages_for_invocation(
                session_id, formatted_input, task_metadata
            )

            # `max_iterations` maps roughly to LangGraph's `recursion_limit`.
            # Each iteration is ~2 graph nodes (model + tool), so double it.
            recursion_limit = max(
                4, int(self.config["agent"].get("max_iterations", 10)) * 2
            )
            result_state = self.agent_graph.invoke(
                {"messages": messages_in},
                config={"recursion_limit": recursion_limit},
            )
            end_time = datetime.now()

            all_messages: List[BaseMessage] = result_state.get("messages", [])

            # Record tool calls against the session's execution state
            self._record_tool_search_history(session_id, all_messages)

            # Build the trace the platform expects
            execution_trace = self._extract_execution_trace(all_messages)

            # Check for human-input interrupt
            human_question = self._detect_human_input_request(all_messages)
            if human_question is not None:
                execution_state.execution_metrics.record_human_interaction()
                # Persist the question as the AI side of the conversation so
                # that a follow-up continuation can find it.
                self._save_conversation(
                    session_id,
                    human_message=user_message,
                    ai_message=human_question,
                )
                return {
                    "result": f"I need clarification: {human_question}",
                    "status": "interrupted",
                    "requires_action": True,
                    "session_id": session_id,
                    "job_id": job_id,
                    "human_input_question": human_question,
                    "next_steps": [
                        "Please provide the requested information",
                        "Answer the clarification question",
                        "Provide any additional context that might be helpful",
                    ],
                    "execution_metrics": execution_state.execution_metrics.get_metrics(),
                    "execution_trace": execution_trace,
                }

            # Extract final output
            final_output = self._extract_final_output(all_messages)

            # Update metrics
            execution_time = (end_time - start_time).total_seconds()
            execution_state.execution_metrics.execution_time = execution_time

            # Persist conversation turn
            self._save_conversation(
                session_id, human_message=user_message, ai_message=final_output
            )

            execution_summary = execution_state.get_execution_summary()

            # Fallback clarification detection on the text (legacy behaviour)
            if self._needs_clarification(final_output):
                return {
                    "result": final_output,
                    "status": "interrupted",
                    "requires_action": True,
                    "session_id": session_id,
                    "job_id": job_id,
                    "clarification_needed": True,
                    "execution_metrics": execution_state.execution_metrics.get_metrics(),
                    "execution_summary": execution_summary,
                    "execution_trace": execution_trace,
                }

            return {
                "result": final_output,
                "status": "completed",
                "session_id": session_id,
                "job_id": job_id,
                "execution_metrics": execution_state.execution_metrics.get_metrics(),
                "execution_summary": execution_summary,
                "execution_trace": execution_trace,
            }

        except HumanInputRequiredException as e:
            # Retained for defence in depth - if something outside the graph
            # still raises this, handle it the same way we would an in-graph
            # interrupt.
            execution_state = self.session_states.get(session_id)
            if execution_state:
                execution_state.execution_metrics.record_human_interaction()
            self._save_conversation(
                session_id, human_message=user_message, ai_message=e.question
            )
            return {
                "result": f"I need clarification: {e.question}",
                "status": "interrupted",
                "requires_action": True,
                "session_id": session_id,
                "job_id": job_id,
                "human_input_question": e.question,
                "next_steps": [
                    "Please provide the requested information",
                    "Answer the clarification question",
                    "Provide any additional context that might be helpful",
                ],
                "execution_metrics": (
                    execution_state.execution_metrics.get_metrics()
                    if execution_state
                    else {}
                ),
                "execution_trace": [],
            }
        except Exception as e:
            logger.error(
                "Agent execution failed", extra={"error": str(e)}, exc_info=True
            )
            execution_state = self.session_states.get(session_id)
            if execution_state:
                execution_state.execution_metrics.record_error(str(e))
            return {
                "result": "An internal error occurred. Please try again or contact support.",
                "status": "error",
                "session_id": session_id,
                "job_id": job_id,
                "execution_metrics": (
                    execution_state.execution_metrics.get_metrics()
                    if execution_state
                    else {}
                ),
                "execution_trace": [],
            }


# Initialize AgentCore app
app = BedrockAgentCoreApp()

# Lazily initialise the agent on first invocation so cold-start cost is not
# paid unless the container actually handles a request.
agent: Optional[Agent] = None


@app.entrypoint
def invoke(payload):
    """Agent entrypoint."""
    global agent
    if agent is None:
        agent = Agent()
    return agent.invoke(payload)


if __name__ == "__main__":
    app.run()
