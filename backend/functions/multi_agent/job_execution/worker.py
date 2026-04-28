# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
SQS worker entry path for job execution.

Consumes `job-execution-queue` messages one at a time (batch_size=1 on
the event source, with `report_batch_item_failures` enabled), invokes
AgentCore Runtime for the referenced job, persists the agent's reply
to the conversation store, and updates the job status.

Any raised exception bubbles up to the handler so the message is
retried up to the queue's maxReceiveCount, after which SQS moves it to
the DLQ.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from aws_lambda_powertools import Logger
from aws_lambda_powertools.metrics import MetricUnit, Metrics

from .agent_client import invoke_agent, parse_agent_response
from .clients import agent_table, task_table
from .conversation_store import (
    load_conversation_context,
    save_conversation_turn,
)
from .status import append_execution_log, update_task_field, update_task_status

logger = Logger(service="job-execution", level="INFO", child=True)
metrics = Metrics(namespace="AmbientAgents", service="job-execution")


def handle_sqs_batch(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process an SQS batch and report partial failures.

    Returning a `batchItemFailures` array tells SQS which individual
    records failed; the rest are acked. Any record that fails moves
    toward the DLQ on its next failed receive.
    """
    failures: List[Dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            import json as _json  # local import keeps module boot cheap

            payload = _json.loads(record.get("body", "{}"))
        except (TypeError, ValueError):
            logger.error(
                "Malformed SQS message body",
                extra={
                    "messageId": message_id,
                    "body": record.get("body"),
                },
            )
            failures.append({"itemIdentifier": message_id})
            continue

        try:
            _process_job_message(payload, context)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception(
                "Job worker failed",
                extra={"messageId": message_id, "error": str(exc)},
            )
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def _process_job_message(payload: Dict[str, Any], context: Any) -> None:
    job_id = payload.get("jobId")
    if not job_id:
        logger.error("SQS message missing jobId", extra={"payload": payload})
        return

    task_response = task_table.get_item(Key={"jobId": job_id})
    if "Item" not in task_response:
        logger.error("Job not found for queue message", extra={"jobId": job_id})
        return

    job = task_response["Item"]
    human_response = payload.get("humanResponse")
    _execute_task(job, human_response, context)


def _execute_task(
    job: Dict[str, Any],
    human_response: Optional[str],
    context: Any,
) -> Dict[str, Any]:
    """Run the agent for one turn of this job."""
    job_id = job["jobId"]
    agent_id = job["agentId"]
    session_id = job["sessionId"]
    user_id = job.get("userId")

    try:
        update_task_status(job_id, "busy")
        append_execution_log(job_id, "info", "Job execution started")

        agent_response = agent_table.get_item(Key={"agentId": agent_id})
        if "Item" not in agent_response:
            update_task_status(job_id, "error", "Agent not found")
            metrics.add_metric(
                name="JobError", unit=MetricUnit.Count, value=1
            )
            return {"status": "error", "error": "Agent not found"}

        agent = agent_response["Item"]
        agent_arn = agent["agentArn"]
        append_execution_log(
            job_id,
            "info",
            f"Agent loaded: {agent.get('agentName', 'Unknown')}",
        )

        prompt = _build_prompt(job, human_response, session_id, agent_id)

        start_time = datetime.utcnow()
        append_execution_log(job_id, "info", "Sending request to agent")

        response = invoke_agent(
            agent_arn=agent_arn,
            session_id=session_id,
            prompt=prompt,
            job_id=job_id,
        )

        result_text, agent_status, requires_human_input = parse_agent_response(
            response, job_id
        )

        execution_time = (datetime.utcnow() - start_time).total_seconds()
        append_execution_log(
            job_id,
            "info",
            f"Agent execution completed in {execution_time:.2f}s",
        )

        save_source = human_response if human_response else job["prompt"]
        save_conversation_turn(
            session_id, agent_id, save_source, result_text, user_id=user_id
        )

        if agent_status == "error":
            update_task_status(job_id, "error", result_text)
            metrics.add_metric(
                name="JobError", unit=MetricUnit.Count, value=1
            )
            return {
                "status": "error",
                "error": result_text,
                "sessionId": session_id,
            }

        if requires_human_input:
            update_task_status(job_id, "interrupted")
            update_task_field(job_id, "requiresAction", True)
            metrics.add_metric(
                name="JobInterrupted", unit=MetricUnit.Count, value=1
            )
            return {
                "status": "interrupted",
                "result": result_text,
                "requiresAction": True,
                "sessionId": session_id,
                "executionTime": execution_time,
            }

        update_task_status(job_id, "completed")
        update_task_field(job_id, "finalResult", result_text)
        update_task_field(job_id, "requiresAction", False)
        metrics.add_metric(
            name="JobCompleted", unit=MetricUnit.Count, value=1
        )
        return {
            "status": "completed",
            "result": result_text,
            "sessionId": session_id,
            "executionTime": execution_time,
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.exception(
            "Agent execution failed",
            extra={"jobId": job_id, "error": str(exc)},
        )
        append_execution_log(job_id, "error", "Agent execution failed")
        update_task_status(job_id, "error", "Agent execution failed")
        metrics.add_metric(name="JobError", unit=MetricUnit.Count, value=1)
        return {
            "status": "error",
            "error": "Agent execution failed",
            "sessionId": session_id,
        }


def _build_prompt(
    job: Dict[str, Any],
    human_response: Optional[str],
    session_id: str,
    agent_id: str,
) -> str:
    """Compose the prompt sent to AgentCore for this turn.

    Uses the stored conversation context for continuity, then layers the
    current human response (if this is a continuation) or the original
    job prompt (fresh run) on top.
    """
    original_prompt = job["prompt"]
    conversation_context = load_conversation_context(session_id, agent_id)

    if human_response:
        return (
            "CONVERSATION CONTEXT:\n"
            f"{conversation_context}\n\n"
            f"HUMAN RESPONSE: {human_response}\n\n"
            "Please continue based on the human's response above."
        )
    if conversation_context:
        return (
            "CONVERSATION CONTEXT:\n"
            f"{conversation_context}\n\n"
            f"CURRENT REQUEST: {original_prompt}"
        )
    return original_prompt
