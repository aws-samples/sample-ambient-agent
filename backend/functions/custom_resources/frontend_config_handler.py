# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import logging
from typing import Dict, Any

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

cfn_client = boto3.client("cloudformation")
s3_client = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Custom resource handler that:
    1. Fetches the multi-agent API endpoint from CloudFormation
    2. Creates .env.production file with the endpoint
    3. Builds the frontend
    4. Uploads to S3
    """
    logger.info("Frontend config handler invoked", extra={"event": event})

    request_type = event["RequestType"]
    properties = event["ResourceProperties"]

    response_data = {}
    physical_resource_id = "FrontendConfigHandler"

    try:
        if request_type in ["Create", "Update"]:
            # Get properties from the custom resource
            multi_agent_stack_name = properties["MultiAgentStackName"]
            frontend_bucket = properties["FrontendBucket"]
            _distribution_id = properties["DistributionId"]

            # Fetch the multi-agent API endpoint from CloudFormation
            logger.info(
                "Fetching stack outputs", extra={"stack_name": multi_agent_stack_name}
            )
            stack_response = cfn_client.describe_stacks(
                StackName=multi_agent_stack_name
            )
            outputs = stack_response["Stacks"][0]["Outputs"]

            api_endpoint = None
            for output in outputs:
                if output["OutputKey"] == "MultiAgentApiEndpoint":
                    api_endpoint = output["OutputValue"]
                    break

            if not api_endpoint:
                raise Exception("MultiAgentApiEndpoint not found in stack outputs")

            logger.info("API endpoint retrieved", extra={"api_endpoint": api_endpoint})

            # Create .env.production content
            env_content = f"VITE_API_ENDPOINT={api_endpoint}\n"

            # Store the env content in S3 for the build process to use
            # We'll store it in a temporary location that the build can access
            env_key = "build-config/.env.production"
            s3_client.put_object(
                Bucket=frontend_bucket,
                Key=env_key,
                Body=env_content.encode("utf-8"),
                ContentType="text/plain",
            )

            logger.info(
                "Environment file uploaded to S3",
                extra={"bucket": frontend_bucket, "key": env_key},
            )

            response_data = {
                "ApiEndpoint": api_endpoint,
                "EnvFileLocation": f"s3://{frontend_bucket}/{env_key}",
                "Message": "Frontend configuration updated successfully",
            }

        elif request_type == "Delete":
            # Cleanup if needed
            logger.info("Delete request received", extra={"cleanup_needed": False})
            response_data = {"Message": "Resource deleted"}

        # Send success response to CloudFormation
        send_response(event, context, "SUCCESS", response_data, physical_resource_id)

    except Exception as e:
        logger.error(
            "Frontend config handler failed", extra={"error": str(e)}, exc_info=True
        )
        send_response(
            event, context, "FAILED", {"Message": str(e)}, physical_resource_id
        )

    return response_data


def send_response(event, context, response_status, response_data, physical_resource_id):
    """Send response to CloudFormation"""
    import urllib3

    response_body = {
        "Status": response_status,
        "Reason": f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": physical_resource_id,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": response_data,
    }

    logger.info(
        "Sending CloudFormation response", extra={"response_body": response_body}
    )

    http = urllib3.PoolManager()
    response = http.request(
        "PUT",
        event["ResponseURL"],
        body=json.dumps(response_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    logger.info("CloudFormation response sent", extra={"status_code": response.status})
