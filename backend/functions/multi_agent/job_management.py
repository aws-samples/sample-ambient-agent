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
logger = Logger(service="job-management", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")

# Environment variables
TASK_REGISTRY_TABLE = os.environ["TASK_REGISTRY_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
REGION = os.environ["REGION"]

# Get DynamoDB tables
task_table = dynamodb.Table(TASK_REGISTRY_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for job management operations

    Supports:
    - GET /jobs - List user's jobs
    - POST /jobs - Create new job
    - GET /jobs/{jobId} - Get job details
    - PUT /jobs/{jobId} - Update job (for human responses)
    - DELETE /jobs/{jobId} - Delete job
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
            "Processing job management request",
            extra={"http_method": http_method, "path": path, "user_id": user_id},
        )

        # Route to appropriate handler
        if http_method == "GET" and path == "/jobs":
            return list_tasks(user_id, query_parameters)
        elif http_method == "POST" and path == "/jobs":
            return create_task(user_id, body)
        elif http_method == "GET" and "jobId" in path_parameters:
            return get_task(user_id, path_parameters["jobId"])
        elif http_method == "PUT" and "jobId" in path_parameters:
            return update_task(user_id, path_parameters["jobId"], body)
        elif http_method == "DELETE" and "jobId" in path_parameters:
            return delete_task(user_id, path_parameters["jobId"])
        else:
            return create_response(404, {"error": "Not found"})

    except Exception as e:
        logger.error(
            "Request processing failed", extra={"error": str(e)}, exc_info=True
        )
        return create_response(500, {"error": "Internal server error"})


def list_tasks(user_id: str, query_params: Dict[str, str]) -> Dict[str, Any]:
    """List jobs for a user with optional filtering"""
    try:
        logger.info(
            "Listing jobs for user",
            extra={"user_id": user_id, "query_params": query_params},
        )

        # Get status filter
        status = query_params.get("status")

        # Build query parameters
        expression_attribute_values = {":userId": user_id}
        expression_attribute_names = {"#userId": "userId"}

        # Choose the appropriate query strategy based on status filter
        if status and status != "all":
            # Use the userId-status-index GSI for status filtering
            query_params_ddb = {
                "IndexName": "userId-status-index",
                "KeyConditionExpression": "#userId = :userId AND #status = :status",
                "ExpressionAttributeNames": {"#userId": "userId", "#status": "status"},
                "ExpressionAttributeValues": {":userId": user_id, ":status": status},
            }
        else:
            # Query all jobs for the user (no status filter)
            query_params_ddb = {
                "IndexName": "userId-status-index",
                "KeyConditionExpression": "#userId = :userId",
                "ExpressionAttributeNames": expression_attribute_names,
                "ExpressionAttributeValues": expression_attribute_values,
            }

        # Add additional filters if provided
        filter_parts = []

        # Add agent filter if provided
        agent_id = query_params.get("agentId")
        if agent_id:
            filter_parts.append("agentId = :agentId")
            expression_attribute_values[":agentId"] = agent_id

        # Add job type filter if provided
        job_type = query_params.get("jobType")
        if job_type:
            filter_parts.append("jobType = :jobType")
            expression_attribute_values[":jobType"] = job_type

        # Apply additional filters if any
        if filter_parts:
            query_params_ddb["FilterExpression"] = " AND ".join(filter_parts)
            query_params_ddb["ExpressionAttributeValues"].update(
                expression_attribute_values
            )

        logger.info(
            "DynamoDB query parameters", extra={"query_params": query_params_ddb}
        )

        response = task_table.query(**query_params_ddb)

        jobs = response.get("Items", [])
        logger.info("Jobs retrieved", extra={"job_count": len(jobs)})

        # Handle pagination (simplified)
        page = max(1, int(query_params.get("page", 1)))
        page_size = min(max(1, int(query_params.get("pageSize", 10))), 100)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_tasks = jobs[start_idx:end_idx]

        return create_response(
            200,
            {
                "jobs": paginated_tasks,
                "total": len(jobs),
                "page": page,
                "pageSize": page_size,
            },
        )

    except Exception as e:
        logger.error("Job listing failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to list jobs"})


def create_task(user_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new job"""
    try:
        # Validate required fields
        required_fields = ["agentId", "jobName", "prompt", "jobType"]
        for field in required_fields:
            if field not in body:
                return create_response(
                    400, {"error": f"Missing required field: {field}"}
                )

        # Validate job type
        valid_types = ["user_initiated", "scheduled"]
        if body["jobType"] not in valid_types:
            return create_response(
                400, {"error": f"Invalid job type. Must be one of: {valid_types}"}
            )

        # Verify agent exists and belongs to user
        agent_response = agent_table.get_item(Key={"agentId": body["agentId"]})
        if "Item" not in agent_response:
            return create_response(400, {"error": "Agent not found"})

        agent = agent_response["Item"]
        if agent["userId"] != user_id:
            return create_response(403, {"error": "Access denied to agent"})

        # Create job record
        job_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        job = {
            "jobId": job_id,
            "userId": user_id,
            "agentId": body["agentId"],
            "jobName": body["jobName"],
            "jobType": body["jobType"],
            "status": "idle",
            "sessionId": session_id,
            "prompt": body["prompt"],
            "requiresAction": False,
            "createdAt": now,
            "updatedAt": now,
        }

        # Add schedule configuration for scheduled jobs
        if body["jobType"] == "scheduled":
            schedule = body.get("schedule")
            if not schedule:
                return create_response(
                    400, {"error": "Schedule configuration required for scheduled jobs"}
                )

            # Calculate next run time
            next_run = calculate_next_run(schedule)
            if not next_run:
                return create_response(400, {"error": "Invalid schedule configuration"})

            job["schedule"] = {
                "type": schedule["type"],
                "pattern": schedule["pattern"],
                "enabled": schedule.get("enabled", True),
                "nextRun": next_run,
            }
            job["nextRun"] = next_run  # For GSI queries

        # Save to DynamoDB
        task_table.put_item(Item=job)

        logger.info(
            "Job created successfully",
            extra={"job_id": job_id, "user_id": user_id, "agent_id": body["agentId"]},
        )
        return create_response(201, job)

    except Exception as e:
        logger.error("Job creation failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to create job"})


def get_task(user_id: str, job_id: str) -> Dict[str, Any]:
    """Get job details"""
    try:
        response = task_table.get_item(Key={"jobId": job_id})

        if "Item" not in response:
            return create_response(404, {"error": "Job not found"})

        job = response["Item"]

        # Verify ownership
        if job["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        return create_response(200, job)

    except Exception as e:
        logger.error("Job retrieval failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to get job"})


def update_task(user_id: str, job_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Update job (primarily for human responses and schedule changes)"""
    try:
        # Get existing job
        response = task_table.get_item(Key={"jobId": job_id})

        if "Item" not in response:
            return create_response(404, {"error": "Job not found"})

        job = response["Item"]

        # Verify ownership
        if job["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        # Update allowed fields
        updatable_fields = ["jobName", "status", "requiresAction", "schedule"]
        update_expression = "SET updatedAt = :updatedAt"
        expression_attribute_values = {":updatedAt": datetime.utcnow().isoformat()}

        for field in updatable_fields:
            if field in body:
                if field == "schedule" and job["jobType"] == "scheduled":
                    # Update schedule and recalculate next run
                    schedule = body[field]
                    next_run = calculate_next_run(schedule)
                    if next_run:
                        update_expression += f", {field} = :{field}, nextRun = :nextRun"
                        expression_attribute_values[f":{field}"] = schedule
                        expression_attribute_values[":nextRun"] = next_run
                else:
                    update_expression += f", {field} = :{field}"
                    expression_attribute_values[f":{field}"] = body[field]

        # Update in DynamoDB
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
        )

        # Get updated job
        updated_response = task_table.get_item(Key={"jobId": job_id})
        updated_task = updated_response["Item"]

        logger.info(
            "Job updated successfully", extra={"job_id": job_id, "user_id": user_id}
        )
        return create_response(200, updated_task)

    except Exception as e:
        logger.error("Job update failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to update job"})


def delete_task(user_id: str, job_id: str) -> Dict[str, Any]:
    """Delete a job"""
    try:
        # Get existing job to verify ownership
        response = task_table.get_item(Key={"jobId": job_id})

        if "Item" not in response:
            return create_response(404, {"error": "Job not found"})

        job = response["Item"]

        # Verify ownership
        if job["userId"] != user_id:
            return create_response(403, {"error": "Access denied"})

        # Delete from DynamoDB
        task_table.delete_item(Key={"jobId": job_id})

        logger.info(
            "Job deleted successfully", extra={"job_id": job_id, "user_id": user_id}
        )
        return create_response(200, {"message": "Job deleted successfully"})

    except Exception as e:
        logger.error("Job deletion failed", extra={"error": str(e)}, exc_info=True)
        return create_response(500, {"error": "Failed to delete job"})


def calculate_next_run(schedule: Dict[str, Any]) -> str:
    """
    Calculate the next run time for a scheduled job

    This is a simplified implementation. In production, you might want to use
    a more sophisticated scheduling library like croniter.
    """
    try:
        schedule_type = schedule.get("type")
        pattern = schedule.get("pattern", "").lower()

        if not schedule.get("enabled", True):
            return None

        now = datetime.utcnow()

        if schedule_type == "one_time":
            # For one-time jobs, pattern should be an ISO datetime string
            try:
                return datetime.fromisoformat(
                    pattern.replace("Z", "+00:00")
                ).isoformat()
            except BaseException:
                return None

        elif schedule_type == "recurring":
            # Simple recurring patterns
            if "daily" in pattern:
                # Extract time if specified (e.g., "daily at 9am")
                if "at" in pattern:
                    try:
                        time_part = pattern.split("at")[1].strip()
                        if "am" in time_part or "pm" in time_part:
                            # Parse time like "9am" or "2:30pm"
                            time_str = (
                                time_part.replace("am", "").replace("pm", "").strip()
                            )
                            if ":" in time_str:
                                hour, minute = map(int, time_str.split(":"))
                            else:
                                hour, minute = int(time_str), 0

                            if "pm" in time_part and hour != 12:
                                hour += 12
                            elif "am" in time_part and hour == 12:
                                hour = 0

                            # Schedule for next occurrence
                            next_run = now.replace(
                                hour=hour, minute=minute, second=0, microsecond=0
                            )
                            if next_run <= now:
                                next_run += timedelta(days=1)

                            return next_run.isoformat()
                    except BaseException:
                        pass

                # Default to next day at current time
                return (now + timedelta(days=1)).isoformat()

            elif "hourly" in pattern or "hour" in pattern:
                # Extract interval if specified (e.g., "every 6 hours")
                hours = 1
                if "every" in pattern:
                    try:
                        parts = pattern.split()
                        for i, part in enumerate(parts):
                            if part.isdigit():
                                hours = int(part)
                                break
                    except BaseException:
                        pass

                return (now + timedelta(hours=hours)).isoformat()

            elif "weekly" in pattern:
                return (now + timedelta(weeks=1)).isoformat()

        # Default: schedule for 1 hour from now
        return (now + timedelta(hours=1)).isoformat()

    except Exception as e:
        logger.error(
            "Next run calculation failed", extra={"error": str(e)}, exc_info=True
        )
        return None


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
