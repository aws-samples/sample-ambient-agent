# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
API Gateway entry path for job execution.

Accepts `POST /jobs/{jobId}/execute`, verifies ownership, optionally
persists the human turn immediately (so the UI reflects it on the next
poll), and enqueues an SQS message for the worker to pick up. Returns
202 so API Gateway latency is bounded to the enqueue round-trip.

Idempotency: duplicate API calls for the same job + humanResponse land
on the same idempotency key and return the stored 202 response instead
of enqueuing twice. Scoped via the Powertools idempotency utility.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import MetricUnit, Metrics
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent_function,
)

from .clients import (
    IDEMPOTENCY_TABLE,
    JOB_EXECUTION_QUEUE_URL,
    sqs_client,
    task_table,
)
from .conversation_store import append_human_turn
from .response import api_response
from .status import update_task_status

logger = Logger(service="job-execution", level="INFO", child=True)
metrics = Metrics(namespace="AmbientAgents", service="job-execution")


# Build the idempotency persistence layer lazily: if the idempotency
# table env var is unset (e.g., in local tests), fall back to a no-op
# path so the Lambda still boots. The production stack always sets it.
_idempotency_layer: Optional[DynamoDBPersistenceLayer] = None
if IDEMPOTENCY_TABLE:
    _idempotency_layer = DynamoDBPersistenceLayer(table_name=IDEMPOTENCY_TABLE)

_idempotency_config = IdempotencyConfig(
    event_key_jmespath="idempotency_key",
    # Short window: we only want to catch accidental double-clicks and
    # network-layer retries, not legitimate re-runs by the user. The
    # trace id in the key already makes distinct user actions distinct;
    # the TTL is belt-and-braces to bound the idempotency table size.
    expires_after_seconds=300,
    raise_on_no_idempotency_key=False,
    use_local_cache=True,
)


def _enqueue_once(
    job_id: str,
    human_response: Optional[str],
    user_id: str,
    session_id: Optional[str],
    trace_id: str,
) -> Dict[str, Any]:
    """Enqueue a job-execution message on SQS exactly once per key.

    Powertools' idempotency decorator stores the return value keyed on
    `idempotency_key` (derived below) so a retry returns the same 202
    body without re-enqueuing. The key includes the API Gateway trace
    id so two separate user actions on the same job produce two jobs,
    while a retry of the same network request (same trace id) is
    deduped.
    """
    def _do_enqueue() -> Dict[str, Any]:
        message_body = json.dumps(
            {
                "jobId": job_id,
                "humanResponse": human_response,
                "userId": user_id,
            }
        )
        sqs_client.send_message(
            QueueUrl=JOB_EXECUTION_QUEUE_URL,
            MessageBody=message_body,
        )
        metrics.add_metric(name="JobEnqueued", unit=MetricUnit.Count, value=1)
        return {
            "status": "busy",
            "message": "Job execution queued",
            "jobId": job_id,
            "sessionId": session_id,
        }

    if _idempotency_layer is None or not trace_id:
        # No idempotency table configured, or no trace id available
        # (local/dev invocations). Fall through without dedup.
        return _do_enqueue()

    @idempotent_function(
        data_keyword_argument="payload",
        persistence_store=_idempotency_layer,
        config=_idempotency_config,
    )
    def _idempotent(payload: Dict[str, Any]) -> Dict[str, Any]:
        # The decorator reads `payload["idempotency_key"]` via the
        # jmespath expression configured above.
        return _do_enqueue()

    return _idempotent(
        payload={
            "idempotency_key": f"{job_id}:{trace_id}",
        }
    )


def handle_api_enqueue(event: Dict[str, Any]) -> Dict[str, Any]:
    """API Gateway handler: validate, persist human turn, enqueue."""
    try:
        path_parameters = event.get("pathParameters") or {}
        body = json.loads(event.get("body", "{}")) if event.get("body") else {}
        user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
        job_id = path_parameters.get("jobId")

        if not job_id:
            return api_response(400, {"error": "Job ID required"})

        task_response = task_table.get_item(Key={"jobId": job_id})
        if "Item" not in task_response:
            return api_response(404, {"error": "Job not found"})

        job = task_response["Item"]
        if job["userId"] != user_id:
            return api_response(403, {"error": "Access denied"})

        human_response = body.get("humanResponse")
        session_id = job.get("sessionId")
        agent_id = job.get("agentId")

        update_task_status(job_id, "busy")

        # Append the human turn immediately so the UI reflects it on the
        # next poll without waiting for the worker to finish.
        if human_response and session_id and agent_id:
            append_human_turn(
                session_id, agent_id, human_response, user_id=user_id
            )

        if not JOB_EXECUTION_QUEUE_URL:
            logger.error("JOB_EXECUTION_QUEUE_URL not configured")
            update_task_status(job_id, "error", "Queue not configured")
            return api_response(500, {"error": "Queue not configured"})

        # API Gateway sets X-Amzn-Trace-Id on every request. Retries of
        # the same request (SDK retry, client retry with the same trace)
        # share the same trace id; distinct user actions get distinct
        # ones. We use this as the dedup fingerprint per job.
        headers = event.get("headers") or {}
        trace_id = (
            headers.get("X-Amzn-Trace-Id")
            or headers.get("x-amzn-trace-id")
            or ""
        )

        result = _enqueue_once(
            job_id, human_response, user_id, session_id, trace_id
        )
        return api_response(202, result)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "API enqueue failed", extra={"error": str(exc)}
        )
        return api_response(500, {"error": "API execution failed"})
