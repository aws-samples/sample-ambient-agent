# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Pytest fixtures for the agent/platform contract tests.

These tests import the platform-side parser (`job_execution.agent_client`)
and exercise it against canned AgentCore response payloads. To import
that module we need:

- Lambda environment variables set, because `clients.py` reads them at
  import time.
- A stubbed boto3 so no real AWS calls are attempted during collection.
- The Lambda source directory on `sys.path` so Python can resolve the
  `job_execution` package the same way Lambda does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Path bootstrap - point pytest at the Lambda source tree.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
LAMBDA_SRC = REPO_ROOT / "backend" / "functions" / "multi_agent"
if str(LAMBDA_SRC) not in sys.path:
    sys.path.insert(0, str(LAMBDA_SRC))


# ---------------------------------------------------------------------------
# Environment variables - required for module import.
# ---------------------------------------------------------------------------

_ENV_DEFAULTS = {
    "TASK_REGISTRY_TABLE": "test-task-registry",
    "CONVERSATION_STORE_TABLE": "test-conversation-store",
    "AGENT_REGISTRY_TABLE": "test-agent-registry",
    "JOB_EXECUTION_QUEUE_URL": "",  # API path is not under test here
    "IDEMPOTENCY_TABLE": "",  # disable idempotency layer for unit tests
    "ALLOWED_ORIGIN": "http://localhost",
    "AMBIENT_SIGNALS_TABLE": "test-ambient-signals",
    "AWS_DEFAULT_REGION": "us-east-1",
    "REGION": "us-east-1",
    "POWERTOOLS_METRICS_NAMESPACE": "AmbientAgentsTest",
    "POWERTOOLS_SERVICE_NAME": "contract-tests",
}


@pytest.fixture(autouse=True, scope="session")
def _set_env():
    with mock.patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
        yield


# ---------------------------------------------------------------------------
# boto3 stub - keep import-time DynamoDB/SQS calls offline.
# ---------------------------------------------------------------------------


class _StubTable:
    def __init__(self, name: str):
        self.name = name

    def get_item(self, **_kwargs):
        return {}

    def put_item(self, **_kwargs):
        return {}

    def update_item(self, **_kwargs):
        return {}

    def query(self, **_kwargs):
        return {"Items": []}


class _StubResource:
    def Table(self, name: str):  # noqa: N802 - boto3 camelCase
        return _StubTable(name)


class _StubClient:
    def invoke_agent_runtime(self, **_kwargs):
        return {"response": _EmptyBody()}

    def send_message(self, **_kwargs):
        return {"MessageId": "stub"}


class _EmptyBody:
    def read(self) -> bytes:
        return b""


def _stub_boto3_factory(service_name: str, **_kwargs):  # noqa: D401
    return _StubClient()


def _stub_boto3_resource(service_name: str, **_kwargs):  # noqa: D401
    return _StubResource()


@pytest.fixture(autouse=True, scope="session")
def _stub_boto3():
    """Replace boto3.client / boto3.resource so module import stays offline."""
    import boto3

    with mock.patch.object(boto3, "client", side_effect=_stub_boto3_factory), \
            mock.patch.object(boto3, "resource", side_effect=_stub_boto3_resource):
        yield
