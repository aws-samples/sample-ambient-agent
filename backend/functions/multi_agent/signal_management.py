# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Signal Management Lambda Function

Handles CRUD operations for ambient signals that trigger agent responses.
"""

import json
import boto3
import uuid
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError
from decimal import Decimal

# Configure logging
logger = Logger(service="signal-management", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")
lambda_client = boto3.client("lambda")

# Environment variables
SIGNALS_TABLE = os.environ.get("AMBIENT_SIGNALS_TABLE")
AGENT_REGISTRY_TABLE = os.environ.get("AGENT_REGISTRY_TABLE")
SIGNAL_PROCESSOR_FUNCTION_NAME = os.environ.get("SIGNAL_PROCESSOR_FUNCTION_NAME")
REGION = os.environ.get("REGION", "us-east-1")
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]


def decimal_default(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError


def get_user_id_from_event(event: Dict[str, Any]) -> str:
    """Extract user ID from the event context"""
    try:
        # Extract from Cognito JWT token
        claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
        return claims.get("sub") or claims.get("cognito:username", "unknown")
    except Exception as e:
        logger.warning("User ID extraction failed", extra={"error": str(e)})
        return "unknown"


def create_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new ambient signal"""
    try:
        body = json.loads(event.get("body", "{}"))
        user_id = get_user_id_from_event(event)

        # Validate required fields
        required_fields = ["signalName", "signalType", "agentId"]
        for field in required_fields:
            if not body.get(field):
                return {
                    "statusCode": 400,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": f"Missing required field: {field}"}),
                }

        # Verify agent exists
        agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)
        try:
            agent_response = agent_table.get_item(Key={"agentId": body["agentId"]})
            if "Item" not in agent_response:
                return {
                    "statusCode": 404,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "Agent not found"}),
                }
        except ClientError as e:
            logger.error(
                "Agent verification failed", extra={"error": str(e)}, exc_info=True
            )
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Failed to verify agent"}),
            }

        # Create signal record
        signal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        signal_item = {
            "signalId": signal_id,
            "userId": user_id,
            "signalName": body["signalName"],
            "signalType": body["signalType"],
            "agentId": body["agentId"],
            "description": body.get("description", ""),
            "configuration": body.get("configuration", {}),
            "enabled": body.get("enabled", True),
            "triggerCount": 0,
            "createdAt": now,
            "updatedAt": now,
        }

        # Store in DynamoDB
        signals_table = dynamodb.Table(SIGNALS_TABLE)
        signals_table.put_item(Item=signal_item)

        # Create S3 trigger if signal is enabled and type is S3
        if signal_item["enabled"] and signal_item["signalType"] == "s3_file_upload":
            create_s3_trigger(signal_item)

        logger.info(
            "Signal created successfully",
            extra={
                "signal_id": signal_id,
                "user_id": user_id,
                "signal_name": body["signalName"],
            },
        )

        return {
            "statusCode": 201,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(signal_item),
        }

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Invalid JSON in request body"}),
        }
    except Exception as e:
        logger.error("Signal creation failed", extra={"error": str(e)}, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def list_signals(event: Dict[str, Any]) -> Dict[str, Any]:
    """List signals for the authenticated user"""
    try:
        user_id = get_user_id_from_event(event)
        query_params = event.get("queryStringParameters") or {}

        # Parse pagination parameters
        page = int(query_params.get("page", 1))
        page_size = min(int(query_params.get("pageSize", 10)), 100)

        # Parse filter parameters
        status_filter = query_params.get("status")
        signal_type_filter = query_params.get("signalType")
        agent_id_filter = query_params.get("agentId")

        signals_table = dynamodb.Table(SIGNALS_TABLE)

        # Try to query signals for user using GSI
        try:
            response = signals_table.query(
                IndexName="userId-signalName-index",
                KeyConditionExpression="userId = :userId",
                ExpressionAttributeValues={":userId": user_id},
            )
            signals = response.get("Items", [])
        except ClientError as e:
            logger.error(
                "GSI query failed for signal listing",
                extra={"error": str(e)},
                exc_info=True,
            )
            signals = []

        # Apply filters
        if status_filter:
            if status_filter == "active":
                signals = [s for s in signals if s.get("enabled", False)]
            elif status_filter == "inactive":
                signals = [s for s in signals if not s.get("enabled", False)]

        if signal_type_filter:
            signals = [s for s in signals if s.get("signalType") == signal_type_filter]

        if agent_id_filter:
            signals = [s for s in signals if s.get("agentId") == agent_id_filter]

        # Sort by creation date (newest first)
        signals.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

        # Apply pagination
        total = len(signals)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_signals = signals[start_idx:end_idx]

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "signals": paginated_signals,
                    "total": total,
                    "page": page,
                    "pageSize": page_size,
                },
                default=decimal_default,
            ),
        }

    except Exception as e:
        logger.error("Signal listing failed", extra={"error": str(e)}, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def get_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Get a specific signal by ID"""
    try:
        signal_id = event["pathParameters"]["signalId"]
        user_id = get_user_id_from_event(event)

        signals_table = dynamodb.Table(SIGNALS_TABLE)
        response = signals_table.get_item(Key={"signalId": signal_id})

        if "Item" not in response:
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Signal not found"}),
            }

        signal = response["Item"]

        # Check ownership
        if signal.get("userId") != user_id:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Access denied"}),
            }

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(signal, default=decimal_default),
        }

    except Exception as e:
        logger.error("Signal retrieval failed", extra={"error": str(e)}, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def update_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing signal"""
    try:
        signal_id = event["pathParameters"]["signalId"]
        user_id = get_user_id_from_event(event)
        body = json.loads(event.get("body", "{}"))

        signals_table = dynamodb.Table(SIGNALS_TABLE)

        # Get existing signal
        response = signals_table.get_item(Key={"signalId": signal_id})
        if "Item" not in response:
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Signal not found"}),
            }

        signal = response["Item"]

        # Check ownership
        if signal.get("userId") != user_id:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Access denied"}),
            }

        # Update fields
        update_expression = "SET updatedAt = :updatedAt"
        expression_values = {":updatedAt": datetime.now(timezone.utc).isoformat()}

        if "signalName" in body:
            update_expression += ", signalName = :signalName"
            expression_values[":signalName"] = body["signalName"]

        if "description" in body:
            update_expression += ", description = :description"
            expression_values[":description"] = body["description"]

        if "configuration" in body:
            update_expression += ", configuration = :configuration"
            expression_values[":configuration"] = body["configuration"]

        if "enabled" in body:
            update_expression += ", enabled = :enabled"
            expression_values[":enabled"] = body["enabled"]

            # Update S3 trigger if enabled status changed
            if body["enabled"] != signal.get("enabled"):
                if body["enabled"]:
                    create_s3_trigger({**signal, **body})
                else:
                    delete_s3_trigger(
                        signal_id, signal.get("configuration", {}).get("bucketName")
                    )

        # Update in DynamoDB
        response = signals_table.update_item(
            Key={"signalId": signal_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW",
        )

        updated_signal = response["Attributes"]

        logger.info(
            "Signal updated successfully",
            extra={"signal_id": signal_id, "user_id": user_id},
        )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(updated_signal, default=decimal_default),
        }

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Invalid JSON in request body"}),
        }
    except Exception as e:
        logger.error("Signal update failed", extra={"error": str(e)}, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def delete_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a signal"""
    try:
        signal_id = event["pathParameters"]["signalId"]
        user_id = get_user_id_from_event(event)

        signals_table = dynamodb.Table(SIGNALS_TABLE)

        # Get existing signal to check ownership
        response = signals_table.get_item(Key={"signalId": signal_id})
        if "Item" not in response:
            return {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Signal not found"}),
            }

        signal = response["Item"]

        # Check ownership
        if signal.get("userId") != user_id:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Access denied"}),
            }

        # Delete S3 trigger if exists
        delete_s3_trigger(signal_id, signal.get("configuration", {}).get("bucketName"))

        # Delete from DynamoDB
        signals_table.delete_item(Key={"signalId": signal_id})

        logger.info(
            "Signal deleted successfully",
            extra={"signal_id": signal_id, "user_id": user_id},
        )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": "Signal deleted successfully"}),
        }

    except Exception as e:
        logger.error("Signal deletion failed", extra={"error": str(e)}, exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def create_s3_trigger(signal: Dict[str, Any]) -> None:
    """Create S3 bucket notification to trigger Lambda for S3 file upload signals"""
    try:
        if signal["signalType"] != "s3_file_upload":
            return

        configuration = signal.get("configuration", {})
        bucket_name = configuration.get("bucketName")
        prefix = configuration.get("prefix", "")
        suffix = configuration.get("suffix", "")

        if not bucket_name:
            logger.warning(f"No bucket name configured for signal {signal['signalId']}")
            return

        if not SIGNAL_PROCESSOR_FUNCTION_NAME:
            logger.error("SIGNAL_PROCESSOR_FUNCTION_NAME not set")
            return

        # Get bucket region to ensure Lambda and bucket are in same region
        try:
            bucket_location = s3_client.get_bucket_location(Bucket=bucket_name)
            bucket_region = bucket_location.get("LocationConstraint")
            # Note: us-east-1 returns None for LocationConstraint
            if bucket_region is None:
                bucket_region = "us-east-1"
        except ClientError as e:
            logger.error(
                "Bucket location retrieval failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return

        # Get Lambda function ARN - use bucket's region
        lambda_arn = f"arn:aws:lambda:{bucket_region}:{ACCOUNT_ID}:function:{SIGNAL_PROCESSOR_FUNCTION_NAME}"

        # Add Lambda permission for S3 to invoke the function
        statement_id = f"s3-invoke-signal-{signal['signalId']}"
        try:
            lambda_client.add_permission(
                FunctionName=SIGNAL_PROCESSOR_FUNCTION_NAME,
                StatementId=statement_id,
                Action="lambda:InvokeFunction",
                Principal="s3.amazonaws.com",
                SourceArn=f"arn:aws:s3:::{bucket_name}",
                SourceAccount=ACCOUNT_ID,
            )
            logger.info(
                "Lambda permission added for S3", extra={"bucket_name": bucket_name}
            )
        except lambda_client.exceptions.ResourceConflictException:
            logger.info(
                "Lambda permission already exists", extra={"statement_id": statement_id}
            )
        except Exception as e:
            logger.error(
                "Lambda permission addition failed",
                extra={"error": str(e)},
                exc_info=True,
            )
            return

        # Get existing bucket notification configuration
        try:
            notification_config = s3_client.get_bucket_notification_configuration(
                Bucket=bucket_name
            )
            # Remove ResponseMetadata if present
            notification_config.pop("ResponseMetadata", None)
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucket":
                logger.error(
                    "Bucket does not exist", extra={"bucket_name": bucket_name}
                )
                return
            # If no notification configuration exists, start with empty
            notification_config = {}

        # Get existing Lambda configurations or initialize empty list
        lambda_configs = notification_config.get("LambdaFunctionConfigurations", [])

        # Create new notification configuration for this signal
        new_config = {
            "Id": f"signal-{signal['signalId']}",
            "LambdaFunctionArn": lambda_arn,
            "Events": ["s3:ObjectCreated:*"],
        }

        # Add filter rules if prefix or suffix specified
        filter_rules = []
        if prefix:
            filter_rules.append({"Name": "prefix", "Value": prefix})
        if suffix:
            filter_rules.append({"Name": "suffix", "Value": suffix})

        if filter_rules:
            new_config["Filter"] = {"Key": {"FilterRules": filter_rules}}

        # Remove any existing configuration for this signal (in case of update)
        lambda_configs = [
            c for c in lambda_configs if c.get("Id") != f"signal-{signal['signalId']}"
        ]

        # Add new configuration
        lambda_configs.append(new_config)

        # Update bucket notification configuration
        notification_config["LambdaFunctionConfigurations"] = lambda_configs

        s3_client.put_bucket_notification_configuration(
            Bucket=bucket_name, NotificationConfiguration=notification_config
        )

        logger.info(
            "S3 trigger created",
            extra={"signal_id": signal["signalId"], "bucket_name": bucket_name},
        )

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "AccessDenied":
            logger.error(
                f"Access denied when configuring S3 bucket {bucket_name}. Ensure the Lambda has s3:PutBucketNotification permission."
            )
        else:
            logger.error(
                "S3 trigger creation failed", extra={"error": str(e)}, exc_info=True
            )
    except Exception as e:
        logger.error(
            "S3 trigger creation failed", extra={"error": str(e)}, exc_info=True
        )


def delete_s3_trigger(signal_id: str, bucket_name: Optional[str]) -> None:
    """Delete S3 bucket notification for a signal"""
    try:
        if not bucket_name:
            logger.warning("No bucket name provided", extra={"signal_id": signal_id})
            return

        # Get existing bucket notification configuration
        try:
            notification_config = s3_client.get_bucket_notification_configuration(
                Bucket=bucket_name
            )
            notification_config.pop("ResponseMetadata", None)
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucket":
                logger.warning(
                    "Bucket does not exist", extra={"bucket_name": bucket_name}
                )
                return
            logger.warning(
                f"No notification configuration found for bucket {bucket_name}"
            )
            return

        # Get existing Lambda configurations
        lambda_configs = notification_config.get("LambdaFunctionConfigurations", [])

        # Remove configuration for this signal
        original_count = len(lambda_configs)
        lambda_configs = [
            c for c in lambda_configs if c.get("Id") != f"signal-{signal_id}"
        ]

        if len(lambda_configs) == original_count:
            logger.info("No S3 trigger found", extra={"signal_id": signal_id})
        else:
            # Update bucket notification configuration
            notification_config["LambdaFunctionConfigurations"] = lambda_configs

            s3_client.put_bucket_notification_configuration(
                Bucket=bucket_name, NotificationConfiguration=notification_config
            )

            logger.info(
                "S3 trigger deleted",
                extra={"signal_id": signal_id, "bucket_name": bucket_name},
            )

        # Remove Lambda permission
        statement_id = f"s3-invoke-signal-{signal_id}"
        try:
            lambda_client.remove_permission(
                FunctionName=SIGNAL_PROCESSOR_FUNCTION_NAME, StatementId=statement_id
            )
            logger.info(
                "Lambda permission removed", extra={"statement_id": statement_id}
            )
        except lambda_client.exceptions.ResourceNotFoundException:
            logger.info(
                "Lambda permission not found", extra={"statement_id": statement_id}
            )
        except Exception as e:
            logger.warning("Lambda permission removal failed", extra={"error": str(e)})

    except ClientError as e:
        logger.error(
            "S3 trigger deletion failed", extra={"error": str(e)}, exc_info=True
        )
    except Exception as e:
        logger.error(
            "S3 trigger deletion failed", extra={"error": str(e)}, exc_info=True
        )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler for signal management"""
    try:
        http_method = event.get("httpMethod", "")
        path = event.get("path", "")

        # Add CORS headers
        cors_headers = {
            "Access-Control-Allow-Origin": os.environ["ALLOWED_ORIGIN"],
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Cache-Control": "no-store",
        }

        # Handle preflight requests
        if http_method == "OPTIONS":
            return {"statusCode": 200, "headers": cors_headers, "body": ""}

        # Route requests
        if http_method == "POST" and path == "/signals":
            response = create_signal(event)
        elif http_method == "GET" and path == "/signals":
            response = list_signals(event)
        elif http_method == "GET" and "/signals/" in path:
            response = get_signal(event)
        elif http_method == "PUT" and "/signals/" in path:
            response = update_signal(event)
        elif http_method == "DELETE" and "/signals/" in path:
            response = delete_signal(event)
        else:
            response = {
                "statusCode": 404,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Not found"}),
            }

        # Add CORS headers to response
        if "headers" not in response:
            response["headers"] = {}
        response["headers"].update(cors_headers)

        return response

    except Exception as e:
        logger.error(
            "Unhandled error in signal management",
            extra={"error": str(e)},
            exc_info=True,
        )
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", **cors_headers},
            "body": json.dumps({"error": "Internal server error"}),
        }
