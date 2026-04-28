# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import uuid
import os
from datetime import datetime
from typing import Dict, Any
from aws_lambda_powertools import Logger

# Configure logging
logger = Logger(service="agent-management", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime")
bedrock_agentcore = boto3.client("bedrock-agentcore")

# Environment variables
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
REGION = os.environ["REGION"]

# Get DynamoDB table
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for agent management operations

    Supports:
    - GET /agents - List user's agents
    - POST /agents - Register new agent
    - GET /agents/{agentId} - Get agent details
    - PUT /agents/{agentId} - Update agent
    - DELETE /agents/{agentId} - Delete agent
    - POST /agents/{agentId}/test - Test agent connectivity
    """

    try:
        # Extract request information
        http_method = event["httpMethod"]
        path = event["path"]
        path_parameters = event.get("pathParameters") or {}
        query_parameters = event.get("queryStringParameters") or {}
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}

        # Extract user ID from Cognito JWT
        user_id = event["requestContext"]["authorizer"]["claims"]["sub"]

        logger.info(
            "Processing request",
            extra={"http_method": http_method, "path": path, "user_id": user_id},
        )

        # Route to appropriate handler
        if http_method == "GET" and path == "/agents":
            return list_agents(user_id, query_parameters)
        elif http_method == "POST" and path == "/agents":
            return register_agent(user_id, body)
        elif http_method == "GET" and "agentId" in path_parameters:
            return get_agent(user_id, path_parameters["agentId"])
        elif http_method == "PUT" and "agentId" in path_parameters:
            return update_agent(user_id, path_parameters["agentId"], body)
        elif http_method == "DELETE" and "agentId" in path_parameters:
            return delete_agent(user_id, path_parameters["agentId"])
        else:
            return create_response(404, {"error": "Not found"})

    except Exception as e:
        logger.error(
            "Request processing failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "Internal server error"})


def list_agents(user_id: str, query_params: Dict[str, str]) -> Dict[str, Any]:
    """List agents for a user with optional filtering"""
    try:
        # Build query parameters
        filter_expression = None
        expression_attribute_values = {"#userId": user_id}

        # Add status filter if provided
        status = query_params.get("status")
        if status and status != "all":
            filter_expression = "#status = :status"
            expression_attribute_values[":status"] = status

        # Add type filter if provided
        agent_type = query_params.get("type")
        if agent_type and agent_type != "all":
            if filter_expression:
                filter_expression += " AND #agentType = :agentType"
            else:
                filter_expression = "#agentType = :agentType"
            expression_attribute_values[":agentType"] = agent_type

        # Query using GSI
        query_params_ddb = {
            "IndexName": "userId-agentName-index",
            "KeyConditionExpression": "#userId = :userId",
            "ExpressionAttributeNames": {"#userId": "userId"},
            "ExpressionAttributeValues": {":userId": user_id},
        }

        if filter_expression:
            query_params_ddb["FilterExpression"] = filter_expression
            query_params_ddb["ExpressionAttributeNames"].update(
                {"#status": "status", "#agentType": "agentType"}
            )
            if ":status" in expression_attribute_values:
                query_params_ddb["ExpressionAttributeValues"][":status"] = (
                    expression_attribute_values[":status"]
                )
            if ":agentType" in expression_attribute_values:
                query_params_ddb["ExpressionAttributeValues"][":agentType"] = (
                    expression_attribute_values[":agentType"]
                )

        response = agent_table.query(**query_params_ddb)

        agents = response.get("Items", [])

        # Handle pagination (simplified)
        page = max(1, int(query_params.get("page", 1)))
        page_size = min(max(1, int(query_params.get("pageSize", 10))), 100)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_agents = agents[start_idx:end_idx]

        return create_response(
            200,
            {
                "agents": paginated_agents,
                "total": len(agents),
                "page": page,
                "pageSize": page_size,
            },
        )

    except Exception as e:
        logger.error("Agent listing failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to list agents"})


def register_agent(user_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Register a new agent"""
    try:
        # Validate required fields
        required_fields = ["agentName", "agentArn", "agentType"]
        for field in required_fields:
            if field not in body:
                return create_response(
                    400, {"error": f"Missing required field: {field}"}
                )

        # Validate agent type
        valid_types = ["user_initiated", "scheduled", "ambient"]
        if body["agentType"] not in valid_types:
            return create_response(
                400, {"error": f"Invalid agent type. Must be one of: {valid_types}"}
            )

        # Validate ARN format (basic validation)
        agent_arn = body["agentArn"]
        if not agent_arn.startswith("arn:aws:bedrock-agent"):
            return create_response(400, {"error": "Invalid agent ARN format"})

        # Create agent record
        agent_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        agent = {
            "agentId": agent_id,
            "userId": user_id,
            "agentName": body["agentName"],
            "agentArn": agent_arn,
            "agentType": body["agentType"],
            "description": body.get("description", ""),
            "capabilities": body.get(
                "capabilities", ["human_interruption", "conversation_continuity"]
            ),
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "metadata": body.get("metadata", {}),
        }

        # Save to DynamoDB
        agent_table.put_item(Item=agent)

        logger.info(
            "Agent registered successfully",
            extra={
                "agent_id": agent_id,
                "user_id": user_id,
                "agent_name": body["agentName"],
            },
        )
        return create_response(201, agent)

    except Exception as e:
        logger.error(
            "Agent registration failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "Failed to register agent"})


def get_agent(user_id: str, agent_id: str) -> Dict[str, Any]:
    """Get agent details"""
    try:
        response = agent_table.get_item(Key={"agentId": agent_id})

        if "Item" not in response:
            return create_response(404, {"error": "Agent not found"})

        agent = response["Item"]

        # Verify ownership
        if agent["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        return create_response(200, agent)

    except Exception as e:
        logger.error("Agent retrieval failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to get agent"})


def update_agent(user_id: str, agent_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Update agent configuration"""
    try:
        # Get existing agent
        response = agent_table.get_item(Key={"agentId": agent_id})

        if "Item" not in response:
            return create_response(404, {"error": "Agent not found"})

        agent = response["Item"]

        # Verify ownership
        if agent["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        # Update allowed fields
        updatable_fields = [
            "agentName",
            "description",
            "status",
            "capabilities",
            "metadata",
        ]
        update_expression = "SET updatedAt = :updatedAt"
        expression_attribute_values = {":updatedAt": datetime.utcnow().isoformat()}

        for field in updatable_fields:
            if field in body:
                update_expression += f", {field} = :{field}"
                expression_attribute_values[f":{field}"] = body[field]

        # Update in DynamoDB
        agent_table.update_item(
            Key={"agentId": agent_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
        )

        # Get updated agent
        updated_response = agent_table.get_item(Key={"agentId": agent_id})
        updated_agent = updated_response["Item"]

        logger.info(
            "Agent updated successfully",
            extra={"agent_id": agent_id, "user_id": user_id},
        )
        return create_response(200, updated_agent)

    except Exception as e:
        logger.error("Agent update failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to update agent"})


def delete_agent(user_id: str, agent_id: str) -> Dict[str, Any]:
    """Delete an agent"""
    try:
        # Get existing agent to verify ownership
        response = agent_table.get_item(Key={"agentId": agent_id})

        if "Item" not in response:
            return create_response(404, {"error": "Agent not found"})

        agent = response["Item"]

        # Verify ownership
        if agent["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        # Delete from DynamoDB
        agent_table.delete_item(Key={"agentId": agent_id})

        logger.info(
            "Agent deleted successfully",
            extra={"agent_id": agent_id, "user_id": user_id},
        )
        return create_response(200, {"message": "Agent deleted successfully"})

    except Exception as e:
        logger.error("Agent deletion failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to delete agent"})

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
