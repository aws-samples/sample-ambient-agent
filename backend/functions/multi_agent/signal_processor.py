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
from typing import Dict, Any, Optional
from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import MetricUnit, Metrics
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent_function,
)
from boto3.dynamodb.conditions import Key



# Configure logging
logger = Logger(service="signal-processor", level="INFO")
metrics = Metrics(namespace="AmbientAgents", service="signal-processor")


# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")
sqs_client = boto3.client("sqs")

# Environment variables
SIGNALS_TABLE = os.environ.get("AMBIENT_SIGNALS_TABLE")
TASK_REGISTRY_TABLE = os.environ.get("TASK_REGISTRY_TABLE")
TASK_EXECUTION_FUNCTION = os.environ.get("TASK_EXECUTION_FUNCTION_NAME")
REGION = os.environ.get("REGION", "us-east-1")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "")
# When set, the signal processor can enqueue a job directly onto the
# worker queue for signals configured with `autoExecute: true`. Without
# this env var the auto-execute branch short-circuits and the job stays
# in `idle` for a human to run manually, preserving the review-first
# default behaviour.
JOB_EXECUTION_QUEUE_URL = os.environ.get("JOB_EXECUTION_QUEUE_URL", "")


# Powertools idempotency persistence layer. Keyed on
# `(signalId, bucket, key, eventName, eTag)` so an S3 retry of the same
# notification cannot create duplicate jobs. The idempotency table has a
# TTL so stored keys expire after the configured window.
_idempotency_layer: Optional[DynamoDBPersistenceLayer] = None
if IDEMPOTENCY_TABLE:
    _idempotency_layer = DynamoDBPersistenceLayer(table_name=IDEMPOTENCY_TABLE)

_idempotency_config = IdempotencyConfig(
    event_key_jmespath="idempotency_key",
    expires_after_seconds=3600,
    raise_on_no_idempotency_key=False,
    use_local_cache=True,
)


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

            # Find matching signals for this bucket and key via the
            # bucketName-signalId GSI. Keeps latency flat as the signals
            # table grows; the old scan was O(tableSize).
            signals_table = dynamodb.Table(SIGNALS_TABLE)

            response = signals_table.query(
                IndexName="bucketName-signalId-index",
                KeyConditionExpression=Key("bucketName").eq(bucket_name),
                FilterExpression=(
                    "signalType = :type AND enabled = :enabled"
                ),
                ExpressionAttributeValues={
                    ":type": "s3_file_upload",
                    ":enabled": True,
                },
            )

            matching_signals = []
            for signal in response.get("Items", []):

                config = signal.get("configuration", {})
                signal_bucket = config.get("bucketName")
                signal_prefix = config.get("prefix", "")
                signal_suffix = config.get("suffix", "")

                # Defensive strip: pre-refactor signal rows may carry
                # trailing whitespace. S3 event payloads never do, so
                # without this trim the equality check below would miss
                # legitimate matches.
                if isinstance(signal_bucket, str):
                    signal_bucket = signal_bucket.strip()
                if isinstance(signal_prefix, str):
                    signal_prefix = signal_prefix.strip()
                if isinstance(signal_suffix, str):
                    signal_suffix = signal_suffix.strip()

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
            if matching_signals:
                metrics.add_metric(
                    name="SignalMatched",
                    unit=MetricUnit.Count,
                    value=len(matching_signals),
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


def _create_task_for_signal_impl(
    signal: Dict[str, Any], payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a job for a triggered signal (non-idempotent core)."""
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

        # Honour the per-signal autoExecute flag. When True the signal
        # processor will enqueue the job onto the worker queue right
        # after persisting it, so the agent runs without any user
        # action; when False (default) the job sits in `idle` and waits
        # for the user to kick it off from the UI.
        auto_execute = bool(signal.get("autoExecute", False))
        initial_status = "busy" if auto_execute else "idle"

        # Create job record
        task_item = {
            "jobId": job_id,
            "userId": signal["userId"],
            "agentId": signal["agentId"],
            "jobName": f"Signal: {signal['signalName']} - {payload.get('key', 'Event')}",
            "jobType": "signal_triggered",
            "status": initial_status,
            "sessionId": session_id,
            "prompt": prompt,
            "requiresAction": False,
            "createdAt": now,
            "updatedAt": now,
            "metadata": {
                "signalId": signal["signalId"],
                "signalType": signal["signalType"],
                "triggerPayload": payload,
                "autoExecute": auto_execute,
            },
        }


        # Store job in DynamoDB
        task_table = dynamodb.Table(TASK_REGISTRY_TABLE)
        task_table.put_item(Item=task_item)

        logger.info(
            "Job created for signal",
            extra={
                "signal_id": signal["signalId"],
                "job_id": job_id,
                "auto_execute": auto_execute,
            },
        )

        # Auto-execute path: enqueue the job on the worker queue now so
        # the agent fires without any user click. Any SQS failure is
        # logged and the job is reset to `idle` so the user can still
        # run it manually.
        if auto_execute:
            if not JOB_EXECUTION_QUEUE_URL:
                logger.warning(
                    "autoExecute=true but JOB_EXECUTION_QUEUE_URL is not "
                    "configured; leaving job in idle so the user can run "
                    "it manually",
                    extra={"job_id": job_id},
                )
                task_table.update_item(
                    Key={"jobId": job_id},
                    UpdateExpression="SET #s = :s, updatedAt = :u",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":s": "idle",
                        ":u": datetime.now(timezone.utc).isoformat(),
                    },
                )
            else:
                try:
                    sqs_client.send_message(
                        QueueUrl=JOB_EXECUTION_QUEUE_URL,
                        MessageBody=json.dumps(
                            {
                                "jobId": job_id,
                                "signalTriggered": True,
                                "signalId": signal["signalId"],
                            }
                        ),
                    )
                    metrics.add_metric(
                        name="SignalAutoExecuted",
                        unit=MetricUnit.Count,
                        value=1,
                    )
                except Exception as enqueue_error:  # noqa: BLE001
                    logger.error(
                        "Auto-execute enqueue failed; resetting job to idle",
                        extra={
                            "job_id": job_id,
                            "error": str(enqueue_error),
                        },
                        exc_info=True,
                    )
                    task_table.update_item(
                        Key={"jobId": job_id},
                        UpdateExpression="SET #s = :s, updatedAt = :u",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={
                            ":s": "idle",
                            ":u": datetime.now(timezone.utc).isoformat(),
                        },
                    )

        return {
            "signalId": signal["signalId"],
            "jobId": job_id,
            "status": "created",
            "autoExecute": auto_execute,
        }

    except Exception as e:
        logger.error(
            "Job creation for signal failed", extra={"error": str(e)}, exc_info=True
        )
        raise


def create_task_for_signal(
    signal: Dict[str, Any], payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Idempotent wrapper around `_create_task_for_signal_impl`.

    When the idempotency table is configured, duplicate deliveries of the
    same S3 event are suppressed: Powertools returns the stored result
    of the first run instead of creating a second job. Without the table
    (local/dev), the call falls through unchanged.

    Key composition: `signalId + bucket + key + eventName + eTag +
    eventTime`. Including `eventTime` means two separate uploads of the
    same file (identical contents → same eTag) still produce two jobs
    because S3 stamps a distinct `eventTime` on each delivery. Only a
    literal retry of the same delivery (S3 retrying notification for
    durability) shares the same `eventTime` and gets deduped.
    """
    if _idempotency_layer is None:
        return _create_task_for_signal_impl(signal, payload)

    signal_id = signal.get("signalId", "unknown")
    bucket = payload.get("bucket", "")
    key = payload.get("key", "")
    event_name = payload.get("eventName", "")
    etag = payload.get("eTag", "")
    event_time = payload.get("eventTime", "")
    idempotency_key = (
        f"signal:{signal_id}:{bucket}:{key}:"
        f"{event_name}:{etag}:{event_time}"
    )

    @idempotent_function(
        data_keyword_argument="request",
        persistence_store=_idempotency_layer,
        config=_idempotency_config,
    )
    def _idempotent(request: Dict[str, Any]) -> Dict[str, Any]:
        return _create_task_for_signal_impl(request["signal"], request["payload"])

    return _idempotent(
        request={
            "idempotency_key": idempotency_key,
            "signal": signal,
            "payload": payload,
        }
    )


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


@metrics.log_metrics(capture_cold_start_metric=True)
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
