# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import time
import hashlib
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_event_sources,
    aws_apigateway as apigateway,
    aws_iam as iam,
    aws_logs as logs,
    aws_events as events,
    aws_events_targets as targets,
    aws_sqs as sqs,
    aws_ssm as ssm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)

from cdk_nag import NagSuppressions
from .config import AppConfig


class MultiAgentStack(Stack):
    """
    Multi-Agent Platform Stack

    This stack creates the infrastructure for managing multiple Bedrock Agent Core agents:
    - Agent registry for storing agent configurations
    - Job management system with scheduling capabilities
    - Conversation continuity for agents that support human interruption
    - API endpoints for agent and job management
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_config: AppConfig,
        user_pool=None,
        user_pool_client=None,
        identity_pool=None,
        alerts_topic=None,
        **kwargs,
    ) -> None:
        self.description = "Multi-Agent Platform Infrastructure (uksb-s13m8r6xhz)"
        super().__init__(scope, construct_id, description=self.description, **kwargs)

        self.config = app_config
        self.user_pool = user_pool
        self.user_pool_client = user_pool_client
        self.identity_pool = identity_pool
        self.alerts_topic = alerts_topic

        # Create S3 website bucket
        self.website_bucket = self._create_website_bucket()

        # Create DynamoDB tables
        self.agent_registry_table = self._create_agent_registry_table()
        self.task_registry_table = self._create_task_registry_table()
        self.conversation_store_table = self._create_conversation_store_table()
        self.ambient_signals_table = self._create_ambient_signals_table()
        self.chat_threads_table = self._create_chat_threads_table()
        self.idempotency_table = self._create_idempotency_table()

        # Create Lambda Powertools layer reference
        self.powertools_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "PowertoolsLayer",
            f"arn:aws:lambda:{self.region}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python313-x86_64:7",
        )

        # Create SQS queue + DLQ that decouples job execution from the
        # synchronous API Gateway call. The API handler enqueues, the
        # worker Lambda drains.
        # `enforce_ssl=True` attaches a queue policy that denies any
        # request not using TLS (the aws:SecureTransport condition),
        # satisfying AwsSolutions-SQS4.
        self.job_execution_dlq = sqs.Queue(
            self,
            f"{self.config.stack_name}-JobExecutionDLQ",
            queue_name=f"{self.config.stack_name}-job-execution-dlq",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        self.job_execution_queue = sqs.Queue(
            self,
            f"{self.config.stack_name}-JobExecutionQueue",
            queue_name=f"{self.config.stack_name}-job-execution-queue",
            # Must exceed the worker Lambda timeout; AgentCore runs can
            # legitimately take up to 10 minutes.
            visibility_timeout=Duration.minutes(15),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.job_execution_dlq,
            ),
        )

        # DLQ for functions that are invoked asynchronously (Event
        # invocation) so we do not drop work silently on failure.
        self.async_invoke_dlq = sqs.Queue(
            self,
            f"{self.config.stack_name}-AsyncInvokeDLQ",
            queue_name=f"{self.config.stack_name}-async-invoke-dlq",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )

        # Create Lambda functions
        self.agent_management_function = self._create_agent_management_function()
        self.task_management_function = self._create_task_management_function()
        self.task_execution_function = self._create_task_execution_function()
        self.scheduler_function = self._create_scheduler_function()

        self.conversation_management_function = (
            self._create_conversation_management_function()
        )
        self.signal_management_function = self._create_signal_management_function()
        self.signal_processor_function = self._create_signal_processor_function()
        # Chat execution must be built before chat management so the latter
        # can reference its ARN for async invocation.
        self.chat_execution_function = self._create_chat_execution_function()
        self.chat_management_function = self._create_chat_management_function()

        # Create API Gateway resources
        self.api_gateway = self._create_api_gateway()

        # Create CloudFront distribution with API behavior
        self.distribution = self._create_cloudfront_distribution()

        # Set ALLOWED_ORIGIN on all Lambda functions now that distribution is created
        allowed_origin = f"https://{self.distribution.distribution_domain_name}"
        for fn in [
            self.agent_management_function,
            self.task_management_function,
            self.task_execution_function,
            self.conversation_management_function,
            self.signal_management_function,
            self.signal_processor_function,
            self.chat_management_function,
            self.chat_execution_function,
        ]:
            fn.add_environment("ALLOWED_ORIGIN", allowed_origin)

        # Add CORS preflight to API Gateway now that CloudFront domain is known
        self._add_cors_preflight(allowed_origin)

        # Deploy frontend
        self._deploy_frontend()

        # Create CloudWatch scheduler
        self._create_scheduler_rule()

        # Create outputs
        self._create_outputs()

        # Add cdk-nag suppressions
        self._add_nag_suppressions()

    def _create_website_bucket(self) -> s3.Bucket:
        """Create S3 bucket for frontend static assets"""
        access_logs_bucket = s3.Bucket(
            self,
            f"{self.config.stack_name}-WebsiteAccessLogsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireAccessLogs",
                    enabled=True,
                    expiration=Duration.days(90),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        return s3.Bucket(
            self,
            f"{self.config.stack_name}-WebsiteBucket",
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            auto_delete_objects=True,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            server_access_logs_bucket=access_logs_bucket,
        )

    def _create_agent_registry_table(self) -> dynamodb.Table:
        """Create DynamoDB table for agent registry"""
        table = dynamodb.Table(
            self,
            f"{self.config.stack_name}-AgentRegistryTable",
            table_name=f"{self.config.stack_name}-agent-registry",
            partition_key=dynamodb.Attribute(
                name="agentId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI for user-based queries
        table.add_global_secondary_index(
            index_name="userId-agentName-index",
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="agentName", type=dynamodb.AttributeType.STRING
            ),
        )

        return table

    def _create_task_registry_table(self) -> dynamodb.Table:
        """Create DynamoDB table for job registry"""
        table = dynamodb.Table(
            self,
            f"{self.config.stack_name}-TaskRegistryTable",
            table_name=f"{self.config.stack_name}-job-registry",
            partition_key=dynamodb.Attribute(
                name="jobId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI for user-based queries
        table.add_global_secondary_index(
            index_name="userId-status-index",
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="status", type=dynamodb.AttributeType.STRING
            ),
        )

        # GSI for agent-based queries
        table.add_global_secondary_index(
            index_name="agentId-status-index",
            partition_key=dynamodb.Attribute(
                name="agentId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="status", type=dynamodb.AttributeType.STRING
            ),
        )

        # GSI for scheduled job queries
        table.add_global_secondary_index(
            index_name="jobType-nextRun-index",
            partition_key=dynamodb.Attribute(
                name="jobType", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="nextRun", type=dynamodb.AttributeType.STRING
            ),
        )

        return table

    def _create_conversation_store_table(self) -> dynamodb.Table:
        """Create DynamoDB table for conversation storage"""
        table = dynamodb.Table(
            self,
            f"{self.config.stack_name}-ConversationStoreTable",
            table_name=f"{self.config.stack_name}-conversation-store",
            partition_key=dynamodb.Attribute(
                name="sessionId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI for agent-based conversation queries
        table.add_global_secondary_index(
            index_name="agentId-timestamp-index",
            partition_key=dynamodb.Attribute(
                name="agentId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="updatedAt", type=dynamodb.AttributeType.STRING
            ),
        )

        return table

    def _create_ambient_signals_table(self) -> dynamodb.Table:
        """Create DynamoDB table for ambient signals"""
        table = dynamodb.Table(
            self,
            f"{self.config.stack_name}-AmbientSignalsTable",
            table_name=f"{self.config.stack_name}-ambient-signals",
            partition_key=dynamodb.Attribute(
                name="signalId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI for user-based signal queries (used by the UI list page).
        table.add_global_secondary_index(
            index_name="userId-signalName-index",
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="signalName", type=dynamodb.AttributeType.STRING
            ),
        )

        # GSI that the signal processor queries on every S3 event. The
        # partition key is a top-level `bucketName` attribute (GSI keys
        # cannot be nested, so signal_management copies bucketName out of
        # configuration when it writes the item).
        table.add_global_secondary_index(
            index_name="bucketName-signalId-index",
            partition_key=dynamodb.Attribute(
                name="bucketName", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="signalId", type=dynamodb.AttributeType.STRING
            ),
        )

        return table

    def _create_idempotency_table(self) -> dynamodb.Table:
        """Create DynamoDB table for Powertools idempotency records.

        Shape follows `aws_lambda_powertools.utilities.idempotency.persistence`:
        partition key `id` (string), TTL attribute `expiration` (number).
        Keeps duplicate-suppression records from growing unboundedly - old
        entries expire automatically after the configured idempotency
        window on each lambda.
        """
        return dynamodb.Table(
            self,
            f"{self.config.stack_name}-IdempotencyTable",
            table_name=f"{self.config.stack_name}-idempotency-records",
            partition_key=dynamodb.Attribute(
                name="id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            time_to_live_attribute="expiration",
            removal_policy=RemovalPolicy.DESTROY,
        )


    def _create_agent_management_function(self) -> _lambda.Function:
        """Create Lambda function for agent management"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-AgentManagementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.agent_registry_table.grant_read_write_data(lambda_role)

        # Grant Bedrock permissions for agent testing
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agent-runtime:InvokeAgent",
                    "bedrock-agent:GetAgent",
                    "bedrock-agent:GetAgentAlias",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/*/*",
                ],
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-AgentManagementFunction",
            function_name=f"{self.config.stack_name}-agent-management",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="agent_management.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "REGION": self.region,
            },
        )

        return function

    def _create_task_management_function(self) -> _lambda.Function:
        """Create Lambda function for job management"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-TaskManagementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.task_registry_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-TaskManagementFunction",
            function_name=f"{self.config.stack_name}-job-management",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="job_management.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "TASK_REGISTRY_TABLE": self.task_registry_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "REGION": self.region,
            },
        )

        return function

    def _create_task_execution_function(self) -> _lambda.Function:
        """Create Lambda function for job execution"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-TaskExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.task_registry_table.grant_read_write_data(lambda_role)
        self.conversation_store_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)

        # Grant Bedrock permissions for agent invocation (including Agent Core)
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeAgent",
                    "bedrock:InvokeModel",
                    "bedrock-agent-runtime:InvokeAgent",
                    "bedrock-agent:GetAgent",
                    "bedrock-agent:GetAgentAlias",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:agent-alias/*/*",
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}::foundation-model/us.anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                ],
            )
        )

        # Grant Bedrock Agent Core permissions.
        # The agent registry stores full ARNs from whatever region the user
        # deployed the AgentCore runtime in (e.g., us-east-1), which may
        # differ from this stack's region. Use a region wildcard so the
        # Lambda can invoke runtimes registered from any region in this
        # account. InvokeAgentRuntime authorizes against both the runtime
        # resource and the runtime-endpoint resource, so both patterns are
        # granted.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                resources=[
                    f"arn:aws:bedrock-agentcore:*:{self.account}:runtime/*",
                    f"arn:aws:bedrock-agentcore:*:{self.account}:runtime/*/runtime-endpoint/*",
                ],
            )
        )

        # Grant the worker Lambda permission to consume job-execution
        # messages from the queue.
        self.job_execution_queue.grant_consume_messages(lambda_role)

        # Idempotency: lets the API path suppress duplicate enqueues for
        # the same (jobId, humanResponse) within a short window.
        self.idempotency_table.grant_read_write_data(lambda_role)

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-TaskExecutionFunction",
            function_name=f"{self.config.stack_name}-job-execution",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="job_execution.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.minutes(15),
            memory_size=512,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "TASK_REGISTRY_TABLE": self.task_registry_table.table_name,
                "CONVERSATION_STORE_TABLE": self.conversation_store_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "JOB_EXECUTION_QUEUE_URL": self.job_execution_queue.queue_url,
                "IDEMPOTENCY_TABLE": self.idempotency_table.table_name,
                "REGION": self.region,
            },
            # Cap concurrent model invocations so a burst of signals
            # cannot saturate the Bedrock quota. Tune via deploy config.
            reserved_concurrent_executions=20,
        )

        # Wire SQS as an event source for the same Lambda. API Gateway
        # invocations still land here through the regular event path; SQS
        # records are handled alongside via the Records envelope.
        function.add_event_source(
            lambda_event_sources.SqsEventSource(
                self.job_execution_queue,
                batch_size=1,
                report_batch_item_failures=True,
            )
        )

        # Allow the API-side of this Lambda to enqueue new work.
        self.job_execution_queue.grant_send_messages(lambda_role)

        return function


    def _create_scheduler_function(self) -> _lambda.Function:
        """Create Lambda function for job scheduling"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-SchedulerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.task_registry_table.grant_read_write_data(lambda_role)

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-SchedulerFunction",
            function_name=f"{self.config.stack_name}-scheduler",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="scheduler.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.minutes(5),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "TASK_REGISTRY_TABLE": self.task_registry_table.table_name,
                "JOB_EXECUTION_QUEUE_URL": self.job_execution_queue.queue_url,
                "REGION": self.region,
            },
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.async_invoke_dlq,
        )

        # Scheduler enqueues into the job-execution queue instead of
        # directly invoking the executor Lambda.
        self.job_execution_queue.grant_send_messages(lambda_role)

        return function


    def _create_conversation_management_function(self) -> _lambda.Function:
        """Create Lambda function for conversation management"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-ConversationManagementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions. Conversation access is authorized by
        # verifying ownership against either the jobs table (classic job flow)
        # or the chat-threads table (standalone chat flow), so we need read
        # access to both.
        self.conversation_store_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)
        self.task_registry_table.grant_read_data(lambda_role)
        self.chat_threads_table.grant_read_data(lambda_role)

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-ConversationManagementFunction",
            function_name=f"{self.config.stack_name}-conversation-management",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="conversation_management.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "CONVERSATION_STORE_TABLE": self.conversation_store_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "TASK_REGISTRY_TABLE": self.task_registry_table.table_name,
                "CHAT_THREADS_TABLE": self.chat_threads_table.table_name,
                "REGION": self.region,
            },
        )

        return function

    def _create_signal_management_function(self) -> _lambda.Function:
        """Create Lambda function for signal management"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-SignalManagementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.ambient_signals_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)

        # Grant S3 permissions for managing bucket notifications
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetBucketLocation",
                    "s3:GetBucketNotification",
                    "s3:PutBucketNotification",
                ],
                resources=["arn:aws:s3:::*"],
            )
        )

        # Grant Lambda permissions to add/remove permissions for S3 invocation
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:AddPermission",
                    "lambda:RemovePermission",
                    "lambda:GetPolicy",
                ],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:{
                        self.config.stack_name
                    }-signal-processor"
                ],
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-SignalManagementFunction",
            function_name=f"{self.config.stack_name}-signal-management",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="signal_management.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "AMBIENT_SIGNALS_TABLE": self.ambient_signals_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "SIGNAL_PROCESSOR_FUNCTION_NAME": f"{self.config.stack_name}-signal-processor",
                "REGION": self.region,
            },
        )

        return function

    def _create_signal_processor_function(self) -> _lambda.Function:
        """Create Lambda function for processing ambient signals"""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-SignalProcessorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Grant DynamoDB permissions
        self.ambient_signals_table.grant_read_write_data(lambda_role)
        self.task_registry_table.grant_read_write_data(lambda_role)

        # Idempotency: suppress duplicate S3 event deliveries by
        # (signalId, bucket, key, eventName, eTag).
        self.idempotency_table.grant_read_write_data(lambda_role)

        # Allow the signal processor to enqueue work onto the job
        # execution queue for signals configured with autoExecute=true.
        # Signals that leave autoExecute at its default (False) never
        # hit this path, so the grant is cheap insurance for the
        # auto-execute feature.
        self.job_execution_queue.grant_send_messages(lambda_role)

        # Grant S3 read permissions to access uploaded files if needed
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                ],
                resources=["arn:aws:s3:::*/*"],
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-SignalProcessorFunction",
            function_name=f"{self.config.stack_name}-signal-processor",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="signal_processor.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(60),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "AMBIENT_SIGNALS_TABLE": self.ambient_signals_table.table_name,
                "TASK_REGISTRY_TABLE": self.task_registry_table.table_name,
                "IDEMPOTENCY_TABLE": self.idempotency_table.table_name,
                "JOB_EXECUTION_QUEUE_URL": self.job_execution_queue.queue_url,
                "REGION": self.region,
            },
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.async_invoke_dlq,
        )


        # Note: S3 bucket notification permissions are added dynamically by signal_management
        # when signals are created/updated, using the statement ID pattern:
        # s3-invoke-signal-{signalId}

        return function

    def _create_chat_threads_table(self) -> dynamodb.Table:
        """Create DynamoDB table for chat threads.

        Rows carry thread metadata only. Messages live in the shared
        conversation-store table keyed by sessionId, so they are not
        duplicated here.
        """
        table = dynamodb.Table(
            self,
            f"{self.config.stack_name}-ChatThreadsTable",
            table_name=f"{self.config.stack_name}-chat-threads",
            partition_key=dynamodb.Attribute(
                name="threadId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery=True,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # GSI lets the chat UI list a user's threads sorted by recency.
        table.add_global_secondary_index(
            index_name="userId-updatedAt-index",
            partition_key=dynamodb.Attribute(
                name="userId", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="updatedAt", type=dynamodb.AttributeType.STRING
            ),
        )

        return table

    def _create_chat_execution_function(self) -> _lambda.Function:
        """Create Lambda that performs the actual AgentCore call for a chat turn."""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-ChatExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        self.chat_threads_table.grant_read_write_data(lambda_role)
        self.conversation_store_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)

        # Same region-wildcard policy as the job executor so chat can invoke
        # AgentCore runtimes registered in any region of this account.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    f"arn:aws:bedrock-agentcore:*:{self.account}:runtime/*",
                    f"arn:aws:bedrock-agentcore:*:{self.account}:runtime/*/runtime-endpoint/*",
                ],
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-ChatExecutionFunction",
            function_name=f"{self.config.stack_name}-chat-execution",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="chat_execution.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.minutes(10),
            memory_size=512,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "CHAT_THREADS_TABLE": self.chat_threads_table.table_name,
                "CONVERSATION_STORE_TABLE": self.conversation_store_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "REGION": self.region,
            },
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.async_invoke_dlq,
        )

        return function

    def _create_chat_management_function(self) -> _lambda.Function:

        """Create the Lambda that handles /chats CRUD + message POSTs."""
        lambda_role = iam.Role(
            self,
            f"{self.config.stack_name}-ChatManagementRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        self.chat_threads_table.grant_read_write_data(lambda_role)
        self.conversation_store_table.grant_read_write_data(lambda_role)
        self.agent_registry_table.grant_read_data(lambda_role)

        # Allow async-invoking the chat executor for message turns.
        self.chat_execution_function.grant_invoke(lambda_role)

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-ChatManagementFunction",
            function_name=f"{self.config.stack_name}-chat-management",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="chat_management.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "CHAT_THREADS_TABLE": self.chat_threads_table.table_name,
                "CONVERSATION_STORE_TABLE": self.conversation_store_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "CHAT_EXECUTION_FUNCTION_NAME": self.chat_execution_function.function_name,
                "REGION": self.region,
            },
            dead_letter_queue_enabled=True,
            dead_letter_queue=self.async_invoke_dlq,
        )

        return function


    def _create_api_gateway(self) -> apigateway.RestApi:
        """Create API Gateway for multi-agent platform"""

        # Create access log group
        access_log_group = logs.LogGroup(
            self,
            f"{self.config.stack_name}-MultiAgentApiAccessLogs",
            log_group_name=f"{self.config.stack_name}-multi-agent-api-access-logs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        api = apigateway.RestApi(
            self,
            f"{self.config.stack_name}-MultiAgentAPI",
            rest_api_name=f"{self.config.stack_name}-multi-agent-api",
            description="Multi-Agent Platform API",
            endpoint_types=[apigateway.EndpointType.REGIONAL],
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                logging_level=apigateway.MethodLoggingLevel.INFO,
                access_log_destination=apigateway.LogGroupLogDestination(
                    access_log_group
                ),
                access_log_format=apigateway.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
                metrics_enabled=True,
                tracing_enabled=True,
            ),
            cloud_watch_role=True,
        )

        # Add request validator
        _request_validator = api.add_request_validator(
            "RequestValidator",
            validate_request_body=True,
            validate_request_parameters=True,
        )

        # Create Cognito authorizer using the passed user pool
        authorizer = None
        if self.user_pool:
            authorizer = apigateway.CognitoUserPoolsAuthorizer(
                self,
                f"{self.config.stack_name}-MultiAgentAuthorizer",
                cognito_user_pools=[self.user_pool],
            )

        # Agent management routes
        agents_resource = api.root.add_resource("agents")
        agents_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        agents_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        agent_resource = agents_resource.add_resource("{agentId}")
        agent_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        agent_resource.add_method(
            "PUT",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        agent_resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Agent test route
        test_resource = agent_resource.add_resource("test")
        test_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.agent_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Job management routes
        tasks_resource = api.root.add_resource("jobs")
        tasks_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.task_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        tasks_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.task_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        task_resource = tasks_resource.add_resource("{jobId}")
        task_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.task_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        task_resource.add_method(
            "PUT",
            apigateway.LambdaIntegration(self.task_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        task_resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(self.task_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Job execution route
        execute_resource = task_resource.add_resource("execute")
        execute_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.task_execution_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Conversation management routes
        conversations_resource = api.root.add_resource("conversations")
        conversation_resource = conversations_resource.add_resource("{sessionId}")

        # Get conversation history
        conversation_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.conversation_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Add message to conversation
        conversation_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.conversation_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Get formatted conversation context
        context_resource = conversation_resource.add_resource("context")
        context_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.conversation_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Signal management routes
        signals_resource = api.root.add_resource("signals")
        signals_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.signal_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        signals_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.signal_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        signal_resource = signals_resource.add_resource("{signalId}")
        signal_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.signal_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        signal_resource.add_method(
            "PUT",
            apigateway.LambdaIntegration(self.signal_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        signal_resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(self.signal_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        # Chat routes - standalone agent chat decoupled from jobs.
        chats_resource = api.root.add_resource("chats")
        chats_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.chat_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        chats_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.chat_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        chat_thread_resource = chats_resource.add_resource("{threadId}")
        chat_thread_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(self.chat_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )
        chat_thread_resource.add_method(
            "DELETE",
            apigateway.LambdaIntegration(self.chat_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        chat_messages_resource = chat_thread_resource.add_resource("messages")
        chat_messages_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(self.chat_management_function),
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )

        return api

    def _add_cors_preflight(self, allowed_origin: str):
        """Add CORS preflight OPTIONS responses scoped to the CloudFront domain"""
        cors_kwargs = {
            "allow_origins": [allowed_origin],
            "allow_methods": apigateway.Cors.ALL_METHODS,
            "allow_headers": [
                "Content-Type",
                "Authorization",
                "X-Amz-Date",
                "X-Amz-Security-Token",
            ],
            "max_age": Duration.minutes(10),
        }

        # Add OPTIONS to all API resources
        for child in self.api_gateway.root.node.find_all():
            if isinstance(child, apigateway.Resource):
                child.add_cors_preflight(**cors_kwargs)

    def _create_cloudfront_distribution(self) -> cloudfront.Distribution:
        """Create CloudFront distribution for frontend and API"""
        cloudfront_access_logs_bucket = s3.Bucket(
            self,
            f"{self.config.stack_name}-CloudFrontAccessLogsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            object_ownership=s3.ObjectOwnership.OBJECT_WRITER,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="DeleteOldLogs",
                    enabled=True,
                    expiration=Duration.days(7),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        NagSuppressions.add_resource_suppressions(
            cloudfront_access_logs_bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "This is a logs destination bucket; enabling access logs would create circular logging",
                }
            ],
        )

        s3_origin = origins.S3BucketOrigin.with_origin_access_control(
            self.website_bucket
        )

        default_cache_policy = (
            cloudfront.CachePolicy.CACHING_DISABLED
            if self.config.cloudfront_cache_disable
            else cloudfront.CachePolicy.CACHING_OPTIMIZED
        )

        assets_cache_policy = (
            cloudfront.CachePolicy.CACHING_DISABLED
            if self.config.cloudfront_cache_disable
            else cloudfront.CachePolicy.CACHING_OPTIMIZED
        )

        # Security headers policy for all responses
        security_headers_policy = cloudfront.ResponseHeadersPolicy(
            self,
            f"{self.config.stack_name}-SecurityHeadersPolicy",
            response_headers_policy_name=f"{self.config.stack_name}-security-headers",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(
                    override=True
                ),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=True,
                ),
                xss_protection=cloudfront.ResponseHeadersXSSProtection(
                    protection=True, mode_block=True, override=True
                ),
            ),
        )

        # SPA routing: rewrite any extension-less request path to /index.html
        # at the viewer-request stage of the default (S3) behavior. The
        # function is scoped to the default behavior only so API responses
        # on /prod/* pass through untouched.
        spa_router_fn = cloudfront.Function(

            self,
            f"{self.config.stack_name}-SpaRouterFunction",
            function_name=f"{self.config.stack_name}-spa-router",
            comment=(
                "Rewrites client-side React Router paths to /index.html so "
                "deep links and page refreshes load the SPA shell."
            ),
            code=cloudfront.FunctionCode.from_inline(
                """
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    if (uri === '' || uri === '/') {
        return request;
    }
    // Paths with a file extension are real assets; everything else is a
    // React Router path and gets rewritten to the SPA shell.
    var hasExtension = /\\.[a-zA-Z0-9]{1,8}$/.test(uri);
    if (hasExtension) {
        return request;
    }
    request.uri = '/index.html';
    return request;
}
"""
            ),
        )


        distribution = cloudfront.Distribution(
            self,
            f"{self.config.stack_name}-Distribution",
            default_root_object="index.html",
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                compress=True,
                cache_policy=default_cache_policy,
                response_headers_policy=security_headers_policy,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=spa_router_fn,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    ),
                ],
            ),
            additional_behaviors={
                "/assets/*": cloudfront.BehaviorOptions(
                    origin=s3_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    compress=True,
                    cache_policy=assets_cache_policy,
                    origin_request_policy=cloudfront.OriginRequestPolicy(
                        self,
                        f"{self.config.stack_name}-AssetsOriginRequestPolicy",
                        origin_request_policy_name=f"{self.config.stack_name}-assets-origin-request-policy",
                        query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
                        header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                            "Accept",
                            "Accept-Language",
                            "Cache-Control",
                            "Content-Type",
                            "Origin",
                            "Referer",
                            "User-Agent",
                        ),
                    ),
                ),
                "/prod/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        f"{self.api_gateway.rest_api_id}.execute-api.{self.region}.amazonaws.com",
                        origin_ssl_protocols=[
                            cloudfront.OriginSslPolicy.TLS_V1_2,
                        ],
                        protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
            # SPA fallback is handled by spa_router_fn so API 404s on
            # /prod/* pass through as genuine 404s.
            enable_ipv6=False,

            enable_logging=True,
            log_bucket=cloudfront_access_logs_bucket,
        )


        NagSuppressions.add_resource_suppressions(
            distribution,
            [
                {
                    "id": "AwsSolutions-CFR4",
                    "reason": "Using default CloudFront domain; TLS 1.2 enforcement requires custom domain with ACM certificate",
                }
            ],
        )

        return distribution

    def _deploy_frontend(self):
        """Deploy frontend assets to S3 and invalidate CloudFront"""
        deployment_timestamp = (
            str(int(time.time()))
            if self.config.cloudfront_cache_disable
            else "2025-06-16T19:41:00Z"
        )
        build_hash = (
            hashlib.sha256(
                (deployment_timestamp + str(time.time())).encode()
            ).hexdigest()[:8]
            if self.config.cloudfront_cache_disable
            else "static"
        )

        aws_exports = s3deploy.Source.json_data(
            "aws-exports.json",
            {
                "region": self.region,
                "Auth": {
                    "Cognito": {
                        "userPoolClientId": self.user_pool_client.user_pool_client_id,
                        "userPoolId": self.user_pool.user_pool_id,
                        "identityPoolId": self.identity_pool.ref,
                    },
                },
                "API": {
                    "REST": {
                        "MultiAgentApi": {
                            "endpoint": f"https://{self.distribution.distribution_domain_name}/prod",
                        },
                    },
                },
                "lastUpdated": deployment_timestamp,
            },
        )

        frontend_asset = s3deploy.Source.asset("../frontend/dist")

        cache_control = (
            [
                s3deploy.CacheControl.no_cache(),
                s3deploy.CacheControl.no_store(),
                s3deploy.CacheControl.must_revalidate(),
                s3deploy.CacheControl.max_age(Duration.seconds(0)),
            ]
            if self.config.cloudfront_cache_disable
            else [
                s3deploy.CacheControl.set_public(),
                s3deploy.CacheControl.max_age(Duration.days(365)),
            ]
        )

        self._bucket_deployment = s3deploy.BucketDeployment(
            self,
            f"{self.config.stack_name}-UserInterfaceDeployment",
            prune=False,
            sources=[frontend_asset, aws_exports],
            destination_bucket=self.website_bucket,
            distribution=self.distribution,
            cache_control=cache_control,
            metadata=(
                {
                    "deployment-timestamp": deployment_timestamp,
                    "build-hash": build_hash,
                    "cdk-deployment": "true",
                }
                if self.config.cloudfront_cache_disable
                else None
            ),
            sign_content=True if self.config.cloudfront_cache_disable else False,
            extract=True,
        )

    def _create_scheduler_rule(self):
        """Create CloudWatch Events rule for job scheduling"""
        rule = events.Rule(
            self,
            f"{self.config.stack_name}-SchedulerRule",
            schedule=events.Schedule.rate(Duration.minutes(1)),
            description="Trigger scheduler function every minute for job scheduling",
        )

        rule.add_target(targets.LambdaFunction(self.scheduler_function))

    def _create_outputs(self):
        """Create CloudFormation outputs"""
        CfnOutput(
            self,
            "UserInterfaceDomainName",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="CloudFront distribution URL for the web application",
        )

        CfnOutput(
            self,
            "MultiAgentApiEndpoint",
            value=self.api_gateway.url,
            description="Multi-Agent Platform API endpoint",
        )

        CfnOutput(
            self,
            "MultiAgentApiViaCloudFront",
            value=f"https://{self.distribution.distribution_domain_name}/api",
            description="Multi-Agent Platform API endpoint via CloudFront",
        )

        CfnOutput(
            self,
            "AgentRegistryTableName",
            value=self.agent_registry_table.table_name,
            description="Agent Registry DynamoDB table name",
        )

        CfnOutput(
            self,
            "TaskRegistryTableName",
            value=self.task_registry_table.table_name,
            description="Job Registry DynamoDB table name",
        )

        CfnOutput(
            self,
            "ConversationStoreTableName",
            value=self.conversation_store_table.table_name,
            description="Conversation Store DynamoDB table name",
        )

        # Store API endpoint in SSM for frontend access
        ssm.StringParameter(
            self,
            f"{self.config.stack_name}-MultiAgentApiParam",
            parameter_name=f"/{self.config.stack_name}/multi-agent-api-endpoint",
            string_value=f"https://{self.distribution.distribution_domain_name}/api",
            description="Multi-Agent Platform API endpoint via CloudFront",
        )

    def _add_nag_suppressions(self):
        """Add cdk-nag suppressions for acceptable findings"""

        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWS managed policies are acceptable for Lambda basic execution and API Gateway CloudWatch logging",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs",
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions are scoped to specific resource types: DynamoDB GSI access, Bedrock agents/models/runtimes (account/region scoped), Lambda invoke (specific function ARN), S3 signal processing, and CDK bucket deployment",
                },
                {
                    "id": "AwsSolutions-APIG2",
                    "reason": "Request validation is enabled at the API level; Lambda functions perform additional validation",
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Python 3.13 is the latest supported runtime; cdk-nag may not recognize it yet",
                },
            ],
        )

        # Suppress specific resource wildcards for bucket deployment
        if hasattr(self, "_bucket_deployment"):
            NagSuppressions.add_resource_suppressions(
                self._bucket_deployment,
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": "CDK BucketDeployment requires these permissions to deploy assets",
                    },
                    {
                        "id": "AwsSolutions-L1",
                        "reason": "CDK BucketDeployment uses a managed Lambda function with its own runtime lifecycle",
                    },
                ],
                apply_to_children=True,
            )
