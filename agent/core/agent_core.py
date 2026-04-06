# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from bedrock_agentcore import BedrockAgentCoreApp
from typing import Dict, Any, List
from datetime import datetime
import logging

from langchain_aws import ChatBedrock
from langchain_classic.agents import create_react_agent, AgentExecutor
from langchain_core.prompts import PromptTemplate
from langchain_core.callbacks.base import BaseCallbackHandler

from core.execution_control import ExecutionState, LoopDetector
from core.tool_factory import (
    create_tools_from_config,
    HumanInputRequiredException,
    load_config,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AgentThoughtCapture(BaseCallbackHandler):
    """Callback handler to capture agent thoughts and final answers"""

    def __init__(self):
        self.thoughts: List[str] = []
        self.final_answer: str = ""
        self.current_thought: str = ""

    def on_agent_action(self, action, **kwargs):
        """Called when agent takes an action"""
        # Extract thought from the action log
        if hasattr(action, "log"):
            log = action.log
            if "Thought:" in log:
                thought_parts = log.split("Thought:")
                if len(thought_parts) > 1:
                    thought = thought_parts[1].split("Action:")[0].strip()
                    self.thoughts.append(thought)
                    self.current_thought = thought

    def on_agent_finish(self, finish, **kwargs):
        """Called when agent finishes"""
        # Extract final thought if present
        if hasattr(finish, "log"):
            log = finish.log
            if "Thought:" in log:
                thought_parts = log.split("Thought:")
                if len(thought_parts) > 1:
                    # Get the thought before "Final Answer:"
                    thought = thought_parts[1].split("Final Answer:")[0].strip()
                    if thought and thought not in self.thoughts:
                        self.thoughts.append(thought)
                        self.current_thought = thought

            # Extract final answer
            if "Final Answer:" in log:
                answer_parts = log.split("Final Answer:")
                if len(answer_parts) > 1:
                    self.final_answer = answer_parts[1].strip()

        # Also check the return_values
        if hasattr(finish, "return_values") and "output" in finish.return_values:
            if not self.final_answer:
                self.final_answer = finish.return_values["output"]

    def reset(self):
        """Reset captured data"""
        self.thoughts = []
        self.final_answer = ""
        self.current_thought = ""


class Agent:
    """Agent with circuit breakers, state management, and loop prevention"""

    def __init__(self):
        self.config = load_config()
        # Simple conversation tracking
        self.session_conversations: Dict[str, list] = {}
        self.session_states: Dict[str, ExecutionState] = {}
        self.session_loop_detectors: Dict[str, LoopDetector] = {}

        # Initialize Bedrock model
        self.bedrock_model = ChatBedrock(
            model_id=self.config["aws"]["bedrock"]["model_id"],
            region_name=self.config["aws"]["bedrock"]["region_name"],
        )

        # Create tools and tool factory
        self.tools, self.tool_factory = create_tools_from_config()

        # Create agent with prompt
        self.agent = self._create_agent()

        logger.info(
            "Agent initialized successfully",
            extra={
                "model_id": self.config["aws"]["bedrock"]["model_id"],
                "tool_count": len(self.tools),
            },
        )

    def _create_agent(self):
        """Create the ReAct agent with prompt"""
        prompt_template = self.config["prompts"]["system_template"]

        # Create prompt with the correct input variables for create_react_agent
        prompt = PromptTemplate(
            template=prompt_template,
            input_variables=["input", "agent_scratchpad", "chat_history"],
        )

        return create_react_agent(self.bedrock_model, self.tools, prompt)

    def _get_session_executor(self, session_id: str) -> AgentExecutor:
        """Get or create session-specific agent executor"""
        if session_id not in self.session_conversations:
            # Initialize empty conversation for new session
            self.session_conversations[session_id] = []

            # Create execution state for this session
            self.session_states[session_id] = ExecutionState()

            # Create loop detector for this session
            self.session_loop_detectors[session_id] = LoopDetector(
                max_identical_actions=3, window_size=10
            )

            # Set execution state in tool factory
            self.tool_factory.set_execution_state(self.session_states[session_id])
        else:
            # Make sure the tool factory has the current session's execution
            # state
            self.tool_factory.set_execution_state(self.session_states[session_id])

        # Create agent executor without memory (we'll handle context manually)
        return AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            verbose=self.config["agent"]["verbose"],
            max_iterations=self.config["agent"]["max_iterations"],
            handle_parsing_errors=self.config["agent"]["handle_parsing_errors"],
            return_intermediate_steps=True,  # Enable intermediate steps capture
        )

    def _format_input_with_state(
        self, input_text: str, session_id: str, task_metadata: Dict[str, Any] = None
    ) -> str:
        """Format input with execution state context, conversation history, and S3 file detection"""
        execution_state = self.session_states.get(session_id)
        conversation = self.session_conversations.get(session_id, [])

        # Build conversation history for the prompt
        chat_history = ""
        if conversation:
            history_parts = []
            for msg in conversation[-6:]:  # Show last 6 messages (3 exchanges)
                if msg["type"] == "human":
                    history_parts.append(f"Human: {msg['content']}")
                else:
                    history_parts.append(f"Assistant: {msg['content']}")
            chat_history = "\n".join(history_parts)

        # Check for S3 file references in job metadata
        s3_file_info = ""
        if task_metadata and "triggerPayload" in task_metadata:
            payload = task_metadata["triggerPayload"]
            if payload.get("eventSource") == "s3":
                bucket = payload.get("bucket")
                key = payload.get("key")
                if bucket and key:
                    s3_file_info = f"""
S3 FILE DETECTED:
- An S3 file upload triggered this job
- Bucket: {bucket}
- Key: {key}
- Size: {payload.get("size", "unknown")} bytes
- Upload Time: {payload.get("eventTime", "unknown")}

IMPORTANT: Use the read_s3_file tool to read and analyze this file. Provide the S3 URI: s3://{bucket}/{key}
"""

        if not execution_state and not s3_file_info:
            return input_text

        # Get previous searches and available information
        previous_searches = execution_state.get_previous_searches()
        available_info = execution_state.get_available_info_summary()
        current_focus = execution_state.current_focus

        # Build context string
        context_parts = []

        if previous_searches:
            context_parts.append("PREVIOUS SEARCHES IN THIS SESSION:")
            for i, search in enumerate(
                previous_searches[-5:], 1
            ):  # Show last 5 searches
                context_parts.append(f"{i}. {search}")

        if available_info and available_info != "No previous search results available":
            context_parts.append("\nAVAILABLE INFORMATION:")
            context_parts.append(available_info)

        if current_focus:
            context_parts.append(f"\nCURRENT FOCUS: {current_focus}")

        if context_parts:
            context_string = "\n".join(context_parts)
            return f"""EXECUTION CONTEXT:
{context_string}

CONVERSATION HISTORY:
{chat_history}

IMPORTANT: Use the information from previous searches above. Do NOT repeat searches you've already done unless you need different information.

USER REQUEST: {input_text}"""

        return input_text

    def _save_conversation(
        self, session_id: str, human_message: str = None, ai_message: str = None
    ):
        """Save current conversation to in-memory storage"""
        if session_id in self.session_conversations:
            # Add new messages to the conversation
            if human_message:
                self.session_conversations[session_id].append(
                    {
                        "type": "human",
                        "content": human_message,
                        "timestamp": str(datetime.now()),
                    }
                )

            if ai_message:
                self.session_conversations[session_id].append(
                    {
                        "type": "ai",
                        "content": ai_message,
                        "timestamp": str(datetime.now()),
                    }
                )

            logger.info(
                "Conversation saved",
                extra={
                    "session_id": session_id,
                    "message_count": len(self.session_conversations[session_id]),
                },
            )

    def invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Main agent invocation with improved error handling and state management"""
        user_message = payload.get("prompt", "Hello! How can I help you today?")
        session_id = payload.get("session_id", "default")
        job_id = payload.get("job_id", None)
        task_metadata = payload.get("metadata", None)
        human_response = payload.get("human_response", None)
        original_prompt = payload.get("original_prompt", None)

        try:
            # Get session-specific components
            session_executor = self._get_session_executor(session_id)
            execution_state = self.session_states[session_id]
            loop_detector = self.session_loop_detectors[session_id]

            # Format input based on context
            if human_response and original_prompt:
                # This is a continuation from human input
                # Get the conversation history to find the options that were
                # presented
                conversation = self.session_conversations.get(session_id, [])
                last_ai_message = ""

                if conversation:
                    # Find the last AI message that contains the options
                    logger.info(
                        f"Searching through {len(conversation)} messages for options"
                    )
                    for i, msg in enumerate(reversed(conversation)):
                        logger.info(
                            "Examining message",
                            extra={
                                "index": i,
                                "type": msg["type"],
                                "content_preview": msg["content"][:100],
                            },
                        )
                        if msg["type"] == "ai":
                            if "1." in msg["content"] and "2." in msg["content"]:
                                last_ai_message = msg["content"]
                                logger.info(
                                    "Options message found",
                                    extra={"content_preview": msg["content"][:200]},
                                )
                                break

                    if not last_ai_message:
                        logger.warning(
                            "Previous options message not found in conversation history"
                        )
                        # Fallback: try to get the most recent AI message
                        for msg in reversed(conversation):
                            if msg["type"] == "ai":
                                last_ai_message = msg["content"]
                                logger.info(
                                    "Using most recent AI message as fallback",
                                    extra={"content_preview": msg["content"][:200]},
                                )
                                break

                formatted_input = f"""CONVERSATION CONTINUATION:

Original question: {original_prompt}

I previously asked for clarification with these specific options:
{last_ai_message}

The human responded with: '{human_response}'

IMPORTANT INSTRUCTIONS:
- The human's response '{human_response}' refers to the numbered option from my previous message above
- I must interpret their response in the context of the specific options I provided
- If they gave a number, it corresponds to that exact numbered option from my list
- I should provide detailed information about EXACTLY what they chose
- I should use information from my previous searches and NOT repeat searches I've already done
- Focus specifically on the topic they selected"""

                # Set focus in execution state with more specific context
                if human_response.strip().isdigit():
                    option_num = int(human_response.strip())
                    # Parse the options from the last AI message
                    import re

                    options = re.findall(r"(\d+)\.\s*([^\n]+)", last_ai_message)

                    if options and 1 <= option_num <= len(options):
                        selected_option = options[option_num - 1][1].strip()
                        execution_state.set_focus(selected_option)
                        logger.info(
                            "Human option selected",
                            extra={
                                "option_number": option_num,
                                "selected_option": selected_option,
                            },
                        )
                    else:
                        execution_state.set_focus(f"option {human_response}")
                else:
                    execution_state.set_focus(human_response)
            else:
                # Check if the user is explicitly asking for human-in-the-loop
                # interaction
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
                    formatted_input = f"{user_message}\n\nIMPORTANT: The human has explicitly requested that I use the ask_human tool to get their input on what to explore further. I MUST use the ask_human tool after gathering initial information."
                else:
                    formatted_input = user_message

            # Add execution state context and S3 file detection
            formatted_input = self._format_input_with_state(
                formatted_input, session_id, task_metadata
            )

            # Check for potential loops before execution
            # Note: We check BEFORE execution to prevent wasted compute, but if we've already
            # generated a response in a previous invocation, we should return
            # that instead
            loop_detected_before_execution = loop_detector.add_action(
                "invoke", formatted_input
            )

            if loop_detected_before_execution:
                execution_state.execution_metrics.record_loop_detection()

                # Check if we have a previous response in the conversation
                conversation = self.session_conversations.get(session_id, [])
                last_ai_response = None

                if conversation:
                    for msg in reversed(conversation):
                        if msg["type"] == "ai":
                            last_ai_response = msg["content"]
                            break

                # If we have a previous response, return it instead of the loop
                # error
                if last_ai_response:
                    logger.warning(
                        "Loop detected but returning previous response",
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
                else:
                    # No previous response available, return loop error
                    return {
                        "result": "Loop detected. The agent was about to repeat the same action multiple times. Please try rephrasing your request or provide more specific guidance.",
                        "status": "error",
                        "session_id": session_id,
                        "job_id": job_id,
                        "loop_detected": True,
                        "execution_metrics": execution_state.execution_metrics.get_metrics(),
                    }

            # Execute the agent with chat_history and capture intermediate
            # steps
            start_time = datetime.now()

            # Get chat history for this session
            conversation = self.session_conversations.get(session_id, [])
            chat_history = ""
            if conversation:
                history_parts = []
                # Show last 6 messages (3 exchanges)
                for msg in conversation[-6:]:
                    if msg["type"] == "human":
                        history_parts.append(f"Human: {msg['content']}")
                    else:
                        history_parts.append(f"Assistant: {msg['content']}")
                chat_history = "\n".join(history_parts)

            # Create callback handler to capture thoughts
            thought_capture = AgentThoughtCapture()

            # Execute the agent - intermediate_steps will be included
            # automatically
            result = session_executor.invoke(
                {"input": formatted_input, "chat_history": chat_history},
                config={"callbacks": [thought_capture]},
            )
            end_time = datetime.now()

            # Extract intermediate steps for detailed logging
            intermediate_steps = result.get("intermediate_steps", [])
            execution_trace = []

            # Process intermediate steps (tool actions)
            for step in intermediate_steps:
                if len(step) >= 2:
                    action, observation = step[0], step[1]

                    # Extract action details
                    action_log = action.log if hasattr(action, "log") else str(action)
                    tool_name = action.tool if hasattr(action, "tool") else "unknown"
                    tool_input = (
                        action.tool_input if hasattr(action, "tool_input") else ""
                    )

                    # Parse the thought from the action log
                    thought = ""
                    if "Thought:" in action_log:
                        thought_parts = action_log.split("Thought:")
                        if len(thought_parts) > 1:
                            thought = thought_parts[1].split("Action:")[0].strip()

                    execution_trace.append(
                        {
                            "thought": thought,
                            "action": tool_name,
                            "action_input": str(tool_input),
                            # Limit observation length
                            "observation": str(observation)[:500],
                        }
                    )

            # If no intermediate steps, use the captured thought from callback
            # This handles cases where agent goes directly to Final Answer
            # without using tools
            if not execution_trace:
                final_output = result.get("output", result.get("result", ""))

                # Use the actual thought captured by the callback handler
                thought = (
                    thought_capture.current_thought
                    if thought_capture.current_thought
                    else "Processing request and formulating response"
                )

                # Create trace entry with the actual agent's thought
                if final_output:
                    execution_trace.append(
                        {
                            "thought": thought,
                            "action": "final_answer",
                            "action_input": "",
                            "observation": final_output[:500],
                        }
                    )

            # Update execution metrics
            execution_time = (end_time - start_time).total_seconds()
            execution_state.execution_metrics.execution_time = execution_time

            # Get the final output
            final_output = result.get("output", result.get("result", ""))

            # Save conversation after each interaction
            self._save_conversation(
                session_id, human_message=user_message, ai_message=final_output
            )

            # Debug: Log execution state after saving
            if session_id in self.session_states:
                debug_state = self.session_states[session_id]
                logger.info(
                    "Execution state after completion",
                    extra={
                        "session_id": session_id,
                        "previous_searches": debug_state.get_previous_searches(),
                        "current_focus": debug_state.current_focus,
                        "search_results_count": len(debug_state.search_results),
                    },
                )

            # Get execution summary
            execution_summary = execution_state.get_execution_summary()

            # Check if the result indicates we need more information (fallback)
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
            # The agent requested human input
            execution_state = self.session_states.get(session_id)
            if execution_state:
                execution_state.execution_metrics.record_human_interaction()

            # IMPORTANT: Save conversation even when interrupted
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

    def _needs_clarification(self, output: str) -> bool:
        """Check if the agent's output indicates it needs clarification"""
        clarification_indicators = [
            "need more information",
            "could you clarify",
            "please specify",
            "more details needed",
            "unclear about",
        ]

        output_lower = output.lower()
        return any(indicator in output_lower for indicator in clarification_indicators)


# Initialize AgentCore app
app = BedrockAgentCoreApp()

# Initialize the agent (do this after app creation)
agent = None


@app.entrypoint
def invoke(payload):
    """Agent entrypoint with improved architecture"""
    global agent
    if agent is None:
        agent = Agent()
    return agent.invoke(payload)


if __name__ == "__main__":
    app.run()
