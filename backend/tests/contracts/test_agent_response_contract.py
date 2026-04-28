# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Agent <-> platform response contract tests.

The agent container (in `agent/core/agent_core.py`) is documented to
return exactly one of:

- `{"status": "completed",   "result": <text or dict>}`
- `{"status": "interrupted", "question": <text>}`
- `{"status": "error",       "error":  <text>}`

These tests pin that contract from the platform side. They exercise
`job_execution.agent_client.parse_agent_response` with stubbed AgentCore
response envelopes and assert the (text, status, requires_human) tuple
the worker relies on.

If the agent container ever starts returning a different shape, one of
these tests will fail and force a deliberate contract update rather than
a silent behavioural regression.
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict


class _Body:
    """Minimal stand-in for the `response["response"]` StreamingBody."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def _envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"response": _Body(json.dumps(payload).encode("utf-8"))}


# ---------------------------------------------------------------------------
# Happy paths - each canonical status is parsed exactly as documented.
# ---------------------------------------------------------------------------


def test_completed_with_string_result():
    from job_execution.agent_client import parse_agent_response

    response = _envelope({"status": "completed", "result": "all done"})
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "completed"
    assert requires_human is False
    assert text == "all done"


def test_completed_with_dict_result_unwraps_known_keys():
    from job_execution.agent_client import parse_agent_response

    # Agents that return a structured result should have the text field
    # hoisted out so the worker persists something useful.
    response = _envelope(
        {
            "status": "completed",
            "result": {
                "response": "structured answer",
                "output": "ignored because response wins",
            },
        }
    )
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "completed"
    assert requires_human is False
    assert text == "structured answer"


def test_interrupted_surfaces_question():
    from job_execution.agent_client import parse_agent_response

    response = _envelope(
        {"status": "interrupted", "question": "Which vendor should I use?"}
    )
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "interrupted"
    assert requires_human is True
    assert text == "Which vendor should I use?"


def test_error_uses_error_field_over_result():
    from job_execution.agent_client import parse_agent_response

    response = _envelope(
        {
            "status": "error",
            "error": "loop detected",
            "result": "ignored when error present",
        }
    )
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "error"
    assert requires_human is False
    assert text == "loop detected"


# ---------------------------------------------------------------------------
# Malformed / malicious shapes - all treated as errors, never as success.
# ---------------------------------------------------------------------------


def test_missing_response_key_is_error():
    from job_execution.agent_client import parse_agent_response

    text, status, requires_human = parse_agent_response({}, "job-1")

    assert status == "error"
    assert requires_human is False
    assert text == ""


def test_non_json_payload_is_error_with_raw_text():
    from job_execution.agent_client import parse_agent_response

    response = {"response": _Body(b"this is not json")}
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "error"
    assert requires_human is False
    assert text == "this is not json"


def test_non_object_json_is_error():
    from job_execution.agent_client import parse_agent_response

    # e.g. the agent returned a bare string or a list - not the contract.
    response = {"response": _Body(json.dumps(["not", "an", "object"]).encode())}
    _text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "error"
    assert requires_human is False


def test_unknown_status_is_error():
    from job_execution.agent_client import parse_agent_response

    response = _envelope({"status": "halted", "result": "weird"})
    _text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "error"
    assert requires_human is False


# ---------------------------------------------------------------------------
# Legacy / alternate key names.
# ---------------------------------------------------------------------------


def test_interrupted_falls_back_to_human_input_question_key():
    # Older agent payloads used `human_input_question` instead of `question`.
    # The parser tolerates that for backwards compatibility; if that ever
    # gets removed, this test pins the observable behaviour.
    from job_execution.agent_client import parse_agent_response

    response = _envelope(
        {
            "status": "interrupted",
            "human_input_question": "Approve or reject?",
        }
    )
    text, status, requires_human = parse_agent_response(response, "job-1")

    assert status == "interrupted"
    assert requires_human is True
    assert text == "Approve or reject?"
