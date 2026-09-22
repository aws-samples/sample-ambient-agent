# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import time
import hashlib
from typing import List, Optional
from constructs import Construct
from aws_cdk import (
    Stack,
    Token,
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
    aws_bedrock as bedrock,
    aws_wafv2 as wafv2,
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

        # Create the stack-owned bucket that ambient signals (S3 file-
        # upload triggers) are allowed to watch. Signals can only point
        # at this bucket (never an arbitrary caller-supplied bucket
        # name), and the signal management/processor roles' S3
        # permissions are scoped to match - so attaching a signal never
        # grants a Lambda invoke permission + notification config
        # against a bucket outside the platform's control.
        self.signal_uploads_bucket = self._create_signal_uploads_bucket()

        # Bedrock Guardrail applied by the example agent to every model
        # invocation. Filters prompt-attack/jailbreak attempts (the
        # primary risk from feeding untrusted uploaded-file content and
        # user-supplied signal metadata into the model) plus standard
        # harmful-content categories. The agent runs outside this CDK
        # stack (deployed separately via the AgentCore CLI), so the
        # guardrail's id/version are surfaced as stack outputs for the
        # agent operator to copy into `agent/config.yaml`.
        self.agent_guardrail = self._create_agent_guardrail()
        # A published version (rather than DRAFT) is required to pass
        # `guardrailVersion` to `ChatBedrock`/`invoke_model`.
        self.agent_guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            f"{self.config.stack_name}-AgentGuardrailVersion",
            guardrail_identifier=self.agent_guardrail.attr_guardrail_id,
            description="Published version for the example agent to reference.",
        )

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

    def _create_signal_uploads_bucket(self) -> s3.Bucket:
        """Create the single bucket that ambient S3 signals may watch.

        Ambient signals only ever need a bucket for users to upload
        sample files into so the agent can react to them. Provisioning
        one bucket per stack (instead of accepting any bucket name from
        the client) lets every IAM grant downstream be scoped to this
        bucket's ARN instead of `arn:aws:s3:::*`.
        """
        # Deliberately named without a "SignalUploads" prefix: the
        # physical bucket name this produces (e.g.
        # `...-uploadslogsdest-xxxx`) needs to be visually
        # distinguishable at a glance from the real uploads bucket
        # below, since this bucket has no notification wiring at all -
        # confusing it with the real uploads target would mean an
        # upload silently never fires a signal.
        access_logs_bucket = s3.Bucket(
            self,
            f"{self.config.stack_name}-UploadsLogsDestinationBucket",
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
            f"{self.config.stack_name}-SignalUploadsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            auto_delete_objects=True,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            server_access_logs_bucket=access_logs_bucket,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireUploads",
                    enabled=True,
                    expiration=Duration.days(30),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

    def _create_agent_guardrail(self) -> bedrock.CfnGuardrail:
        """Create the Bedrock Guardrail the example agent applies to every
        model invocation.

        `PROMPT_ATTACK` is the content filter most relevant here: the
        agent feeds uploaded-file content and user-supplied signal
        metadata/descriptions into the model, both of which are
        prompt-injection vectors.
        Per the Bedrock API, PROMPT_ATTACK's `output_strength` must be
        "NONE" - the filter only evaluates input, not model output.
        Standard harmful-content filters are included too, at a
        moderate strength, since they're essentially free once a
        guardrail exists.
        """
        content_filters = [
            bedrock.CfnGuardrail.ContentFilterConfigProperty(
                type="PROMPT_ATTACK",
                input_strength="HIGH",
                output_strength="NONE",
            )
        ]
        for harmful_category in ("HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"):
            content_filters.append(
                bedrock.CfnGuardrail.ContentFilterConfigProperty(
                    type=harmful_category,
                    input_strength="MEDIUM",
                    output_strength="MEDIUM",
                )
            )

        return bedrock.CfnGuardrail(
            self,
            f"{self.config.stack_name}-AgentGuardrail",
            name=f"{self.config.stack_name}-agent-guardrail",
            blocked_input_messaging=(
                "I can't process that request - it looks like it may be "
                "attempting to override my instructions or contains "
                "content I'm not able to act on."
            ),
            blocked_outputs_messaging=(
                "I'm not able to share that response."
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=content_filters,
            ),
        )

    def _nag_account(self) -> str:
        """The account id as cdk-nag renders it in IAM5 finding text.

        When the stack is environment-bound (AWS credentials or an
        explicit env resolved a concrete account at synth time),
        `self.account` is the literal 12-digit id and cdk-nag prints it
        verbatim. When synthesizing environment-agnostically (fresh
        clone, no AWS session - a fully supported `cdk synth` mode),
        `self.account` is an unresolved token that renders into the
        template as `{"Ref": "AWS::AccountId"}`, which cdk-nag prints
        as the placeholder `<AWS::AccountId>`. Suppression `appliesTo`
        strings must match the printed form exactly, so any suppression
        embedding the account must go through this helper or it will
        silently stop matching the moment synth runs without
        credentials - failing the build out of the box.
        """
        if Token.is_unresolved(self.account):
            return "<AWS::AccountId>"
        return self.account

    def _nag_region(self) -> str:
        """Region as cdk-nag renders it in finding text (see _nag_account)."""
        if Token.is_unresolved(self.region):
            return "<AWS::Region>"
        return self.region

    def _agent_runtime_resource_arns(
        self, account: Optional[str] = None
    ) -> List[str]:
        """Build the resource ARN list for bedrock-agentcore:InvokeAgentRuntime.

        Always includes this stack's own deploy region, plus any region
        listed in `allowed_agent_regions` (config.yml) - covering the case
        where an operator registers an AgentCore runtime in a different
        region than the backend stack without granting a bare `*` region
        wildcard. Each region contributes both the runtime resource and
        the runtime-endpoint resource, since InvokeAgentRuntime authorizes
        against either.

        `account` defaults to `self.account` (correct for real IAM policy
        resources, where an unresolved token becomes a CFN intrinsic).
        Pass `self._nag_account()` instead when building cdk-nag
        suppression strings, which must match the rendered finding text.
        """
        account = self.account if account is None else account
        regions = {self.region, *(self.config.allowed_agent_regions or [])}
        resources: List[str] = []
        for region in sorted(regions):
            resources.append(
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/*"
            )
            resources.append(
                f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/*/runtime-endpoint/*"
            )
        return resources

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
            # Two weeks is enough for debugging without letting each of
            # this stack's 9 Lambda log groups grow forever.
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
                    # Scoped to cross-region inference profiles whose
                    # profile id itself starts with the Claude model
                    # prefix (e.g. `us.anthropic.claude-*`) rather than
                    # `inference-profile/*`, which would let this role
                    # invoke a profile fronting any model regardless of
                    # the "Claude models only" intent.
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-*",
                ],
            )
        )

        # Grant Bedrock Agent Core permissions.
        # The agent registry stores full ARNs from whatever region the user
        # deployed the AgentCore runtime in (e.g., us-east-1), which may
        # differ from this stack's region. Scoped to an explicit region
        # allowlist (this stack's own region, plus `allowed_agent_regions`
        # from config.yml) rather than a bare `*`, so registering an agent
        # in another region doesn't grant InvokeAgentRuntime against every
        # AWS region. InvokeAgentRuntime authorizes against both the
        # runtime resource and the runtime-endpoint resource, so both
        # patterns are granted for each allowed region.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                resources=self._agent_runtime_resource_arns(),
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
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

        # Grant S3 permissions for managing bucket notifications, scoped
        # to the stack-owned signal-uploads bucket only. Signals cannot
        # be attached to arbitrary buckets (see create_signal's
        # bucket-name allowlist check), so this grant does not need to
        # be account-wide.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetBucketLocation",
                    "s3:GetBucketNotification",
                    "s3:PutBucketNotification",
                ],
                resources=[self.signal_uploads_bucket.bucket_arn],
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
            timeout=Duration.seconds(30),
            memory_size=256,
            role=lambda_role,
            layers=[self.powertools_layer],
            environment={
                "AMBIENT_SIGNALS_TABLE": self.ambient_signals_table.table_name,
                "AGENT_REGISTRY_TABLE": self.agent_registry_table.table_name,
                "SIGNAL_PROCESSOR_FUNCTION_NAME": f"{self.config.stack_name}-signal-processor",
                "ALLOWED_SIGNAL_BUCKET": self.signal_uploads_bucket.bucket_name,
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

        # Grant S3 read permissions to access uploaded files, scoped to
        # the stack-owned signal-uploads bucket only. Signals cannot
        # point at arbitrary buckets, so this Lambda never needs to
        # read outside of it.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                ],
                resources=[f"{self.signal_uploads_bucket.bucket_arn}/*"],
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-SignalProcessorFunction",
            function_name=f"{self.config.stack_name}-signal-processor",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="signal_processor.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            log_retention=logs.RetentionDays.TWO_WEEKS,
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

        # Same region-allowlisted policy as the job executor so chat can
        # invoke AgentCore runtimes registered in this stack's region or
        # any additional region listed in `allowed_agent_regions`.
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=self._agent_runtime_resource_arns(),
            )
        )

        function = _lambda.Function(
            self,
            f"{self.config.stack_name}-ChatExecutionFunction",
            function_name=f"{self.config.stack_name}-chat-execution",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="chat_execution.handler",
            code=_lambda.Code.from_asset("functions/multi_agent"),
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
            log_retention=logs.RetentionDays.TWO_WEEKS,
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
                # Stage-level throttling applies to EVERY request hitting
                # the stage. The usage plan below carries the same
                # rate/burst numbers, but usage-plan throttles only bind
                # to requests presenting an API key - and this API uses
                # Cognito authorizers, not API keys - so without these
                # stage settings the platform would fall back to the
                # account-level default limits only.
                throttling_rate_limit=50,
                throttling_burst_limit=100,
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

        # Usage plan with a daily quota, layered on top of the
        # stage-level throttling set in `deploy_options` above. The
        # stage throttle is what actually limits anonymous/Cognito
        # traffic (usage-plan throttles only bind to API-key requests,
        # and this API issues no API keys); the plan is kept for its
        # daily quota ceiling and as a place to attach keys if any are
        # added later.
        usage_plan = api.add_usage_plan(
            f"{self.config.stack_name}-MultiAgentUsagePlan",
            name=f"{self.config.stack_name}-multi-agent-usage-plan",
            throttle=apigateway.ThrottleSettings(rate_limit=50, burst_limit=100),
            quota=apigateway.QuotaSettings(
                limit=100000, period=apigateway.Period.DAY
            ),
        )
        usage_plan.add_api_stage(stage=api.deployment_stage)

        # Optional AWS WAFv2 web ACL, gated behind `enable_waf` in
        # config.yml since it adds ongoing cost that isn't needed for a
        # default sample deployment. When enabled, apply AWS-managed
        # rule groups covering common exploits and known bad IPs, plus
        # rate limiting per source IP as defense-in-depth alongside the
        # usage plan above (which limits per-API-key, not per-IP).
        if self.config.enable_waf:
            web_acl = wafv2.CfnWebACL(
                self,
                f"{self.config.stack_name}-MultiAgentWebAcl",
                default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
                scope="REGIONAL",
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    metric_name=f"{self.config.stack_name}-multi-agent-waf",
                    sampled_requests_enabled=True,
                ),
                rules=[
                    wafv2.CfnWebACL.RuleProperty(
                        name="AWSManagedRulesCommonRuleSet",
                        priority=0,
                        override_action=wafv2.CfnWebACL.OverrideActionProperty(
                            none={}
                        ),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesCommonRuleSet",
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name=f"{self.config.stack_name}-common-rule-set",
                            sampled_requests_enabled=True,
                        ),
                    ),
                    wafv2.CfnWebACL.RuleProperty(
                        name="AWSManagedRulesKnownBadInputsRuleSet",
                        priority=1,
                        override_action=wafv2.CfnWebACL.OverrideActionProperty(
                            none={}
                        ),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesKnownBadInputsRuleSet",
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name=f"{self.config.stack_name}-known-bad-inputs",
                            sampled_requests_enabled=True,
                        ),
                    ),
                    wafv2.CfnWebACL.RuleProperty(
                        name="RateLimitPerIp",
                        priority=2,
                        action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                                limit=2000,
                                aggregate_key_type="IP",
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name=f"{self.config.stack_name}-rate-limit-per-ip",
                            sampled_requests_enabled=True,
                        ),
                    ),
                ]
                + (
                    [
                        wafv2.CfnWebACL.RuleProperty(
                            name="IpAllowList",
                            priority=3,
                            action=wafv2.CfnWebACL.RuleActionProperty(
                                block={}
                            ),
                            statement=wafv2.CfnWebACL.StatementProperty(
                                not_statement=wafv2.CfnWebACL.NotStatementProperty(
                                    statement=wafv2.CfnWebACL.StatementProperty(
                                        ip_set_reference_statement=wafv2.CfnWebACL.IPSetReferenceStatementProperty(
                                            arn=wafv2.CfnIPSet(
                                                self,
                                                f"{self.config.stack_name}-MultiAgentIpAllowSet",
                                                addresses=self.config.ip_allow_list,
                                                ip_address_version="IPV4",
                                                scope="REGIONAL",
                                            ).attr_arn
                                        )
                                    )
                                )
                            ),
                            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                                cloud_watch_metrics_enabled=True,
                                metric_name=f"{self.config.stack_name}-ip-allow-list",
                                sampled_requests_enabled=True,
                            ),
                        )
                    ]
                    if self.config.ip_allow_list
                    else []
                ),
            )

            wafv2.CfnWebACLAssociation(
                self,
                f"{self.config.stack_name}-MultiAgentWebAclAssociation",
                resource_arn=(
                    f"arn:aws:apigateway:{self.region}::/restapis/"
                    f"{api.rest_api_id}/stages/{api.deployment_stage.stage_name}"
                ),
                web_acl_arn=web_acl.attr_arn,
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
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    # Defense-in-depth for a UI that renders LLM output
                    # (chat/job responses via react-markdown). No inline
                    # script/style, no plugins/objects, and framing
                    # disallowed entirely (frame-ancestors backs up the
                    # X-Frame-Options DENY above). connect-src covers
                    # same-origin API calls (proxied through this
                    # distribution at /prod/*) plus Cognito's IdP/token
                    # endpoints that Amplify Auth calls directly from
                    # the browser; img-src allows the AWS sign-in page
                    # logo asset used in the login layout.
                    # CSP host-source wildcards are only valid as a
                    # LEADING label (e.g. `*.example.com`) - a wildcard
                    # in the middle of a hostname like
                    # `cognito-idp.*.amazonaws.com` is not valid syntax
                    # and browsers silently drop that source entry,
                    # blocking every Cognito call Amplify Auth makes
                    # (observed as a live CSP violation against
                    # cognito-idp.<region>.amazonaws.com). Cognito's
                    # regional endpoints are always
                    # `<service>.<region>.amazonaws.com`, and this stack
                    # already knows its own deployment region at synth
                    # time, so the exact hostnames are generated instead
                    # of attempted via wildcard.
                    content_security_policy=(
                        "default-src 'self'; "
                        "script-src 'self'; "
                        "style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data: https://*.amazonaws.com; "
                        f"connect-src 'self' https://cognito-idp.{self.region}.amazonaws.com "
                        f"https://cognito-identity.{self.region}.amazonaws.com; "
                        "object-src 'none'; "
                        "base-uri 'self'; "
                        "frame-ancestors 'none'"
                    ),
                    override=True,
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
                # Only bucket ambient S3 signals may point at. Surfaced
                # here so the signals UI can show/prefill it instead of
                # accepting a free-text bucket name.
                "signalUploadsBucket": self.signal_uploads_bucket.bucket_name,
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

        CfnOutput(
            self,
            "SignalUploadsBucketName",
            value=self.signal_uploads_bucket.bucket_name,
            description=(
                "The only S3 bucket ambient signals may watch. Upload "
                "sample files here to trigger s3_file_upload signals."
            ),
        )

        CfnOutput(
            self,
            "AgentGuardrailId",
            value=self.agent_guardrail.attr_guardrail_id,
            description=(
                "Bedrock Guardrail id. Copy into agent/config.yaml's "
                "aws.bedrock.guardrail_id so the example agent applies "
                "it to every model invocation."
            ),
        )

        CfnOutput(
            self,
            "AgentGuardrailVersion",
            value=self.agent_guardrail_version.attr_version,
            description=(
                "Bedrock Guardrail version. Copy into agent/config.yaml's "
                "aws.bedrock.guardrail_version."
            ),
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
                    # Scoped to the exact finding strings cdk-nag emits
                    # for this stack (confirmed via `cdk synth`) rather
                    # than a blanket, stack-wide suppression with no
                    # `appliesTo` - that would silence every current AND
                    # FUTURE wildcard finding, masking accidental
                    # over-broad grants added later. Regenerate this
                    # list with `cdk synth` if resources are added,
                    # renamed, or reordered.
                    "id": "AwsSolutions-IAM5",
                    "reason": "DynamoDB GSI grants (table + all indexes), S3 bucket-deployment/signal-processing actions scoped to specific buckets, and the CDK asset bucket used by BucketDeployment all require a trailing wildcard by construct design",
                    "appliesTo": [
                        "Action::s3:Abort*",
                        "Action::s3:DeleteObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                        "Resource::<reactstarterAgentRegistryTableBB07C774.Arn>/index/*",
                        "Resource::<reactstarterAmbientSignalsTableF850C89A.Arn>/index/*",
                        "Resource::<reactstarterChatExecutionFunction41221382.Arn>:*",
                        "Resource::<reactstarterChatThreadsTableDDAA57CD.Arn>/index/*",
                        "Resource::<reactstarterConversationStoreTable2E465338.Arn>/index/*",
                        "Resource::<reactstarterSignalUploadsBucket97B46C5E.Arn>/*",
                        "Resource::<reactstarterTaskRegistryTableB4A069AD.Arn>/index/*",
                        "Resource::<reactstarterWebsiteBucket50502CD9.Arn>/*",
                        # CDK's bootstrap asset bucket. Named
                        # `cdk-hnb659fds-assets-<account>-<region>` by
                        # convention; built via _nag_account()/_nag_region()
                        # so the string matches cdk-nag's rendered finding
                        # both when synth is env-bound (literal ids) and
                        # env-agnostic (<AWS::AccountId> placeholder, e.g.
                        # a fresh clone with no AWS credentials).
                        f"Resource::arn:aws:s3:::cdk-hnb659fds-assets-{self._nag_account()}-{self._nag_region()}/*",
                        # AgentCore runtime ARNs still need a trailing
                        # `runtime/*` wildcard per allowed region (the
                        # runtime id itself isn't known at synth time).
                        # Mirrors `_agent_runtime_resource_arns()`, so
                        # this list grows/shrinks with `allowed_agent_regions`;
                        # account rendered via _nag_account() to match
                        # cdk-nag's finding text in both synth modes.
                        *[
                            f"Resource::{arn}"
                            for arn in self._agent_runtime_resource_arns(
                                account=self._nag_account()
                            )
                        ],
                        f"Resource::arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-*",
                        f"Resource::arn:aws:bedrock:{self.region}::foundation-model/us.anthropic.claude-*",
                        f"Resource::arn:aws:bedrock:{self.region}:{self._nag_account()}:agent-alias/*/*",
                        f"Resource::arn:aws:bedrock:{self.region}:{self._nag_account()}:agent/*",
                        f"Resource::arn:aws:bedrock:{self.region}:{self._nag_account()}:inference-profile/us.anthropic.claude-*",
                        # CDK's built-in BucketDeployment custom resource
                        # (react-starter-UserInterfaceDeployment below)
                        # generates its own Lambda service role with a
                        # `Resource::*` statement that isn't covered by
                        # the resource-scoped suppression further down
                        # despite `apply_to_children=True` - it's on a
                        # sibling custom-resource-provider construct, not
                        # a child of `_bucket_deployment` itself. Not
                        # something this stack's own code grants.
                        "Resource::*",
                    ],
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
