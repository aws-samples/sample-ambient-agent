# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Shared EMF metric names for the AmbientAgents CloudWatch namespace.

Lambdas should import these constants and emit metrics via
`aws_lambda_powertools.metrics.Metrics`. Keeping the names here makes
dashboards resilient to refactors and avoids typos on metric strings.
"""

from aws_lambda_powertools.metrics import Metrics

METRICS_NAMESPACE = "AmbientAgents"

# Job-lifecycle metrics
JOB_CREATED = "JobCreated"
JOB_ENQUEUED = "JobEnqueued"
JOB_COMPLETED = "JobCompleted"
JOB_INTERRUPTED = "JobInterrupted"
JOB_ERROR = "JobError"

# Signal metrics
SIGNAL_MATCHED = "SignalMatched"

# Human-in-the-loop metrics
HUMAN_INTERACTION_REQUESTED = "HumanInteractionRequested"

# Execution-log instrumentation
EXECUTION_LOG_TRUNCATED = "ExecutionLogTruncated"

# Model rate-limiting
MODEL_THROTTLED = "ModelThrottled"


def build_metrics(service: str) -> Metrics:
    """Return a Powertools Metrics instance scoped to the shared namespace."""
    return Metrics(namespace=METRICS_NAMESPACE, service=service)


__all__ = [
    "METRICS_NAMESPACE",
    "JOB_CREATED",
    "JOB_ENQUEUED",
    "JOB_COMPLETED",
    "JOB_INTERRUPTED",
    "JOB_ERROR",
    "SIGNAL_MATCHED",
    "HUMAN_INTERACTION_REQUESTED",
    "EXECUTION_LOG_TRUNCATED",
    "MODEL_THROTTLED",
    "build_metrics",
]
