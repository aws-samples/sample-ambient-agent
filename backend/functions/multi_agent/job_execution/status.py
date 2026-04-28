# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Job status + execution-log persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import MetricUnit, Metrics
from botocore.exceptions import ClientError

from .clients import task_table

logger = Logger(service="job-execution", level="INFO", child=True)
metrics = Metrics(namespace="AmbientAgents", service="job-execution")


ALLOWED_TASK_FIELDS = {"finalResult", "requiresAction", "executionLogs"}


def update_task_status(
    job_id: str, status: str, error_message: Optional[str] = None
) -> None:
    """Set job status (and optionally an error message) atomically."""
    try:
        update_expression = "SET #status = :status, updatedAt = :updatedAt"
        values: Dict[str, Any] = {
            ":status": status,
            ":updatedAt": datetime.utcnow().isoformat(),
        }
        if error_message:
            update_expression += ", errorMessage = :errorMessage"
            values[":errorMessage"] = error_message
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Job status update failed",
            extra={"error": str(exc)},
            exc_info=True,
        )


def update_task_field(job_id: str, field_name: str, field_value: Any) -> None:
    """Update a whitelisted field on a job record."""
    if field_name not in ALLOWED_TASK_FIELDS:
        raise ValueError(f"Field '{field_name}' is not allowed")
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
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Job field update failed",
            extra={"field_name": field_name, "error": str(exc)},
            exc_info=True,
        )


def append_execution_log(job_id: str, level: str, message: str) -> None:
    """Append a log entry atomically; truncate per-line at 1 KB.

    Single lines over 1 KB are trimmed with an ellipsis so an agent
    dumping a huge tool result cannot poison the 400 KB item limit.
    When DynamoDB still rejects the write for item size, we emit an
    ExecutionLogTruncated metric and log a warning rather than
    silently swallowing the failure.
    """
    safe_message = message if len(message) <= 1024 else message[:1021] + "..."
    entry = {
        "level": level,
        "message": safe_message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=(
                "SET executionLogs = list_append(if_not_exists(executionLogs, :empty), :entry)"
            ),
            ExpressionAttributeValues={":empty": [], ":entry": [entry]},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationException":
            logger.warning(
                "Execution log write rejected (item too large)",
                extra={"jobId": job_id},
            )
            metrics.add_metric(
                name="ExecutionLogTruncated",
                unit=MetricUnit.Count,
                value=1,
            )
        else:
            logger.error(
                "Execution log storage failed",
                extra={"error": str(exc)},
                exc_info=True,
            )
