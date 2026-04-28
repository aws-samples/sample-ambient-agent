# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
AgentCore Runtime wrapper.

Centralises the one call into `bedrock-agentcore:InvokeAgentRuntime` and
the parsing of its response envelope. Keeping this in its own module
lets the contract tests assert against `parse_agent_response` directly
without needing the rest of the Lambda in scope.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, Tuple

from aws_lambda_powertools import Logger

from .clients import agentcore_client_for_arn

logger = Logger(service="job-execution", level="INFO", child=True)


def build_invocation_session_id(session_id: str) -> str:
    """Pad a session id so AgentCore sees at least 33 characters.

    AgentCore rejects session ids shorter than 33 chars. Our internal
    ids can be any length (user-provided, UUID, etc.), so we add a
    deterministic suffix when needed.
    """
    if len(session_id) >= 33:
        return session_id
    padding = "".join(
        str(uuid.uuid4()).replace("-", "") for _ in range(2)
    )[: 33 - len(session_id)]
    return f"{session_id}-{padding}"


def invoke_agent(
    agent_arn: str,
    session_id: str,
    prompt: str,
    job_id: str,
) -> Dict[str, Any]:
    """Invoke AgentCore Runtime for one agent turn; return raw response."""
    invocation_session = build_invocation_session_id(session_id)
    payload = json.dumps(
        {
            "prompt": prompt,
            "session_id": invocation_session,
            "job_id": job_id,
            "metadata": {
                "timestamp": datetime.utcnow().isoformat(),
                "source": "multi-agent-platform",
            },
        }
    )
    client = agentcore_client_for_arn(agent_arn)
    return client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=invocation_session,
        payload=payload,
        qualifier="DEFAULT",
    )


def parse_agent_response(
    response: Dict[str, Any], job_id: str
) -> Tuple[str, str, bool]:
    """Parse an AgentCore reply into `(text, status, requires_human)`.

    The canonical agent-side contract is exactly one of:

    - `{"status": "completed",   "result": <text or dict>}`
    - `{"status": "interrupted", "question": <text>}`
    - `{"status": "error",       "error": <text>}`

    Anything else is treated as an error.
    """
    if "response" not in response:
        return "", "error", False

    raw = response["response"].read()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw.decode("utf-8", errors="replace"), "error", False

    if not isinstance(data, dict):
        return str(data), "error", False

    status = data.get("status")
    if status == "interrupted":
        question = (
            data.get("question")
            or data.get("human_input_question")
            or data.get("result")
            or ""
        )
        return str(question), "interrupted", True

    if status == "error":
        text = str(
            data.get("error")
            or data.get("result")
            or "Agent execution failed"
        )
        return text, "error", False

    if status == "completed":
        result = data.get("result", "")
        if isinstance(result, dict):
            text = (
                result.get("response", "")
                or result.get("output", "")
                or result.get("text", "")
                or result.get("answer", "")
                or str(result)
            )
        else:
            text = str(result)
        return text, "completed", False

    logger.error(
        "Agent returned unknown status",
        extra={"jobId": job_id, "status": status, "response": data},
    )
    return (
        str(data.get("result", "Agent returned invalid status")),
        "error",
        False,
    )
