# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Signal Processor Lambda Function

Processes triggered ambient signals and creates jobs for assigned agents.
"""

import json
import boto3
import uuid
import os
from datetime import datetime, timezone
from typing import Dict, Any
from aws_lambda_powertools import Logger

# Configure logging
logger = Logger(service="signal-processor", level="INFO")

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

# Environment variables
SIGNALS_TABLE = os.environ.get("AMBIENT_SIGNALS_TABLE")
TASK_REGISTRY_TABLE = os.environ.get("TASK_REGISTRY_TABLE")
TASK_EXECUTION_FUNCTION = os.environ.get("TASK_EXECUTION_FUNCTION_NAME")
REGION = os.environ.get("REGION", "us-east-1")


def process_s3_signal(event: Dict[str, Any]) -> Dict[str, Any]:
    """Process S3 file upload signal from direct S3 event notification"""
    try:
        # S3 events come in Records array
        if "Records" not in event:
            logger.error("No Records found in S3 event")
            return {"statusCode": 400, "body": "Invalid S3 event"}

        results = []

        # Process each S3 record
        for record in event["Records"]:
            # Verify this is an S3 event
            if record.get("eventSource") != "aws:s3":
                logger.warning(f"Skipping non-S3 event: {record.get('eventSource')}")
                continue

            # Extract S3 event details
            s3_info = record.get("s3", {})
            bucket_name = s3_info.get("bucket", {}).get("name")
            object_key = s3_info.get("object", {}).get("key")
            event_time = record.get("eventTime")
            event_name = record.get("eventName")

            if not bucket_name or not object_key:
                logger.error("Missing S3 bucket or object key in record")
                continue

            logger.info(
                "Processing S3 signal",
                extra={
                    "bucket_name": bucket_name,
                    "object_key": object_key,
                    "event_name": event_name,
                },
            )

            # Find matching signals for this bucket and key
            signals_table = dynamodb.Table(SIGNALS_TABLE)

            # Scan for enabled S3 signals matching this bucket, scoped by bucket name
            response = signals_table.scan(
                FilterExpression="signalType = :type AND enabled = :enabled AND configuration.bucketName = :bucket",
                ExpressionAttributeValues={
                    ":type": "s3_file_upload",
                    ":enabled": True,
                    ":bucket": bucket_name,
                },
            )

            matching_signals = []
            for signal in response.get("Items", []):
                config = signal.get("configuration", {})
                signal_bucket = config.get("bucketName")
                signal_prefix = config.get("prefix", "")
                signal_suffix = config.get("suffix", "")

                # Check if this signal matches the event
                if signal_bucket == bucket_name:
                    # Check prefix match
                    prefix_match = not signal_prefix or object_key.startswith(
                        signal_prefix
                    )
                    # Check suffix match
                    suffix_match = not signal_suffix or object_key.endswith(
                        signal_suffix
                    )

                    if prefix_match and suffix_match:
                        matching_signals.append(signal)

            logger.info(
                "Matching signals found",
                extra={
                    "signal_count": len(matching_signals),
                    "bucket": bucket_name,
                    "key": object_key,
                },
            )

            # Process each matching signal
            for signal in matching_signals:
                try:
                    result = create_task_for_signal(
                        signal,
                        {
                            "bucket": bucket_name,
                            "key": object_key,
                            "eventTime": event_time,
                            "eventName": event_name,
                            "eventSource": "s3",
                            "size": s3_info.get("object", {}).get("size"),
                            "eTag": s3_info.get("object", {}).get("eTag"),
                        },
                    )
                    results.append(result)

                    # Update signal trigger count
                    update_signal_stats(signal["signalId"])

                except Exception as e:
                    logger.error(f"Error processing signal {signal['signalId']}: {e}")
                    results.append({"signalId": signal["signalId"], "error": str(e)})

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": f"Processed {len(results)} signal triggers",
                    "results": results,
                }
            ),
        }

    except Exception as e:
        logger.error(
            "S3 signal processing failed", extra={"error": str(e)}, exc_info=True
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }


def create_task_for_signal(
    signal: Dict[str, Any], payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a job for a triggered signal"""
    try:
        job_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Create a descriptive prompt based on the signal type and payload
        if signal["signalType"] == "s3_file_upload":
            prompt = f"""A new file has been uploaded to S3 and triggered the ambient signal '{
                signal["signalName"]
            }'.

File Details:
- Bucket: {payload["bucket"]}
- Key: {payload["key"]}
- Upload Time: {payload.get("eventTime", "Unknown")}

Signal Description: {signal.get("description", "No description provided")}

Please analyze this file upload event and provide a summary or take appropriate action based on the signal configuration."""
        else:
            prompt = f"Ambient signal '{
                signal['signalName']
            }' was triggered with payload: {json.dumps(payload, indent=2)}"

        # Create job record
        task_item = {
            "jobId": job_id,
            "userId": signal["userId"],
            "agentId": signal["agentId"],
            "jobName": f"Signal: {signal['signalName']} - {payload.get('key', 'Event')}",
            "jobType": "user_initiated",  # Signal-triggered jobs are treated as user-initiated
            "status": "idle",
            "sessionId": session_id,
            "prompt": prompt,
            "requiresAction": False,
            "createdAt": now,
            "updatedAt": now,
            "metadata": {
                "signalId": signal["signalId"],
                "signalType": signal["signalType"],
                "triggerPayload": payload,
            },
        }

        # Store job in DynamoDB
        task_table = dynamodb.Table(TASK_REGISTRY_TABLE)
        task_table.put_item(Item=task_item)

        logger.info(
            "Job created for signal",
            extra={"signal_id": signal["signalId"], "job_id": job_id},
        )

        # Optionally auto-execute the job (for now, just create it)
        # In a more advanced implementation, you might want to auto-execute
        # based on signal configuration

        return {"signalId": signal["signalId"], "jobId": job_id, "status": "created"}

    except Exception as e:
        logger.error(
            "Job creation for signal failed", extra={"error": str(e)}, exc_info=True
        )
        raise


def update_signal_stats(signal_id: str) -> None:
    """Update signal trigger statistics"""
    try:
        signals_table = dynamodb.Table(SIGNALS_TABLE)
        now = datetime.now(timezone.utc).isoformat()

        # Increment trigger count and update last triggered time
        signals_table.update_item(
            Key={"signalId": signal_id},
            UpdateExpression="ADD triggerCount :inc SET lastTriggered = :now, updatedAt = :now",
            ExpressionAttributeValues={":inc": 1, ":now": now},
        )

        logger.info("Signal stats updated", extra={"signal_id": signal_id})

    except Exception as e:
        logger.error(
            "Signal stats update failed", extra={"error": str(e)}, exc_info=True
        )


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler for signal processing"""
    try:
        logger.info(
            "Signal processing event received",
            extra={"event": json.dumps(event, default=str)},
        )

        # Check if this is an S3 event (direct S3 notification)
        if "Records" in event and len(event["Records"]) > 0:
            first_record = event["Records"][0]
            if first_record.get("eventSource") == "aws:s3":
                return process_s3_signal(event)

        # If not an S3 event, log and return error
        logger.warning(
            "Unsupported event type", extra={"event_keys": list(event.keys())}
        )
        return {
            "statusCode": 400,
            "body": json.dumps(
                {"error": "Unsupported event type. Expected S3 event notification."}
            ),
        }

    except Exception as e:
        logger.error(
            "Unhandled error in signal processor",
            extra={"error": str(e)},
            exc_info=True,
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
