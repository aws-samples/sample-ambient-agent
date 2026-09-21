# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Chat management Lambda.

Exposes CRUD + messaging routes for free-form chat threads against registered
agents. The actual message history lives in the shared conversation-store
table (keyed by sessionId), so this module only owns thread metadata and the
orchestration of message sends.

Routes (mounted at `/chats` by API Gateway):
    GET    /chats                      List the authenticated user's threads
    POST   /chats                      Create a new thread for {agentId, title?}
    GET    /chats/{threadId}           Fetch a single thread
    DELETE /chats/{threadId}           Delete a thread (messages TTL out)
    POST   /chats/{threadId}/messages  Send a user message on the thread

When a user sends a message, this Lambda writes the human turn to the
conversation-store, marks the thread `busy`, and async-invokes the
chat_execution Lambda which does the Bedrock AgentCore call and writes the AI
turn back to the conversation-store.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from authz import is_owner


logger = Logger(service="chat-management", level="INFO")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

CHAT_THREADS_TABLE = os.environ["CHAT_THREADS_TABLE"]
CONVERSATION_STORE_TABLE = os.environ["CONVERSATION_STORE_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
CHAT_EXECUTION_FUNCTION_NAME = os.environ["CHAT_EXECUTION_FUNCTION_NAME"]

threads_table = dynamodb.Table(CHAT_THREADS_TABLE)
conversation_table = dynamodb.Table(CONVERSATION_STORE_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    try:
        http_method: str = event["httpMethod"]
        resource: str = event["resource"]
        path_parameters = event.get("pathParameters") or {}
        body = json.loads(event["body"]) if event.get("body") else {}
        user_id = _extract_user_id(event)

        thread_id = path_parameters.get("threadId")

        if resource == "/chats" and http_method == "GET":
            return _list_threads(user_id)
        if resource == "/chats" and http_method == "POST":
            return _create_thread(user_id, body)
        if resource == "/chats/{threadId}" and http_method == "GET":
            return _get_thread(user_id, thread_id)
        if resource == "/chats/{threadId}" and http_method == "DELETE":
            return _delete_thread(user_id, thread_id)
        if (
            resource == "/chats/{threadId}/messages"
            and http_method == "POST"
        ):
            return _send_message(user_id, thread_id, body)

        return _response(404, {"error": "Route not found"})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Chat management failed", extra={"error": str(exc)})
        return _response(500, {"error": "Chat management failed"})


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _list_threads(user_id: str) -> Dict[str, Any]:
    """Return all chat threads owned by `user_id`, newest first."""
    response = threads_table.query(
        IndexName="userId-updatedAt-index",
        KeyConditionExpression=Key("userId").eq(user_id),
        ScanIndexForward=False,  # newest first
        Limit=100,
    )
    items = response.get("Items", [])
    return _response(200, {"threads": items, "total": len(items)})


def _create_thread(user_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    agent_id: Optional[str] = body.get("agentId")
    if not agent_id:
        return _response(400, {"error": "agentId is required"})

    agent_resp = agent_table.get_item(Key={"agentId": agent_id})
    if "Item" not in agent_resp:
        return _response(404, {"error": "Agent not found"})

    agent = agent_resp["Item"]

    # Ownership check: existence alone isn't enough - the caller must
    # also own the agent, otherwise any authenticated user could open a
    # chat thread against another user's registered agent (which may
    # carry different tool grants / a different execution role).
    # job_management.py's create_job does this same check.
    if not is_owner(agent, user_id):
        return _response(403, {"error": "Access denied to agent"})

    now = datetime.utcnow().isoformat()
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Enforce a reasonable thread-title length without trusting client input.
    title = (body.get("title") or f"Chat with {agent.get('agentName', 'agent')}")
    title = title.strip()[:120]

    item: Dict[str, Any] = {
        "threadId": thread_id,
        "userId": user_id,
        "agentId": agent_id,
        "agentName": agent.get("agentName", "Unknown Agent"),
        "title": title,
        "sessionId": session_id,
        "status": "idle",
        "lastMessagePreview": "",
        "createdAt": now,
        "updatedAt": now,
        # 30 days in epoch seconds
        "ttl": int((datetime.utcnow() + timedelta(days=30)).timestamp()),
    }
    threads_table.put_item(Item=item)

    return _response(201, item)


def _get_thread(user_id: str, thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return _response(400, {"error": "threadId is required"})
    thread = _load_owned_thread(user_id, thread_id)
    if not thread:
        return _response(404, {"error": "Thread not found"})
    return _response(200, thread)


def _delete_thread(user_id: str, thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return _response(400, {"error": "threadId is required"})
    thread = _load_owned_thread(user_id, thread_id)
    if not thread:
        return _response(404, {"error": "Thread not found"})

    threads_table.delete_item(Key={"threadId": thread_id})
    # Best-effort: remove the conversation record as well. Messages have their
    # own TTL, so a failure here is non-fatal.
    try:
        conversation_table.delete_item(Key={"sessionId": thread["sessionId"]})
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Conversation delete failed; will TTL naturally",
            extra={"threadId": thread_id},
        )

    return _response(200, {"message": "Thread deleted"})


def _send_message(
    user_id: str, thread_id: Optional[str], body: Dict[str, Any]
) -> Dict[str, Any]:
    if not thread_id:
        return _response(400, {"error": "threadId is required"})

    message = (body.get("message") or "").strip()
    if not message:
        return _response(400, {"error": "message is required"})

    # Bound message size before it's persisted and replayed into future
    # invocations' history. Without this, a single huge message inflates
    # every subsequent Bedrock call for the thread (denial-of-wallet) and
    # can push the conversation-store item toward DynamoDB's 400KB limit.
    MAX_MESSAGE_LENGTH = 8000
    if len(message) > MAX_MESSAGE_LENGTH:
        return _response(
            400,
            {"error": f"message must be {MAX_MESSAGE_LENGTH} characters or fewer"},
        )

    thread = _load_owned_thread(user_id, thread_id)
    if not thread:
        return _response(404, {"error": "Thread not found"})

    # Reject sends while the agent is still thinking to avoid interleaving
    # turns. The UI should keep the send button disabled while status == busy.
    if thread.get("status") == "busy":
        return _response(
            409,
            {"error": "Agent is still processing the previous message"},
        )

    session_id = thread["sessionId"]
    now = datetime.utcnow().isoformat()

    # Append the human turn to conversation-store so the UI picks it up
    # immediately on the next poll, before the agent responds.
    _append_conversation_turn(
        session_id, thread["agentId"], user_id, "human", message
    )


    # Mark the thread busy and update preview.
    awaiting_human = thread.get("status") == "awaiting_human"
    threads_table.update_item(
        Key={"threadId": thread_id},
        UpdateExpression=(
            "SET #status = :status, updatedAt = :updatedAt, "
            "lastMessagePreview = :preview"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "busy",
            ":updatedAt": now,
            ":preview": message[:140],
        },
    )

    # Kick off async execution. Pass `user_id` so the executor can scope work
    # to this user without needing the original JWT.
    payload = {
        "threadId": thread_id,
        "userId": user_id,
        "agentId": thread["agentId"],
        "sessionId": session_id,
        "message": message,
        # If the agent was awaiting human input, forward the message as the
        # human response so the agent's existing continuation flow kicks in.
        "isHumanResponse": awaiting_human,
    }
    lambda_client.invoke(
        FunctionName=CHAT_EXECUTION_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload),
    )

    return _response(
        202,
        {
            "threadId": thread_id,
            "sessionId": session_id,
            "status": "busy",
            "message": "Message accepted; polling conversation for the reply",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_owned_thread(user_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
    """Return the thread record only if `user_id` owns it."""
    resp = threads_table.get_item(Key={"threadId": thread_id})
    item = resp.get("Item")
    if not is_owner(item, user_id):
        return None
    return item


def _ensure_conversation_exists(
    session_id: str, agent_id: str, user_id: str
) -> None:
    """Conditional put so concurrent writers do not overwrite each other."""
    now = datetime.utcnow().isoformat()
    ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    try:
        conversation_table.put_item(
            Item={
                "sessionId": session_id,
                "agentId": agent_id,
                "userId": user_id,
                "createdAt": now,
                "updatedAt": now,
                "messages": [],
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(sessionId)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def _append_conversation_turn(
    session_id: str, agent_id: str, user_id: str, msg_type: str, content: str
) -> None:
    """Append a turn atomically via list_append.

    Uses the same shape as job_execution's writer so the existing
    conversation-management GET endpoint returns chat messages without
    change.
    """
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



def _extract_user_id(event: Dict[str, Any]) -> str:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
    )
    user_id = claims.get("sub") or claims.get("cognito:username")
    if not user_id:
        raise PermissionError("Unable to determine user identity from request")
    return user_id


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ["ALLOWED_ORIGIN"],
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type, Authorization, X-Amz-Date, X-Amz-Security-Token"
            ),
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }
