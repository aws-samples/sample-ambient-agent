# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import uuid
import os
from datetime import datetime, timedelta
from typing import Dict, Any
from aws_lambda_powertools import Logger

# Configure logging
logger = Logger(service="job-execution", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
# Default bedrock-agentcore client (uses Lambda's region). The actual client
# used for invocation is built per-call from the agent ARN's region, since
# AgentCore runtimes may live in a different region than this Lambda.
bedrock_agentcore = boto3.client("bedrock-agentcore")


def _agentcore_client_for_arn(agent_arn: str):
    """Return a bedrock-agentcore client pinned to the region in the agent ARN.

    The agent registry stores full ARNs, and the runtime may be deployed in
    a different region than this Lambda. Building a region-specific client
    ensures the request is signed and routed to the correct regional
    endpoint.
    """
    try:
        # ARN format: arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>
        arn_region = agent_arn.split(":")[3]
    except (IndexError, AttributeError):
        arn_region = None

    if arn_region:
        return boto3.client("bedrock-agentcore", region_name=arn_region)
    return bedrock_agentcore

# Environment variables
TASK_REGISTRY_TABLE = os.environ["TASK_REGISTRY_TABLE"]
CONVERSATION_STORE_TABLE = os.environ["CONVERSATION_STORE_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
REGION = os.environ["REGION"]

# Get DynamoDB tables
task_table = dynamodb.Table(TASK_REGISTRY_TABLE)
conversation_table = dynamodb.Table(CONVERSATION_STORE_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for job execution

    This function executes jobs by invoking Bedrock Agent Core agents.
    It handles:
    - Direct job execution via API
    - Scheduled job execution via scheduler
    - Human interruption and conversation continuity
    - Agent capability detection and adaptation
    """

    try:
        # Check if this is a direct API call or scheduler invocation
        if "httpMethod" in event:
            return handle_api_execution(event, context)
        else:
            return handle_scheduler_execution(event, context)

    except Exception as e:
        logger.error(
            "Job execution handler failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "Job execution failed"})


def handle_api_execution(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle direct API execution requests"""
    try:
        # Extract request information
        path_parameters = event.get("pathParameters") or {}
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}

        # Extract user ID from Cognito JWT
        user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
        job_id = path_parameters.get("jobId")

        if not job_id:
            return create_response(400, {"error": "Job ID required"})

        # Get job details
        task_response = task_table.get_item(Key={"jobId": job_id})
        if "Item" not in task_response:
            return create_response(404, {"error": "Job not found"})

        job = task_response["Item"]

        # Verify ownership
        if job["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        # For API calls, start async execution to avoid Gateway timeout
        human_response = body.get("humanResponse")

        # Update job status to busy immediately
        update_task_status(job_id, "busy")

        # Persist the human turn synchronously before async execution kicks
        # off, so the conversation reflects the new message on the very next
        # client poll. save_conversation_turn() dedupes against the last
        # human turn, so the eventual write after the agent responds is safe.
        if human_response:
            session_id = job.get("sessionId")
            agent_id = job.get("agentId")
            if session_id and agent_id:
                append_human_turn(session_id, agent_id, human_response)


        # Start async execution using Lambda invoke
        import boto3

        lambda_client = boto3.client("lambda")


        # Prepare payload for async execution
        async_payload = {
            "jobId": job_id,
            "humanResponse": human_response,
            "asyncExecution": True,
        }

        try:
            # Invoke this same function asynchronously for actual execution
            lambda_client.invoke(
                FunctionName=context.function_name,
                InvocationType="Event",  # Async invocation
                Payload=json.dumps(async_payload),
            )

            # Return immediately with busy status
            return create_response(
                200,
                {
                    "status": "busy",
                    "message": "Job execution started",
                    "jobId": job_id,
                    "sessionId": job.get("sessionId"),
                },
            )

        except Exception as invoke_error:
            logger.error(f"Failed to start async execution: {str(invoke_error)}")
            # Fallback to synchronous execution with shorter timeout
            result = execute_task_with_timeout(job, human_response, timeout_seconds=25)
            return create_response(200, result)

    except Exception as e:
        logger.error(
            "API execution handler failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "API execution failed"})


def handle_scheduler_execution(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle scheduler-triggered execution or async execution"""
    try:
        job_id = event.get("jobId")
        if not job_id:
            logger.error("No job ID provided in scheduler event")
            return {"statusCode": 400, "body": "Job ID required"}

        # Check if this is an async execution request
        if event.get("asyncExecution"):
            logger.info("Handling async execution", extra={"job_id": job_id})
            human_response = event.get("humanResponse")

            # Get job details
            task_response = task_table.get_item(Key={"jobId": job_id})
            if "Item" not in task_response:
                logger.error("Job not found", extra={"job_id": job_id})
                return {"statusCode": 404, "body": "Job not found"}

            job = task_response["Item"]

            # Execute the job with full timeout
            result = execute_task(job, human_response)

            return {"statusCode": 200, "body": json.dumps(result)}

        # Regular scheduler execution
        # Get job details
        task_response = task_table.get_item(Key={"jobId": job_id})
        if "Item" not in task_response:
            logger.error("Job not found", extra={"job_id": job_id})
            return {"statusCode": 404, "body": "Job not found"}

        job = task_response["Item"]

        # Execute the job
        result = execute_task(job)

        return {"statusCode": 200, "body": json.dumps(result)}

    except Exception as e:
        logger.error(
            "Scheduler execution handler failed", extra={"error": str(e)}, exc_info=True
        )
        return {"statusCode": 500, "body": "Scheduler execution failed"}


def execute_task_with_timeout(
    job: Dict[str, Any], human_response: str = None, timeout_seconds: int = 25
) -> Dict[str, Any]:
    """Execute job with a shorter timeout for API Gateway compatibility"""
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError("Job execution timed out")

    # Set up timeout
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_seconds)

    try:
        result = execute_task(job, human_response)
        signal.alarm(0)  # Cancel the alarm
        return result
    except TimeoutError:
        signal.alarm(0)  # Cancel the alarm
        job_id = job["jobId"]
        logger.warning(
            f"Job {job_id} execution timed out after {timeout_seconds} seconds"
        )

        # Update job status to indicate timeout
        update_task_status(job_id, "busy")

        return {
            "status": "busy",
            "message": "Job execution is taking longer than expected. It will continue in the background.",
            "jobId": job_id,
            "sessionId": job.get("sessionId"),
        }
    except Exception as e:
        signal.alarm(0)  # Cancel the alarm
        raise e


def execute_task(job: Dict[str, Any], human_response: str = None) -> Dict[str, Any]:
    """
    Execute a job using the specified agent

    This function:
    1. Gets agent details and capabilities
    2. Loads conversation context if agent supports it
    3. Invokes the agent via Bedrock Agent Core
    4. Handles human interruption if supported
    5. Updates job status and conversation store
    """

    job_id = job["jobId"]
    agent_id = job["agentId"]
    session_id = job["sessionId"]
    user_id = job.get("userId")

    try:
        # Update job status to busy
        update_task_status(job_id, "busy")
        logger.info("Job execution started", extra={"job_id": job_id})
        append_execution_log(job_id, "info", "Job execution started")

        # Get agent details
        logger.info(
            f"Retrieving agent details for agent {agent_id}", extra={"job_id": job_id}
        )
        agent_response = agent_table.get_item(Key={"agentId": agent_id})
        if "Item" not in agent_response:
            logger.error(f"Agent {agent_id} not found", extra={"job_id": job_id})
            update_task_status(job_id, "error", "Agent not found")
            return {"status": "error", "error": "Agent not found"}

        agent = agent_response["Item"]
        agent_arn = agent["agentArn"]
        capabilities = agent.get("capabilities", [])

        logger.info(
            f"Executing job {job_id} with agent {agent_id} (capabilities: {capabilities})"
        )
        logger.info(
            f"Agent loaded: {agent.get('agentName', 'Unknown')} (capabilities: {
                capabilities
            })",
            extra={"job_id": job_id},
        )
        append_execution_log(
            job_id, "info", f"Agent loaded: {agent.get('agentName', 'Unknown')}"
        )

        # Prepare the prompt based on whether this is a continuation or new job
        original_prompt = job["prompt"]

        # Load conversation context
        logger.info("Loading conversation context", extra={"job_id": job_id})
        conversation_context = load_conversation_context(session_id, agent_id)

        # Handle human response if this is a continuation
        if human_response:
            logger.info(
                "Processing human response",
                extra={"job_id": job_id, "response_length": len(human_response)},
            )
            logger.info(
                "Processing human response to continue job", extra={"job_id": job_id}
            )
            # This is a continuation - use the human response as the new input
            prompt = f"CONVERSATION CONTEXT:\n{conversation_context}\n\nHUMAN RESPONSE: {human_response}\n\nPlease continue based on the human's response above."
        else:
            # This is a new job or retry - use the original prompt
            if conversation_context:
                logger.info(
                    "Preparing prompt with conversation context",
                    extra={"job_id": job_id, "user_id": user_id},
                )
                prompt = f"CONVERSATION CONTEXT:\n{conversation_context}\n\nCURRENT REQUEST: {original_prompt}"
            else:
                logger.info("Preparing prompt for new job", extra={"job_id": job_id})
                prompt = original_prompt

        # Invoke the agent
        start_time = datetime.utcnow()

        # Extract agent ID from ARN for Bedrock call
        _bedrock_agent_id = extract_agent_id_from_arn(agent_arn)

        try:
            # Use Bedrock Agent Core API with correct payload structure
            # Generate a proper session ID (must be 33+ characters)
            if len(session_id) < 33:
                session_id = f"{session_id}-{
                    ''.join(
                        [str(uuid.uuid4()).replace('-', '') for _ in range(2)][
                            : 33 - len(session_id)
                        ]
                    )
                }"

            logger.info(
                "Sending prompt to agent", extra={"prompt_preview": prompt[:200]}
            )
            logger.info("Using session ID", extra={"session_id": session_id})
            logger.info(
                f"Invoking agent with session ID: {session_id[:20]}...",
                extra={"job_id": job_id, "user_id": user_id},
            )

            # Use the flat payload structure that the agent expects
            payload = json.dumps(
                {
                    "prompt": prompt,
                    "session_id": session_id,
                    "job_id": job_id,
                    "metadata": {
                        "timestamp": datetime.utcnow().isoformat(),
                        "source": "multi-agent-platform",
                    },
                }
            )

            logger.info(
                "Sending request to Bedrock Agent Core", extra={"job_id": job_id}
            )
            append_execution_log(job_id, "info", "Sending request to agent")
            # Build a client pinned to the region encoded in the agent ARN
            # so the request is signed/routed to the correct regional endpoint
            # regardless of which region this Lambda is running in.
            agentcore_client = _agentcore_client_for_arn(agent_arn)
            response = agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=agent_arn,  # Use the full ARN from the agent registry
                runtimeSessionId=session_id,
                payload=payload,
                qualifier="DEFAULT",
            )

            logger.info(
                "Received response from agent, processing results",
                extra={"job_id": job_id, "user_id": user_id},
            )
            # Process the Agent Core response
            result_text = ""
            requires_human_input = False
            agent_status = "completed"

            if "response" in response:
                response_body = response["response"].read()
                response_data = json.loads(response_body)

                # Log the full response for debugging
                logger.info(
                    "Agent response received", extra={"response_data": response_data}
                )
                append_execution_log(job_id, "info", "Received response from agent")

                # Extract and log execution metrics if available
                if (
                    isinstance(response_data, dict)
                    and "execution_metrics" in response_data
                ):
                    metrics = response_data["execution_metrics"]
                    logger.info(
                        f"Agent performed {metrics.get('search_count', 0)} searches in {
                            metrics.get('execution_time', 0):.2f}s",
                        extra={"job_id": job_id},
                    )

                # Extract and log search queries performed
                if (
                    isinstance(response_data, dict)
                    and "execution_summary" in response_data
                ):
                    summary = response_data["execution_summary"]
                    if summary.get("previous_searches"):
                        logger.info("Agent search queries:", extra={"job_id": job_id})
                        for i, query in enumerate(summary["previous_searches"], 1):
                            # Clean up the query (remove newlines)
                            clean_query = query.strip().replace("\n", " ")
                            logger.info(
                                f"  {i}. {clean_query}", extra={"job_id": job_id}
                            )

                # Extract and log execution trace (ReAct loop details)
                if (
                    isinstance(response_data, dict)
                    and "execution_trace" in response_data
                ):
                    trace = response_data["execution_trace"]
                    if trace:
                        logger.info(
                            "--- Agent Reasoning Process ---", extra={"job_id": job_id}
                        )
                        for i, step in enumerate(trace, 1):
                            if step.get("thought"):
                                logger.info(
                                    f"Step {i} - Thought: {step['thought']}",
                                    extra={"job_id": job_id, "user_id": user_id},
                                )
                            logger.info(
                                f"Step {i} - Action: {step.get('action', 'unknown')}",
                                extra={"job_id": job_id},
                            )
                            if step.get("action_input"):
                                # Clean up action input
                                action_input = (
                                    str(step["action_input"]).strip().replace("\n", " ")
                                )
                                if len(action_input) > 200:
                                    action_input = action_input[:200] + "..."
                                logger.info(
                                    f"Step {i} - Input: {action_input}",
                                    extra={"job_id": job_id, "user_id": user_id},
                                )
                            if step.get("observation"):
                                # Clean up observation
                                observation = (
                                    str(step["observation"]).strip().replace("\n", " ")
                                )
                                if len(observation) > 200:
                                    observation = observation[:200] + "..."
                                logger.info(
                                    f"Step {i} - Result: {observation}",
                                    extra={"job_id": job_id, "user_id": user_id},
                                )
                        logger.info(
                            "--- End Reasoning Process ---", extra={"job_id": job_id}
                        )

                # Check if the agent returned a structured response with status
                if isinstance(response_data, dict):
                    # Check for loop detection - if detected, treat as error
                    # but don't use the loop message as result
                    if response_data.get("loop_detected"):
                        logger.warning(
                            f"Loop detected for job {job_id}, but agent may have provided valid response"
                        )
                        # Check if there's a valid agent response before the loop was detected
                        # The agent response should be in 'agent_response' or
                        # similar field
                        if "agent_response" in response_data:
                            result_text = response_data["agent_response"]
                            agent_status = "completed"
                        else:
                            # No valid response found, use the loop detection
                            # message
                            result_text = response_data.get(
                                "result", "Loop detected during execution"
                            )
                            agent_status = "error"
                    else:
                        # Normal processing without loop detection
                        agent_status = response_data.get("status", "completed")
                        requires_human_input = response_data.get(
                            "requires_action", False
                        ) or response_data.get("requiresAction", False)

                        # Extract the actual agent response text
                        if "result" in response_data:
                            result_data = response_data["result"]
                            if isinstance(result_data, dict):
                                result_text = (
                                    result_data.get("response", "")
                                    or result_data.get("output", "")
                                    or result_data.get("text", "")
                                    or result_data.get("answer", "")
                                    or str(result_data)
                                )
                            else:
                                result_text = str(result_data)
                        elif "output" in response_data:
                            output_data = response_data["output"]
                            if isinstance(output_data, dict):
                                result_text = output_data.get("text", str(output_data))
                            else:
                                result_text = str(output_data)
                        else:
                            # Use the entire response as result text
                            result_text = str(response_data)

                        # If the agent explicitly indicated interruption
                        if agent_status == "interrupted":
                            requires_human_input = True
                            logger.info(
                                f"Agent explicitly requested interruption for job {job_id}"
                            )
                else:
                    # Fallback for non-structured responses
                    result_text = str(response_data)

                # Clean up the result if it's still showing internal structure
                if result_text.startswith("{") and "execution_metrics" in result_text:
                    try:
                        parsed_result = (
                            json.loads(result_text)
                            if isinstance(result_text, str)
                            else result_text
                        )
                        if (
                            isinstance(parsed_result, dict)
                            and "result" in parsed_result
                        ):
                            result_text = parsed_result["result"]
                    except BaseException:
                        pass

            # Additional check for human input request patterns (fallback)
            if "human_interruption" in capabilities and not requires_human_input:
                requires_human_input = detect_human_input_request(result_text)
                if requires_human_input:
                    logger.info(
                        f"Detected human input request pattern in response for job {job_id}"
                    )
                    logger.info(
                        "Agent requires human input to continue",
                        extra={"job_id": job_id, "user_id": user_id},
                    )

            end_time = datetime.utcnow()
            execution_time = (end_time - start_time).total_seconds()
            logger.info(
                f"Agent execution completed in {execution_time:.2f} seconds",
                extra={"job_id": job_id, "user_id": user_id},
            )
            append_execution_log(
                job_id,
                "info",
                f"Agent execution completed in {execution_time:.2f} seconds",
            )

            # Always save conversation for human-in-the-loop functionality
            # This ensures we have conversation history available in the UI
            logger.info("Saving conversation history", extra={"job_id": job_id})
            if human_response:
                # For continuations, save the actual human response, not the
                # formatted prompt
                save_conversation_turn(
                    session_id, agent_id, human_response, result_text
                )
            else:
                # For new jobs, save the original prompt
                save_conversation_turn(
                    session_id, agent_id, original_prompt, result_text
                )

            # Update job based on result
            if requires_human_input:
                logger.warning(
                    "Job interrupted - human input required", extra={"job_id": job_id}
                )
                append_execution_log(
                    job_id, "warning", "Job interrupted - human input required"
                )
                update_task_status(job_id, "interrupted")
                update_task_field(job_id, "requiresAction", True)

                return {
                    "status": "interrupted",
                    "result": result_text,
                    "requiresAction": True,
                    "sessionId": session_id,
                    "executionTime": execution_time,
                }
            else:
                logger.info("Job completed successfully", extra={"job_id": job_id})
                append_execution_log(job_id, "success", "Job completed successfully")
                update_task_status(job_id, "completed")
                update_task_field(job_id, "finalResult", result_text)
                update_task_field(job_id, "requiresAction", False)

                return {
                    "status": "completed",
                    "result": result_text,
                    "sessionId": session_id,
                    "executionTime": execution_time,
                }

        except Exception as bedrock_error:
            logger.error(
                "Bedrock invocation failed",
                extra={"error": str(bedrock_error)},
                exc_info=True,
            )
            logger.error(
                "Agent execution failed",
                extra={"job_id": job_id, "error": str(bedrock_error)},
                exc_info=True,
            )
            append_execution_log(job_id, "error", "Agent execution failed")
            update_task_status(job_id, "error", "Agent execution failed")

            return {
                "status": "error",
                "error": "Agent execution failed",
                "sessionId": session_id,
            }

    except Exception as e:
        logger.error(
            "Job execution failed",
            extra={"error": str(e), "job_id": job_id},
            exc_info=True,
        )
        append_execution_log(job_id, "error", "Job execution failed")
        update_task_status(job_id, "error", "Job execution failed")

        return {
            "status": "error",
            "error": "Job execution failed",
            "sessionId": session_id,
        }


def extract_agent_id_from_arn(agent_arn: str) -> str:
    """
    Extract and validate agent ID from Bedrock Agent ARN

    AWS Bedrock agent IDs must:
    - Be alphanumeric only (no hyphens, underscores, etc.)
    - Be 10 characters or less

    If the ARN contains an invalid agent ID, we'll generate a valid one
    """
    # ARN format: arn:aws:bedrock-agent:region:account:agent/agent-id
    raw_agent_id = agent_arn.split("/")[-1]

    # Remove non-alphanumeric characters
    clean_agent_id = "".join(c for c in raw_agent_id if c.isalnum())

    # Truncate to 10 characters if needed
    if len(clean_agent_id) > 10:
        clean_agent_id = clean_agent_id[:10]

    # If the cleaned ID is empty or too short, generate a fallback
    if len(clean_agent_id) < 3:
        import hashlib

        # Generate a consistent hash-based ID from the original ARN
        hash_obj = hashlib.sha256(raw_agent_id.encode())
        clean_agent_id = hash_obj.hexdigest()[:10]

    logger.info(
        f"Converted agent ID '{raw_agent_id}' to Bedrock-compatible '{clean_agent_id}'"
    )
    return clean_agent_id


def detect_human_input_request(response_text: str) -> bool:
    """
    Detect if the agent is requesting human input

    This is a simple heuristic - in practice, agents designed for the platform
    should use specific markers or structured responses to indicate when they
    need human input.
    """

    # Look for common patterns that indicate the agent needs clarification
    human_input_indicators = [
        "I need clarification",
        "Could you clarify",
        "Please specify",
        "Which option would you prefer",
        "What would you like me to",
        "Please choose",
        "I need more information",
        "Could you provide more details",
    ]

    response_lower = response_text.lower()
    return any(
        indicator.lower() in response_lower for indicator in human_input_indicators
    )


def load_conversation_context(session_id: str, agent_id: str) -> str:
    """Load conversation context for agents that support continuity"""
    try:
        response = conversation_table.get_item(Key={"sessionId": session_id})

        if "Item" not in response:
            return ""

        conversation = response["Item"]

        # Verify this conversation belongs to the correct agent
        if conversation.get("agentId") != agent_id:
            return ""

        messages = conversation.get("messages", [])

        # Format recent conversation history (last 10 messages)
        context_parts = []
        for message in messages[-10:]:
            if message["type"] == "human":
                context_parts.append(f"Human: {message['content']}")
            else:
                context_parts.append(f"Assistant: {message['content']}")

        return "\n".join(context_parts)

    except Exception as e:
        logger.error(
            "Conversation context loading failed",
            extra={"error": str(e)},
            exc_info=True,
        )
        return ""


def append_human_turn(session_id: str, agent_id: str, human_input: str) -> None:
    """Persist the human side of a turn to the conversation store.

    Idempotent against the last human message, so calling this and then
    `save_conversation_turn` later will not duplicate the entry.
    """
    try:
        now = datetime.utcnow().isoformat()
        response = conversation_table.get_item(Key={"sessionId": session_id})

        if "Item" in response:
            conversation = response["Item"]
            messages = conversation.get("messages", [])
        else:
            conversation = {
                "sessionId": session_id,
                "agentId": agent_id,
                "createdAt": now,
                "messages": [],
            }
            messages = []

        last_human = next(
            (m for m in reversed(messages) if m.get("type") == "human"), None
        )
        if last_human and last_human.get("content") == human_input:
            return

        messages.append(
            {"type": "human", "content": human_input, "timestamp": now}
        )
        conversation["messages"] = messages
        conversation["updatedAt"] = now
        conversation["ttl"] = int(
            (datetime.utcnow() + timedelta(days=30)).timestamp()
        )
        conversation_table.put_item(Item=conversation)
    except Exception as e:
        # Non-fatal: save_conversation_turn will persist the turn later.
        logger.error(
            "Human turn pre-persist failed",
            extra={"error": str(e), "session_id": session_id},
            exc_info=True,
        )



def save_conversation_turn(
    session_id: str, agent_id: str, human_input: str, agent_response: str
):
    """Save a conversation turn for agents that support continuity"""

    try:
        now = datetime.utcnow().isoformat()

        # Get existing conversation or create new one
        response = conversation_table.get_item(Key={"sessionId": session_id})

        if "Item" in response:
            conversation = response["Item"]
            messages = conversation.get("messages", [])
        else:
            messages = []
            conversation = {
                "sessionId": session_id,
                "agentId": agent_id,
                "createdAt": now,
                "messages": [],
            }

        # Check if we already have this exact human input to avoid duplicates
        # This prevents duplicate messages when a job is interrupted and
        # resumed
        last_human_message = None
        for msg in reversed(messages):
            if msg["type"] == "human":
                last_human_message = msg
                break

        # Only add human message if it's different from the last one
        if not last_human_message or last_human_message["content"] != human_input:
            messages.append({"type": "human", "content": human_input, "timestamp": now})

        # Always add the agent response (it should be unique)
        messages.append({"type": "ai", "content": agent_response, "timestamp": now})

        # Update conversation
        conversation["messages"] = messages
        conversation["updatedAt"] = now
        conversation["ttl"] = int(
            (datetime.utcnow() + timedelta(days=30)).timestamp()
        )  # 30 day TTL

        # Save to DynamoDB
        conversation_table.put_item(Item=conversation)

    except Exception as e:
        logger.error("Conversation save failed", extra={"error": str(e)}, exc_info=True)


def update_task_status(job_id: str, status: str, error_message: str = None):
    """Update job status in DynamoDB"""
    try:
        update_expression = "SET #status = :status, updatedAt = :updatedAt"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":updatedAt": datetime.utcnow().isoformat(),
        }

        if error_message:
            update_expression += ", errorMessage = :errorMessage"
            expression_attribute_values[":errorMessage"] = error_message

        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
        )

    except Exception as e:
        logger.error("Job status update failed", extra={"error": str(e)}, exc_info=True)


ALLOWED_TASK_FIELDS = {"finalResult", "requiresAction", "executionLogs"}


def update_task_field(job_id: str, field_name: str, field_value: Any):
    """Update a specific field in the job (allowlisted fields only)"""
    if field_name not in ALLOWED_TASK_FIELDS:
        raise ValueError(f"Field '{field_name}' is not in the allowed fields list")

    try:
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #field = :value, updatedAt = :updatedAt",
            ExpressionAttributeNames={"#field": field_name},
            ExpressionAttributeValues={
                ":value": field_value,
                ":updatedAt": datetime.utcnow().isoformat(),
            },
        )

    except Exception as e:
        logger.error(
            "Job field update failed",
            extra={"field_name": field_name, "error": str(e)},
            exc_info=True,
        )


def append_execution_log(job_id: str, level: str, message: str):
    """Append a log entry to the job's executionLogs in DynamoDB"""
    try:
        response = task_table.get_item(Key={"jobId": job_id})
        if "Item" in response:
            job = response["Item"]
            execution_logs = job.get("executionLogs", [])

            execution_logs.append(
                {
                    "level": level,
                    "message": message,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

            # Keep only last 100 logs to avoid item size limits
            if len(execution_logs) > 100:
                execution_logs = execution_logs[-100:]

            task_table.update_item(
                Key={"jobId": job_id},
                UpdateExpression="SET executionLogs = :logs",
                ExpressionAttributeValues={":logs": execution_logs},
            )
    except Exception as e:
        logger.error(
            "Execution log storage failed",
            extra={"error": str(e)},
            exc_info=True,
        )


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create standardized API response"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ["ALLOWED_ORIGIN"],
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Amz-Date, X-Amz-Security-Token",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }
