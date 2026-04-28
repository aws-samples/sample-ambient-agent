# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Chat execution Lambda.

Async-invoked by chat_management when the user sends a message on a thread.
Responsibilities:
  1. Look up the thread's agent in the registry and invoke the Bedrock
     AgentCore runtime (using a region-aware client so cross-region agents
     work the same way they do for job execution).
  2. Parse the agent's structured response to decide whether it completed or
     is asking for human input.
  3. Append the AI turn(s) to conversation-store so the UI picks them up via
     polling.
  4. Update the thread status to `idle` (completed) or `awaiting_human`
     (interrupted), plus refresh the last-message preview.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError


logger = Logger(service="chat-execution", level="INFO")

dynamodb = boto3.resource("dynamodb")

CHAT_THREADS_TABLE = os.environ["CHAT_THREADS_TABLE"]
CONVERSATION_STORE_TABLE = os.environ["CONVERSATION_STORE_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]

threads_table = dynamodb.Table(CHAT_THREADS_TABLE)
conversation_table = dynamodb.Table(CONVERSATION_STORE_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """Entrypoint for async chat-execution invocations."""
    thread_id = event.get("threadId")
    session_id = event.get("sessionId")
    agent_id = event.get("agentId")
    user_id = event.get("userId")
    user_message = event.get("message", "")
    is_human_response = bool(event.get("isHumanResponse"))


    if not (thread_id and session_id and agent_id):
        logger.error("Missing required event fields", extra={"event": event})
        return {"statusCode": 400, "body": "Missing required fields"}

    try:
        agent_response = agent_table.get_item(Key={"agentId": agent_id})
        if "Item" not in agent_response:
            _complete_thread_with_error(
                thread_id, session_id, agent_id, "Agent not found", user_id=user_id
            )
            return {"statusCode": 404, "body": "Agent not found"}


        agent_arn = agent_response["Item"]["agentArn"]

        # Build the prompt. For a human-response turn, include the previous AI
        # question as context so the agent can tie the answer back to what it
        # asked. For a normal turn, just forward the user's message - the
        # conversation-store record already carries the history and the agent
        # replays it internally via session state.
        prompt = _build_prompt(session_id, user_message, is_human_response)

        # Session IDs must be >= 33 chars for AgentCore.
        invocation_session = session_id
        if len(invocation_session) < 33:
            invocation_session = (
                f"{invocation_session}-"
                + "".join(str(uuid.uuid4()).replace("-", "") for _ in range(2))[
                    : 33 - len(invocation_session)
                ]
            )

        payload = json.dumps(
            {
                "prompt": prompt,
                "session_id": invocation_session,
                "job_id": thread_id,  # agent treats this as an opaque id
                "metadata": {
                    "source": "chat",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
        )

        agentcore_client = _agentcore_client_for_arn(agent_arn)
        logger.info(
            "Invoking AgentCore runtime",
            extra={"threadId": thread_id, "agentArn": agent_arn},
        )
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=invocation_session,
            payload=payload,
            qualifier="DEFAULT",
        )

        result_text, requires_human_input = _parse_agent_response(response)

        # Append AI turn (the agent's reply, or its question if it interrupted)
        _append_conversation_turn(
            session_id,
            agent_id,
            "ai",
            result_text or "(no response)",
            user_id=user_id,
        )


        new_status = "awaiting_human" if requires_human_input else "idle"
        _update_thread_status(
            thread_id,
            new_status,
            preview=(result_text or "")[:140],
        )

        return {"statusCode": 200, "body": "OK"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "Chat execution failed",
            extra={"threadId": thread_id, "error": str(exc)},
        )
        _complete_thread_with_error(
            thread_id,
            session_id,
            agent_id,
            "Agent execution failed",
            user_id=user_id,
        )
        return {"statusCode": 500, "body": "Chat execution failed"}



# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(session_id: str, user_message: str, is_human_response: bool) -> str:
    """Construct the prompt sent to the agent for this turn."""
    # Load existing conversation so we can inline a short history. The agent
    # keeps its own per-session state across invocations, but including recent
    # turns in the prompt keeps multi-turn coherence when the cold-start has
    # discarded in-memory state.
    history = _recent_history_text(session_id, limit=10)

    if is_human_response:
        return (
            "CONVERSATION CONTEXT:\n"
            f"{history}\n\n"
            f"HUMAN RESPONSE: {user_message}\n\n"
            "Please continue based on the human's response above."
        )

    if history:
        return (
            "CONVERSATION CONTEXT:\n"
            f"{history}\n\n"
            f"CURRENT REQUEST: {user_message}"
        )
    return user_message


def _recent_history_text(session_id: str, limit: int = 10) -> str:
    resp = conversation_table.get_item(Key={"sessionId": session_id})
    if "Item" not in resp:
        return ""
    messages = resp["Item"].get("messages", [])
    # Exclude the most recent human message - that's the current turn.
    trimmed = messages[:-1] if messages else []
    tail = trimmed[-limit:]
    lines = []
    for msg in tail:
        prefix = "Human" if msg.get("type") == "human" else "Assistant"
        lines.append(f"{prefix}: {msg.get('content', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_agent_response(response: Dict[str, Any]) -> tuple[str, bool]:
    """Parse the AgentCore reply into (text, requires_human_input).

    Contract: the agent returns exactly one of
        {"status": "completed",   "result": ...}
        {"status": "interrupted", "question": ...}
        {"status": "error",       "error":  ...}
    """
    if "response" not in response:
        return "", False

    body_bytes = response["response"].read()
    try:
        data = json.loads(body_bytes)
    except (TypeError, ValueError):
        return body_bytes.decode("utf-8", errors="replace"), False

    if not isinstance(data, dict):
        return str(data), False

    status = data.get("status")
    if status == "interrupted":
        question = (
            data.get("question")
            or data.get("human_input_question")
            or data.get("result")
            or ""
        )
        return str(question), True

    if status == "error":
        return str(data.get("error") or data.get("result") or "Agent error"), False

    # Default: treat anything else as completed text.
    result = data.get("result", "")
    if isinstance(result, dict):
        return (
            result.get("response", "")
            or result.get("output", "")
            or result.get("text", "")
            or result.get("answer", "")
            or str(result)
        ), False
    return str(result), False



# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _ensure_conversation_exists(
    session_id: str, agent_id: str, user_id: Optional[str] = None
) -> None:
    """Create the conversation item if it does not exist.

    Conditional put so concurrent writers do not overwrite each other.
    """
    now = datetime.utcnow().isoformat()
    ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    item: Dict[str, Any] = {
        "sessionId": session_id,
        "agentId": agent_id,
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
        "ttl": ttl,
    }
    if user_id:
        item["userId"] = user_id
    try:
        conversation_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(sessionId)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def _append_conversation_turn(
    session_id: str,
    agent_id: str,
    msg_type: str,
    content: str,
    user_id: Optional[str] = None,
) -> None:
    """Append a single turn to the conversation using atomic list_append."""
    _ensure_conversation_exists(session_id, agent_id, user_id)

    now = datetime.utcnow().isoformat()
    ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    conversation_table.update_item(
        Key={"sessionId": session_id},
        UpdateExpression=(
            "SET messages = list_append(if_not_exists(messages, :empty), :turn), "
            "updatedAt = :now, "
            "#ttl = :ttl"
        ),
        ExpressionAttributeNames={"#ttl": "ttl"},
        ExpressionAttributeValues={
            ":empty": [],
            ":turn": [{"type": msg_type, "content": content, "timestamp": now}],
            ":now": now,
            ":ttl": ttl,
        },
    )



def _update_thread_status(thread_id: str, status: str, preview: str = "") -> None:
    now = datetime.utcnow().isoformat()
    update_expr = "SET #status = :status, updatedAt = :updatedAt"
    expr_values: Dict[str, Any] = {":status": status, ":updatedAt": now}
    if preview:
        update_expr += ", lastMessagePreview = :preview"
        expr_values[":preview"] = preview
    threads_table.update_item(
        Key={"threadId": thread_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=expr_values,
    )


def _complete_thread_with_error(
    thread_id: str,
    session_id: str,
    agent_id: str,
    message: str,
    user_id: Optional[str] = None,
) -> None:
    try:
        _append_conversation_turn(
            session_id, agent_id, "ai", f"Error: {message}", user_id=user_id
        )
    finally:
        _update_thread_status(thread_id, "idle", preview=f"Error: {message}")



# ---------------------------------------------------------------------------
# AgentCore client builder (shared with job_execution behaviour)
# ---------------------------------------------------------------------------

def _agentcore_client_for_arn(agent_arn: str):
    """Return a bedrock-agentcore client pinned to the region in the agent ARN."""
    try:
        arn_region: Optional[str] = agent_arn.split(":")[3]
    except (IndexError, AttributeError):
        arn_region = None
    if arn_region:
        return boto3.client("bedrock-agentcore", region_name=arn_region)
    return boto3.client("bedrock-agentcore")
