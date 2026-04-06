# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Agent Entry Point

This is the main entry point for the agent. It simply imports and runs
the agent from the core package. This keeps the root directory clean
and makes it clear where the platform integration code lives.

For customization:
- Modify tools in the tools/ directory
- Update configuration in config.yaml
- Platform integration code is in core/ (rarely needs changes)
"""

from core.agent_core import app

if __name__ == "__main__":
    app.run()
