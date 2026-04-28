# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Job execution Lambda entry point.

Two invocation paths land on `handler`:

- API Gateway (`POST /jobs/{jobId}/execute`) - enqueues an SQS message
  and returns 202 immediately. Routed to `api.handle_api_enqueue`.
- SQS event source on `job-execution-queue` - consumes messages, invokes
  AgentCore Runtime, persists results. Routed to `worker.handle_sqs_batch`.

The module is a package so each concern (API/worker/agent client/
conversation store/status updates/response shaping) lives in its own
file. CDK's handler path remains `job_execution.handler` because Python
imports `job_execution/__init__.py` when it resolves `job_execution`.
"""

from __future__ import annotations

from typing import Any, Dict

from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import Metrics

from .api import handle_api_enqueue
from .response import api_response
from .worker import handle_sqs_batch
from .conversation_store import (
    append_human_turn,
    load_conversation_context,
    save_conversation_turn,
)
from .status import (
    append_execution_log,
    update_task_field,
    update_task_status,
)

logger = Logger(service="job-execution", level="INFO")
metrics = Metrics(namespace="AmbientAgents", service="job-execution")


def _is_sqs_event(event: Dict[str, Any]) -> bool:
    records = event.get("Records") or []
    return bool(records) and records[0].get("eventSource") == "aws:sqs"


@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Entry dispatch.

    Routes SQS batch events to the worker path and API Gateway events
    to the enqueue path. Anything else is a misconfiguration.
    """
    if _is_sqs_event(event):
        return handle_sqs_batch(event, context)
    if "httpMethod" in event:
        return handle_api_enqueue(event)
    logger.error(
        "Unrecognised event shape",
        extra={"event_keys": list(event.keys())},
    )
    return api_response(400, {"error": "Unsupported event type"})


# Backwards-compatible re-exports. External test harnesses and docs
# referenced these names when they lived in a flat module; keep them
# importable from the package root.
create_response = api_response

__all__ = [
    "handler",
    "handle_api_enqueue",
    "handle_sqs_batch",
    "append_human_turn",
    "load_conversation_context",
    "save_conversation_turn",
    "update_task_status",
    "update_task_field",
    "append_execution_log",
    "api_response",
    "create_response",
]
