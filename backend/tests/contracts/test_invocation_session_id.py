# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Contract test for `build_invocation_session_id`.

AgentCore requires session ids of at least 33 characters. The platform
pads short ids before invocation but must not mutate ones that are
already long enough. This test pins that behaviour so accidental
changes (e.g. trimming) show up as a failure.
"""

from __future__ import annotations


def test_short_session_id_is_padded_to_at_least_33_chars():
    from job_execution.agent_client import build_invocation_session_id

    out = build_invocation_session_id("short")
    assert len(out) >= 33
    assert out.startswith("short")


def test_long_session_id_is_returned_unchanged():
    from job_execution.agent_client import build_invocation_session_id

    long_id = "a" * 40
    assert build_invocation_session_id(long_id) == long_id


def test_exactly_33_char_session_id_is_not_padded():
    from job_execution.agent_client import build_invocation_session_id

    boundary = "b" * 33
    assert build_invocation_session_id(boundary) == boundary
