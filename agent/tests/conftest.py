# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Pytest fixtures for the agent test suite.

Puts `agent/` on `sys.path` so tests can `import core.agent_core` the
same way the AgentCore Runtime does (it runs `agent.py` from inside the
`agent/` directory).
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
