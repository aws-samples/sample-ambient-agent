# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Conversation store helpers.

All writes use `UpdateItem + list_append` so concurrent job runs on the
same session append to the message log atomically. Reads return the
latest snapshot.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

from .clients import conversation_table

logger = Logger(service="job-execution", level="INFO", child=True)


def load_conversation_context(session_id: str, agent_id: str) -> str:
    """Return the last 10 messages formatted for a continuation prompt."""
    try:
        response = conversation_table.get_item(Key={"sessionId": session_id})
        if "Item" not in response:
            return ""
        conversation = response["Item"]
        if conversation.get("agentId") != agent_id:
            return ""
        lines = []
        for message in conversation.get("messages", [])[-10:]:
            prefix = "Human" if message.get("type") == "human" else "Assistant"
            lines.append(f"{prefix}: {message.get('content', '')}")
        return "\n".join(lines)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Conversation context loading failed",
            extra={"error": str(exc)},
            exc_info=True,
        )
        return ""


def _ensure_conversation_exists(
    session_id: str, agent_id: str, user_id: Optional[str] = None
) -> None:
    """Create the conversation item if it does not exist.

    A conditional put on `attribute_not_exists(sessionId)` means two
    concurrent writers each racing to create the record are safe: one
    succeeds, the other is harmlessly rejected.
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


def _append_messages_atomic(
    session_id: str,
    agent_id: str,
    new_messages: List[Dict[str, Any]],
    user_id: Optional[str] = None,
) -> None:
    """Atomically append one or more messages to the session's log."""
    if not new_messages:
        return
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
            ":turn": new_messages,
            ":now": now,
            ":ttl": ttl,
        },
    )


def _last_human_message_content(session_id: str) -> Optional[str]:
    try:
        resp = conversation_table.get_item(Key={"sessionId": session_id})
        item = resp.get("Item")
        if not item:
            return None
        for msg in reversed(item.get("messages", [])):
            if msg.get("type") == "human":
                return msg.get("content")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "Last human message read failed",
            extra={"sessionId": session_id, "error": str(exc)},
        )
    return None


def append_human_turn(
    session_id: str,
    agent_id: str,
    human_input: str,
    user_id: Optional[str] = None,
) -> None:
    """Persist the human side of a turn.

    Skips the write if the last human message already matches the input
    (the typical retry / re-invocation path during a job continuation).
    """
    try:
        if _last_human_message_content(session_id) == human_input:
            return
        now = datetime.utcnow().isoformat()
        _append_messages_atomic(
            session_id,
            agent_id,
            [{"type": "human", "content": human_input, "timestamp": now}],
            user_id=user_id,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Human turn pre-persist failed",
            extra={"sessionId": session_id, "error": str(exc)},
            exc_info=True,
        )


def save_conversation_turn(
    session_id: str,
    agent_id: str,
    human_input: str,
    agent_response: str,
    user_id: Optional[str] = None,
) -> None:
    """Persist a full human + AI turn."""
    try:
        now = datetime.utcnow().isoformat()
        new_messages: List[Dict[str, Any]] = []
        if _last_human_message_content(session_id) != human_input:
            new_messages.append(
                {"type": "human", "content": human_input, "timestamp": now}
            )
        new_messages.append(
            {"type": "ai", "content": agent_response, "timestamp": now}
        )
        _append_messages_atomic(
            session_id, agent_id, new_messages, user_id=user_id
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Conversation save failed",
            extra={"error": str(exc)},
            exc_info=True,
        )
