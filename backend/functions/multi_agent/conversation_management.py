# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError


# Configure logging
logger = Logger(service="conversation-management", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")

# Environment variables
CONVERSATION_STORE_TABLE = os.environ["CONVERSATION_STORE_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
REGION = os.environ["REGION"]

# Get DynamoDB tables
conversation_table = dynamodb.Table(CONVERSATION_STORE_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for conversation management

    This function handles:
    - GET /conversations/{sessionId} - Get conversation history
    - GET /conversations/{sessionId}/context - Get formatted context for agent
    - POST /conversations/{sessionId}/messages - Add message to conversation
    """

    try:
        http_method = event["httpMethod"]
        path_parameters = event.get("pathParameters") or {}
        query_parameters = event.get("queryStringParameters") or {}
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}

        # Extract user ID from Cognito JWT
        user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
        session_id = path_parameters.get("sessionId")

        if not session_id:
            return create_response(400, {"error": "Session ID required"})

        if http_method == "GET":
            # Check if this is a context request
            if event["resource"].endswith("/context"):
                return get_conversation_context(session_id, user_id, query_parameters)
            else:
                return get_conversation_history(session_id, user_id, query_parameters)
        elif http_method == "POST":
            return add_conversation_message(session_id, user_id, body)
        else:
            return create_response(405, {"error": "Method not allowed"})

    except Exception as e:
        logger.error(
            "Conversation management failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "Conversation management failed"})


def get_conversation_history(
    session_id: str, user_id: str, query_params: Dict[str, Any]
) -> Dict[str, Any]:
    """Get conversation history for a session"""
    try:
        # Get conversation from DynamoDB
        response = conversation_table.get_item(Key={"sessionId": session_id})

        if "Item" not in response:
            return create_response(404, {"error": "Conversation not found"})

        conversation = response["Item"]

        # Verify user has access to this conversation
        # We'll check if the user owns any jobs with this session ID
        if not verify_user_access_to_session(user_id, session_id):
            return create_response(403, {"error": "Access denied"})

        # Get agent information
        agent_id = conversation.get("agentId")
        agent_name = "Unknown Agent"
        if agent_id:
            agent_response = agent_table.get_item(Key={"agentId": agent_id})
            if "Item" in agent_response:
                agent_name = agent_response["Item"].get("agentName", "Unknown Agent")

        # Format response
        messages = conversation.get("messages", [])

        # Apply pagination if requested
        limit = int(query_params.get("limit", 50))
        offset = int(query_params.get("offset", 0))

        paginated_messages = messages[offset : offset + limit]

        return create_response(
            200,
            {
                "sessionId": session_id,
                "agentId": agent_id,
                "agentName": agent_name,
                "messages": paginated_messages,
                "totalMessages": len(messages),
                "hasMore": offset + limit < len(messages),
                "createdAt": conversation.get("createdAt"),
                "updatedAt": conversation.get("updatedAt"),
            },
        )

    except Exception as e:
        logger.error(
            "Conversation history retrieval failed",
            extra={"error": str(e)},
            exc_info=True,
        )
        return create_response(500, {"error": "Failed to get conversation history"})


def get_conversation_context(
    session_id: str, user_id: str, query_params: Dict[str, Any]
) -> Dict[str, Any]:
    """Get formatted conversation context for agent continuation"""
    try:
        # Get conversation history
        history_response = get_conversation_history(session_id, user_id, query_params)

        if history_response["statusCode"] != 200:
            return history_response

        history_data = json.loads(history_response["body"])
        messages = history_data["messages"]

        # Format context for agent
        context_parts = []

        # Add conversation summary
        if messages:
            context_parts.append("CONVERSATION HISTORY:")

            # Show recent messages (last 10 exchanges)
            recent_messages = messages[-20:] if len(messages) > 20 else messages

            for msg in recent_messages:
                timestamp = datetime.fromisoformat(
                    msg["timestamp"].replace("Z", "+00:00")
                ).strftime("%H:%M")
                if msg["type"] == "human":
                    context_parts.append(f"[{timestamp}] Human: {msg['content']}")
                else:
                    context_parts.append(f"[{timestamp}] Assistant: {msg['content']}")

            context_parts.append("")  # Empty line for separation

        # Add summary of key information if conversation is long
        if len(messages) > 10:
            context_parts.append("CONVERSATION SUMMARY:")
            context_parts.append(
                "This is a continuation of an ongoing conversation. Key context:"
            )

            # Extract key information from early messages
            early_messages = messages[:5]
            for msg in early_messages:
                if msg["type"] == "human" and len(msg["content"]) > 20:
                    context_parts.append(
                        f"- User initially requested: {msg['content'][:100]}..."
                    )
                    break

            context_parts.append("")

        formatted_context = "\n".join(context_parts)

        return create_response(
            200,
            {
                "sessionId": session_id,
                "formattedContext": formatted_context,
                "messageCount": len(messages),
                "lastMessageTime": messages[-1]["timestamp"] if messages else None,
            },
        )

    except Exception as e:
        logger.error(
            "Conversation context retrieval failed",
            extra={"error": str(e)},
            exc_info=True,
        )
        return create_response(500, {"error": "Failed to get conversation context"})


def _ensure_conversation_exists(
    session_id: str, agent_id: Optional[str], user_id: str
) -> None:
    """Create the conversation item if it does not exist (conditional put)."""
    now = datetime.utcnow().isoformat()
    ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
    try:
        conversation_table.put_item(
            Item={
                "sessionId": session_id,
                "agentId": agent_id,
                "userId": user_id,
                "messages": [],
                "createdAt": now,
                "updatedAt": now,
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(sessionId)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def add_conversation_message(
    session_id: str, user_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Append a message to the conversation using atomic list_append."""
    try:
        message_type = body.get("type")  # 'human' or 'ai'
        content = body.get("content")
        agent_id = body.get("agentId")

        if not message_type or not content:
            return create_response(400, {"error": "Message type and content required"})

        if message_type not in ["human", "ai"]:
            return create_response(400, {"error": "Message type must be human or ai"})

        if not verify_user_access_to_session(user_id, session_id):
            return create_response(403, {"error": "Access denied"})

        _ensure_conversation_exists(session_id, agent_id, user_id)

        now = datetime.utcnow().isoformat()
        ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
        new_message = {
            "type": message_type,
            "content": content,
            "timestamp": now,
        }

        result = conversation_table.update_item(
            Key={"sessionId": session_id},
            UpdateExpression=(
                "SET messages = list_append(if_not_exists(messages, :empty), :turn), "
                "updatedAt = :now, "
                "#ttl = :ttl"
            ),
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":empty": [],
                ":turn": [new_message],
                ":now": now,
                ":ttl": ttl,
            },
            ReturnValues="UPDATED_NEW",
        )

        message_count = len(result.get("Attributes", {}).get("messages", []))

        return create_response(
            200,
            {
                "message": "Message added successfully",
                "messageCount": message_count,
                "sessionId": session_id,
            },
        )

    except Exception as e:
        logger.error(
            "Conversation message addition failed",
            extra={"error": str(e)},
            exc_info=True,
        )
        return create_response(500, {"error": "Failed to add message"})



def verify_user_access_to_session(user_id: str, session_id: str) -> bool:
    """Verify that the user has access to this session (fail-closed).

    The conversation-store record itself carries `userId` (populated at
    creation time by any of the write paths), so a single get_item is
    sufficient. This avoids the O(N) scans the earlier implementation
    issued against the jobs and chat-threads tables.
    """
    try:
        resp = conversation_table.get_item(
            Key={"sessionId": session_id},
            AttributesToGet=["userId"],
        )
        item = resp.get("Item")
        if not item:
            return False
        owner = item.get("userId")
        if owner and owner == user_id:
            return True
        return False
    except Exception as e:
        logger.error(
            "User access verification failed", extra={"error": str(e)}, exc_info=True
        )
        return False  # Deny access on error (fail-closed)



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
