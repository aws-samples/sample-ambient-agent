# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""API Gateway response helpers for job_execution."""

from __future__ import annotations

import json
import os
from typing import Any, Dict


def api_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build an API Gateway proxy-integration response with CORS."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ["ALLOWED_ORIGIN"],
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type, Authorization, X-Amz-Date, X-Amz-Security-Token"
            ),
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }
