# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import os
from datetime import datetime
from typing import Dict, Any, List
from aws_lambda_powertools import Logger

logger = Logger(service="scheduler", level="INFO")

dynamodb = boto3.resource("dynamodb")
sqs_client = boto3.client("sqs")

TASK_REGISTRY_TABLE = os.environ["TASK_REGISTRY_TABLE"]
JOB_EXECUTION_QUEUE_URL = os.environ.get("JOB_EXECUTION_QUEUE_URL", "")
REGION = os.environ["REGION"]


# Get DynamoDB table
task_table = dynamodb.Table(TASK_REGISTRY_TABLE)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Scheduler Lambda function

    This function runs every minute via CloudWatch Events and:
    1. Scans for scheduled jobs that are due to run
    2. Invokes the job execution function for each due job
    3. Updates the next run time for recurring jobs
    """

    try:
        logger.info("Scheduler execution started")

        # Get current time
        now = datetime.utcnow().isoformat()

        # Find jobs that are due to run
        due_tasks = find_due_tasks(now)

        logger.info("Due jobs found", extra={"job_count": len(due_tasks)})

        # Process each due job
        results = []
        for job in due_tasks:
            try:
                result = process_due_task(job, now)
                results.append(result)
            except Exception as e:
                logger.error(
                    "Error processing job",
                    extra={"job_id": job["jobId"], "error": str(e)},
                )
                results.append(
                    {"jobId": job["jobId"], "status": "error", "error": str(e)}
                )

        logger.info("Scheduler completed", extra={"processed_count": len(results)})

        return {
            "statusCode": 200,
            "body": json.dumps({"processedTasks": len(results), "results": results}),
        }

    except Exception as e:
        logger.error(
            "Scheduler execution failed", extra={"error": str(e)}, exc_info=True
        )
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def find_due_tasks(current_time: str) -> List[Dict[str, Any]]:
    """Find scheduled jobs that are due to run"""
    try:
        # Query the GSI for scheduled jobs
        response = task_table.query(
            IndexName="jobType-nextRun-index",
            KeyConditionExpression="jobType = :jobType AND nextRun <= :currentTime",
            FilterExpression="#status IN (:idle, :interrupted) AND schedule.enabled = :enabled",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":jobType": "scheduled",
                ":currentTime": current_time,
                ":idle": "idle",
                ":interrupted": "interrupted",
                ":enabled": True,
            },
        )

        return response.get("Items", [])

    except Exception as e:
        logger.error("Due jobs search failed", extra={"error": str(e)}, exc_info=True)
        return []


def process_due_task(job: Dict[str, Any], current_time: str) -> Dict[str, Any]:
    """Process a single due job"""
    job_id = job["jobId"]

    try:
        logger.info("Processing due job", extra={"job_id": job_id})

        # Update job status to prevent duplicate execution
        update_task_status(job_id, "scheduled_for_execution")

        if not JOB_EXECUTION_QUEUE_URL:
            raise RuntimeError("JOB_EXECUTION_QUEUE_URL not configured")

        # Enqueue onto the job execution queue; the worker Lambda drains it.
        message_body = json.dumps(
            {
                "jobId": job_id,
                "scheduledExecution": True,
                "scheduledAt": current_time,
            }
        )
        response = sqs_client.send_message(
            QueueUrl=JOB_EXECUTION_QUEUE_URL,
            MessageBody=message_body,
        )


        # Update next run time for recurring jobs
        schedule = job.get("schedule", {})
        if schedule.get("type") == "recurring":
            next_run = calculate_next_run(schedule)
            if next_run:
                update_next_run(job_id, next_run)
                logger.info(
                    "Next run updated", extra={"job_id": job_id, "next_run": next_run}
                )
            else:
                # If we can't calculate next run, disable the schedule
                disable_task_schedule(job_id)
                logger.warning(
                    "Schedule disabled - could not calculate next run",
                    extra={"job_id": job_id},
                )
        else:
            # For one-time jobs, mark as completed after scheduling
            update_task_status(job_id, "scheduled_for_execution")

        return {
            "jobId": job_id,
            "status": "scheduled",
            "messageId": response.get("MessageId"),
        }


    except Exception as e:
        logger.error("Error processing job", extra={"job_id": job_id, "error": str(e)})
        # Reset job status on error
        update_task_status(job_id, "idle")
        raise e


def calculate_next_run(schedule: Dict[str, Any]) -> str:
    """
    Calculate the next run time for a recurring job

    This mirrors the logic in task_management.py but focuses on recurring schedules
    """
    try:
        pattern = schedule.get("pattern", "").lower()

        if not schedule.get("enabled", True):
            return None

        now = datetime.utcnow()

        # Simple recurring patterns
        if "daily" in pattern:
            # Extract time if specified (e.g., "daily at 9am")
            if "at" in pattern:
                try:
                    time_part = pattern.split("at")[1].strip()
                    if "am" in time_part or "pm" in time_part:
                        # Parse time like "9am" or "2:30pm"
                        time_str = time_part.replace("am", "").replace("pm", "").strip()
                        if ":" in time_str:
                            hour, minute = map(int, time_str.split(":"))
                        else:
                            hour, minute = int(time_str), 0

                        if "pm" in time_part and hour != 12:
                            hour += 12
                        elif "am" in time_part and hour == 12:
                            hour = 0

                        # Schedule for next day at the specified time
                        from datetime import timedelta

                        next_run = now.replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )
                        # Always schedule for next day
                        next_run += timedelta(days=1)

                        return next_run.isoformat()
                except BaseException:
                    pass

            # Default to next day at current time
            from datetime import timedelta

            return (now + timedelta(days=1)).isoformat()

        elif "hourly" in pattern or "hour" in pattern:
            # Extract interval if specified (e.g., "every 6 hours")
            hours = 1
            if "every" in pattern:
                try:
                    parts = pattern.split()
                    for i, part in enumerate(parts):
                        if part.isdigit():
                            hours = int(part)
                            break
                except BaseException:
                    pass

            from datetime import timedelta

            return (now + timedelta(hours=hours)).isoformat()

        elif "weekly" in pattern:
            from datetime import timedelta

            return (now + timedelta(weeks=1)).isoformat()

        elif "minute" in pattern:
            # For testing - every N minutes
            minutes = 5  # Default to 5 minutes
            if "every" in pattern:
                try:
                    parts = pattern.split()
                    for i, part in enumerate(parts):
                        if part.isdigit():
                            minutes = int(part)
                            break
                except BaseException:
                    pass

            from datetime import timedelta

            return (now + timedelta(minutes=minutes)).isoformat()

        # Default: schedule for 1 hour from now
        from datetime import timedelta

        return (now + timedelta(hours=1)).isoformat()

    except Exception as e:
        logger.error(
            "Next run calculation failed", extra={"error": str(e)}, exc_info=True
        )
        return None


def update_task_status(job_id: str, status: str):
    """Update job status in DynamoDB"""
    try:
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #status = :status, updatedAt = :updatedAt",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":updatedAt": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.error("Job status update failed", extra={"error": str(e)}, exc_info=True)


def update_next_run(job_id: str, next_run: str):
    """Update the next run time for a job"""
    try:
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET nextRun = :nextRun, schedule.nextRun = :nextRun, updatedAt = :updatedAt",
            ExpressionAttributeValues={
                ":nextRun": next_run,
                ":updatedAt": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.error(
            "Next run time update failed", extra={"error": str(e)}, exc_info=True
        )


def disable_task_schedule(job_id: str):
    """Disable a job's schedule"""
    try:
        task_table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET schedule.enabled = :enabled, updatedAt = :updatedAt",
            ExpressionAttributeValues={
                ":enabled": False,
                ":updatedAt": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.error(
            "Job schedule disable failed", extra={"error": str(e)}, exc_info=True
        )
