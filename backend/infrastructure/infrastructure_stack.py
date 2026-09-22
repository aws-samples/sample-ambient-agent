# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from constructs import Construct
from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
)
from cdk_nag import NagSuppressions
from .config import AppConfig


class InfrastructureStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, app_config: AppConfig, **kwargs
    ) -> None:
        self.description = "React-Starter-Pack (uksb-s13m8r6xhz)"
        super().__init__(scope, construct_id, description=self.description, **kwargs)

        self.config = app_config
        self.alerts_topic = self._create_alerts_topic()
        self.user_pool, self.user_pool_client, self.identity_pool = (
            self._create_cognito_auth()
        )

        self._create_outputs()
        self._add_nag_suppressions()

    def _create_cognito_auth(self):
        user_pool = cognito.UserPool(
            self,
            f"{self.config.stack_name}-UserPool",
            user_pool_name=f"{self.config.stack_name}-user-pool",
            removal_policy=RemovalPolicy.DESTROY,
            self_sign_up_enabled=False,
            # Phone auto-verify removed: sign-in only ever uses email
            # (`sign_in_aliases` below), so verifying phone numbers was
            # unused - but its presence makes CDK/Cognito provision an
            # SMS-sending configuration on the pool. That SMS config
            # then conflicts with `mfa_second_factor(sms=False)` below:
            # Cognito refuses "turn off SMS_MFA while SMS configuration
            # is set" on an update. Dropping phone verification removes
            # the SMS config so TOTP-only MFA can deploy.
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            # Required TOTP MFA (cdk-nag AwsSolutions-COG2 / ARCC BSC10
            # only clear with MfaConfiguration=ON, not OPTIONAL). The
            # frontend's Amplify `<Authenticator>` natively handles the
            # CONTINUE_SIGN_IN_WITH_TOTP_SETUP and
            # CONFIRM_SIGN_IN_WITH_TOTP_CODE challenge steps, so
            # admin-provisioned users (see _create_cognito_users below)
            # are walked through QR-code TOTP enrollment on their first
            # sign-in - no custom enrollment UI is needed. On an
            # existing pool this deploys as an in-place update; users
            # who haven't enrolled yet are prompted at their next
            # sign-in.
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=False, otp=True, email=False
            ),
            # Use the Plus feature plan (replaces the deprecated
            # advanced_security_mode property). Plus tier enables advanced
            # security features such as adaptive authentication, compromised
            # credential detection, and protection against unsafe passwords.
            feature_plan=cognito.FeaturePlan.PLUS,
        )

        user_pool_client = user_pool.add_client(
            f"{self.config.stack_name}-UserPoolClient",
            generate_secret=False,
            # SRP-only: the frontend's Amplify `<Authenticator>` uses
            # USER_SRP_AUTH exclusively, so ADMIN_USER_PASSWORD_AUTH and
            # USER_PASSWORD_AUTH (both of which send the plaintext
            # password to Cognito instead of a zero-knowledge proof)
            # were enabled but unused. Removing them narrows the auth
            # surface to the flow actually exercised.
            auth_flows=cognito.AuthFlow(
                user_srp=True,
            ),
        )

        identity_pool = cognito.CfnIdentityPool(
            self,
            f"{self.config.stack_name}-IdentityPool",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[
                cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                    client_id=user_pool_client.user_pool_client_id,
                    provider_name=user_pool.user_pool_provider_name,
                )
            ],
        )

        authenticated_role = iam.Role(
            self,
            f"{self.config.stack_name}-AuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": identity_pool.ref,
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated",
                    },
                },
                "sts:AssumeRoleWithWebIdentity",
            ),
        )

        cognito.CfnIdentityPoolRoleAttachment(
            self,
            f"{self.config.stack_name}-IdentityPoolRoleAttachment",
            identity_pool_id=identity_pool.ref,
            roles={
                "authenticated": authenticated_role.role_arn,
            },
        )

        # Create default users with the emails from config
        self._create_cognito_users(user_pool)

        return user_pool, user_pool_client, identity_pool

    def _create_outputs(self):
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
        )

        CfnOutput(
            self,
            "AlertsTopicArn",
            value=self.alerts_topic.topic_arn,
            description="SNS Topic ARN for CloudWatch alerts",
        )

        CfnOutput(
            self,
            "CognitoUserEmail",
            value=self.config.monitoring.alert_email,
            description="Email address of the created Cognito user (check email for temporary password)",
        )

        CfnOutput(
            self,
            "CognitoUsers",
            value=", ".join(self.config.cognito.users),
            description="All Cognito users created (check emails for temporary passwords)",
        )

    def _create_alerts_topic(self) -> sns.Topic:
        """Create SNS topic for CloudWatch alerts"""
        topic = sns.Topic(
            self,
            f"{self.config.stack_name}-AlertsTopic",
            topic_name=f"{self.config.stack_name}-alerts",
            display_name="CloudWatch Alerts",
            enforce_ssl=True,
        )

        # Subscribe email to the topic
        topic.add_subscription(
            sns_subscriptions.EmailSubscription(self.config.monitoring.alert_email)
        )

        return topic

    def _create_cognito_users(self, user_pool: cognito.UserPool):
        """Create Cognito users for all emails in config"""

        # Add users to the pool
        for email in self.config.cognito.users:
            cognito.CfnUserPoolUser(
                self,
                f"CognitoUser-{email.replace('@', '-').replace('.', '-')}",
                user_pool_id=user_pool.user_pool_id,
                username=email,
                desired_delivery_mediums=["EMAIL"],
                force_alias_creation=True,
                user_attributes=[
                    cognito.CfnUserPoolUser.AttributeTypeProperty(
                        name="email", value=email
                    ),
                    cognito.CfnUserPoolUser.AttributeTypeProperty(
                        name="email_verified", value="true"
                    ),
                ],
            )

    def _add_nag_suppressions(self):
        """Add cdk-nag suppressions for acceptable findings"""

        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard permissions from CDK-generated resources",
                    "appliesTo": [
                        "Resource::*",
                    ],
                },
            ],
        )
