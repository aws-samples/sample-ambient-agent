# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from dataclasses import dataclass
from typing import Optional, List, Dict


@dataclass
class MonitoringConfig:
    """Configuration for CloudWatch monitoring and alerting"""

    alert_email: str
    enable_cloudwatch_alerts: bool = True


@dataclass
class CognitoConfig:
    """Configuration for Cognito users"""

    users: List[str]

    def __post_init__(self):
        if not self.users:
            raise ValueError("At least one Cognito user email must be specified")


@dataclass
class AppConfig:
    stack_name: str
    region: Optional[str] = None
    account: Optional[str] = None
    enable_waf: bool = False
    cloudfront_cache_disable: bool = False  # Disable CloudFront caching when True
    ip_allow_list: Optional[List[str]] = None
    environment_variables: Optional[Dict[str, str]] = None
    monitoring: Optional[MonitoringConfig] = None
    cognito: Optional[CognitoConfig] = None

    def __post_init__(self):
        if self.environment_variables is None:
            self.environment_variables = {}
        if not self.stack_name:
            raise ValueError("stack_name must be specified in config.yml")

        # Require monitoring config to be explicitly provided
        if self.monitoring is None:
            raise ValueError("monitoring configuration must be specified in config.yml")

        # Require cognito config to be explicitly provided
        if self.cognito is None:
            raise ValueError("cognito configuration must be specified in config.yml")
