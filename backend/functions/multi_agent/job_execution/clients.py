# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared boto3 clients and environment bindings for job_execution."""

from __future__ import annotations

import os

import boto3

TASK_REGISTRY_TABLE = os.environ["TASK_REGISTRY_TABLE"]
CONVERSATION_STORE_TABLE = os.environ["CONVERSATION_STORE_TABLE"]
AGENT_REGISTRY_TABLE = os.environ["AGENT_REGISTRY_TABLE"]
JOB_EXECUTION_QUEUE_URL = os.environ.get("JOB_EXECUTION_QUEUE_URL", "")
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "")

dynamodb = boto3.resource("dynamodb")
sqs_client = boto3.client("sqs")

task_table = dynamodb.Table(TASK_REGISTRY_TABLE)
conversation_table = dynamodb.Table(CONVERSATION_STORE_TABLE)
agent_table = dynamodb.Table(AGENT_REGISTRY_TABLE)


def agentcore_client_for_arn(agent_arn: str):
    """Build a bedrock-agentcore client pinned to the ARN's region.

    The agent registry stores full ARNs that may reference AgentCore
    runtimes in regions different from this Lambda's own region. Reading
    the region out of the ARN keeps the client targeted.
    """
    try:
        arn_region = agent_arn.split(":")[3]
    except (IndexError, AttributeError):
        arn_region = None
    if arn_region:
        return boto3.client("bedrock-agentcore", region_name=arn_region)
    return boto3.client("bedrock-agentcore")
