# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import yaml
import os
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks
from infrastructure.infrastructure_stack import InfrastructureStack
from infrastructure.multi_agent_stack import MultiAgentStack
from infrastructure.config import AppConfig, MonitoringConfig, CognitoConfig

# Load app configuration
try:
    with open("config.yml", "r") as f:
        config_data = yaml.safe_load(f)
except FileNotFoundError:
    print("You must make a copy of config.example.yml as config.yml before deploying")
    raise
except Exception:
    raise

app = cdk.App()

# Add cdk_nag AWS Solutions checks to all stacks
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

# Parse monitoring configuration if present
monitoring_config = None
if "monitoring" in config_data:
    monitoring_data = config_data["monitoring"]
    monitoring_config = MonitoringConfig(
        alert_email=monitoring_data.get("alert_email"),
        enable_cloudwatch_alerts=monitoring_data.get("enable_cloudwatch_alerts", True),
    )

# Parse cognito configuration if present
cognito_config = None
if "cognito" in config_data:
    cognito_data = config_data["cognito"]
    cognito_config = CognitoConfig(
        users=cognito_data.get("users", []),
    )

config = AppConfig(
    stack_name=config_data["stack_name"],
    region=os.environ.get("CDK_DEFAULT_REGION"),
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    monitoring=monitoring_config,
    cognito=cognito_config,
    enable_waf=config_data.get("enable_waf", False),
    ip_allow_list=config_data.get("ip_allow_list"),
    cloudfront_cache_disable=config_data.get("cloudfront_cache_disable", False),
    allowed_agent_regions=config_data.get("allowed_agent_regions"),
)

# Deploy base infrastructure stack
base_stack = InfrastructureStack(
    app,
    config.stack_name,
    app_config=config,
    env=cdk.Environment(account=config.account, region=config.region),
)

# Deploy multi-agent platform stack
multi_agent_stack = MultiAgentStack(
    app,
    f"{config.stack_name}-MultiAgent",
    app_config=config,
    user_pool=base_stack.user_pool,
    user_pool_client=base_stack.user_pool_client,
    identity_pool=base_stack.identity_pool,
    alerts_topic=base_stack.alerts_topic,
    env=cdk.Environment(account=config.account, region=config.region),
)

# Multi-agent stack depends on base stack for Cognito user pool
multi_agent_stack.add_dependency(base_stack)

app.synth()
