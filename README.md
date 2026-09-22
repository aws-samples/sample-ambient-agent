# Multi-Agent Platform

A production-ready platform for managing and orchestrating multiple Bedrock Agent Core agents with automated task execution, conversation continuity, and ambient signal processing. Built with React, AWS CDK, and serverless architecture.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Detailed Deployment Guide](#detailed-deployment-guide)
- [Configuration Files](#configuration-files)
- [Agent Development](#agent-development)
- [Architecture](#architecture)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Maintenance](#maintenance)

## Features

### Core Capabilities

- **Agent Management**: Register and manage multiple Bedrock Agent Core agents
- **Task Automation**: Schedule and execute agent tasks with human-in-the-loop support
- **Conversation Continuity**: Maintain conversation context across task executions
- **Ambient Signals**: Trigger agents based on S3 events and other signals
- **Task Scheduling**: Automated task execution with cron-like scheduling
- **Standalone Chat**: Dedicated `/chat` page with a ChatGPT-style interface for
  free-form conversations with any registered agent, independent of jobs.
  Threads are listed in a sidebar, messages render as GitHub-flavoured
  Markdown, and the composer supports Enter-to-send.
- **Interactive Job Chat**: Every job's detail page now exposes the same
  chat surface on its `Chat` tab. Sending a message from that tab resumes
  the job (or answers an `awaiting_human` interrupt), with the human turn
  persisted synchronously so the UI never flickers.
- **Real-time Updates**: SSE support for live task status updates


### Technical Features

- **Authentication**: AWS Cognito with automatic user creation
- **Serverless**: Fully serverless architecture with Lambda and DynamoDB
- **Modern UI**: React with CloudScape Design System
- **CI/CD Ready**: Automated build and deployment process
- **Cross-Platform**: Works on Windows, Mac, and Linux

## Prerequisites

Before starting, ensure you have:

- AWS CLI configured with appropriate credentials
- AWS Account ID and default region noted
- Node.js 20+ and npm installed
- Python 3.11+ installed
- AWS CDK CLI installed (`npm install -g aws-cdk`)
- AgentCore CLI installed (`npm install -g @aws/agentcore@latest`) — this
  replaces the older Python `bedrock-agentcore-starter-toolkit`
  (`agentcore configure`/`agentcore launch`), which AWS has superseded.
  If a plain `agentcore --version` errors instead of printing a version,
  an old Python `agentcore` is shadowing the npm one on your PATH; run
  `pip uninstall bedrock-agentcore-starter-toolkit` and open a new shell.
- Git Bash or similar Unix-like shell (for Windows users)

## Quick Start

### 1. Configure Backend

```bash
cd backend
cp config.example.yml config.yml
# Edit config.yml with your email and settings
```

### 2. Build Frontend

```bash
cd ../frontend
npm install
npm run build
```

### 3. Deploy Infrastructure

```bash
cd ../backend
pip install -r requirements.txt
cdk bootstrap  # First time only
cdk deploy --all
```

### 4. Deploy Agent

```bash
cd ../agent
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env`:

- `AWS_ACCOUNT_ID` / `AWS_DEFAULT_REGION` — your account and region.
- `AGENT_S3_BUCKET_NAME` — copy from the backend stack's `SignalUploadsBucketName`
  output (step 3). Scopes the agent's S3 read tool and IAM role to this single
  bucket instead of the whole account.

Edit `config.yaml`:

- `aws.bedrock.region_name` — set to the same region you deployed the backend
  stack to. Bedrock Guardrails are region-scoped, so a mismatch here causes
  `ValidationException: The guardrail identifier or version provided in the
  request does not exist` at invoke time.
- `aws.bedrock.guardrail_id` / `aws.bedrock.guardrail_version` — `deploy_agent.sh`
  resolves these from the backend stack's `AgentGuardrailId` /
  `AgentGuardrailVersion` outputs and writes them into `config.yaml`
  automatically on every deploy (pass `--skip-guardrail-sync` to manage them
  by hand instead). The agent refuses to start without either value set —
  see [Troubleshooting](#troubleshooting) if you need to run unguarded for
  local development.
- Customize model, tools, and prompts as needed.

```bash
./deploy_agent.sh
```

On first run this bootstraps the sibling AgentCore CLI project at
`../AmbientAgent` and creates a scoped IAM execution role automatically —
see `agent/README.md` for details. No manual `agentcore create` step or
IAM role needed beforehand.

### 5. Access Application

Open the CloudFront URL from step 3, log in with your Cognito credentials, and register your agent!

## Detailed Deployment Guide

### Part 1: Infrastructure and Frontend

#### Step 1: Configure Backend Settings

Navigate to the backend directory and create your configuration:

```bash
cd backend
cp config.example.yml config.yml
```

Edit `config.yml`:

```yaml
stack_name: bedrock-agent-core-agents
cloudfront_cache_disable: true # Set to false for production

monitoring:
  alert_email: your-email@example.com
  enable_cloudwatch_alerts: true

cognito:
  users:
    - user1@example.com
    - user2@example.com
```

#### Step 2: Build Frontend

```bash
cd ../frontend
npm install
npm run build
```

#### Step 3: Deploy Infrastructure

```bash
cd ../backend

# Install Python dependencies
pip install -r requirements.txt

# Bootstrap CDK (first time only)
cdk bootstrap

# Deploy all stacks
cdk deploy --all
```

**Expected Output**: After deployment completes, you'll see:

- `UserInterfaceDomainName`: Your CloudFront URL
- `MultiAgentApiEndpoint`: Your API Gateway URL
- `CognitoUsers`: List of created users
- `SignalUploadsBucketName`: The only S3 bucket ambient signals may watch — you'll need this in Step 4
- `AgentGuardrailId` / `AgentGuardrailVersion`: The Bedrock Guardrail applied to agent model calls — you'll need these in Step 4

### Part 2: Agent Deployment

#### Step 4: Configure Agent

```bash
cd ../agent

# Copy configuration files
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env`:

- `AWS_ACCOUNT_ID` / `AWS_DEFAULT_REGION` — your account and region.
- `AGENT_S3_BUCKET_NAME` — copy from the backend stack's `SignalUploadsBucketName`
  output (Part 1, Step 3). Scopes the agent's S3 read tool and IAM role to this
  single bucket instead of the whole account.

```bash
AWS_ACCOUNT_ID=123456789012
AWS_DEFAULT_REGION=us-east-1
AGENT_S3_BUCKET_NAME=react-starter-multiagent-signaluploadsbucket-xxxxxxxx
```

Edit `config.yaml`:

- `aws.bedrock.region_name` — must match the region the backend stack was
  deployed to (Part 1, Step 3). Bedrock Guardrails are region-scoped; a
  mismatch produces `ValidationException: The guardrail identifier or
  version provided in the request does not exist` at invoke time.
- `aws.bedrock.guardrail_id` / `aws.bedrock.guardrail_version` — `deploy_agent.sh`
  (Step 5) resolves these automatically from the backend stack's
  `AgentGuardrailId` / `AgentGuardrailVersion` outputs and writes them into
  `config.yaml` on every deploy; no manual copy needed. The agent refuses
  to start if they're unset (see Troubleshooting below for the local-dev
  opt-out).
- Customize model, tools, and prompts as needed.

#### Step 5: Deploy Agent

```bash
cd ../agent
./deploy_agent.sh
```

On first run, this script:

1. Creates the sibling AgentCore CLI project at `../AmbientAgent` (a `byo`
   runtime pointed at this `agent/` directory) — equivalent to running
   `agentcore create` + `agentcore add agent` yourself, but automated.
2. Creates an IAM execution role trusted only by
   `bedrock-agentcore.amazonaws.com` for this account/region, with no
   permissions of its own, and pins it into the new project's
   `agentcore.json`.
3. Attaches the permissions from `agent/policies/*.json` (Bedrock invoke +
   guardrail, S3 read scoped to `AGENT_S3_BUCKET_NAME`, CloudWatch logs,
   plus the baseline every AgentCore runtime needs to boot — ECR image
   pull, workload identity token, X-Ray/CloudWatch metrics) to that role.
4. Runs `agentcore deploy`, packaging `agent/` as a CodeZip artifact and
   provisioning the AgentCore Runtime via CloudFormation.

Subsequent runs reuse the same project and role, re-attaching the policy in
case `agent/policies/*.json` changed. Pass `--role-arn <arn>` to use an
existing role instead, or `--project-dir <path>` if you've placed the
AgentCore project somewhere other than `../AmbientAgent`.

**Copy the Agent Runtime ARN from the output!**

#### Step 6: Register Agent in Platform

1. Open the CloudFront URL in your browser
2. Log in with Cognito credentials (check email for temp password).
   MFA is required: on first sign-in you'll be walked through TOTP
   setup — scan the QR code with an authenticator app (e.g. Google
   Authenticator, Authy) and enter the 6-digit code. Subsequent
   sign-ins ask for the current code.
3. Navigate to **Agents** page
4. Click **"Register New Agent"**
5. Fill in:
   - Agent Name: "My Agent"
   - Agent Runtime ARN: (from step 5)
   - Description: What your agent does
6. Click **"Register Agent"**

## Configuration Files

### Required Files to Copy

| Template File                | Copy To              | Purpose                      |
| ---------------------------- | -------------------- | ---------------------------- |
| `backend/config.example.yml` | `backend/config.yml` | Stack settings, email, users |
| `agent/.env.example`         | `agent/.env`         | AWS Account ID, region       |
| `agent/config.example.yaml`  | `agent/config.yaml`  | Agent configuration          |

### Auto-Generated Files (Don't Create Manually)

- `AmbientAgent/` - The AgentCore CLI project (sibling of `agent/`); created
  automatically on first run of `deploy_agent.sh` (see Step 5 above).
  Contains only deployment tooling (`agentcore.json`, the CDK app that
  provisions the runtime, `.cli/` state) — never your agent's source code,
  which stays in `agent/` and is referenced via `codeLocation`.
- `agent/.venv/` - Local virtual environment, if you created one

## Agent Development

The reference agent is built on `langchain.agents.create_agent`
(LangGraph). This is the supported replacement for the legacy
`create_react_agent` + `AgentExecutor` pipeline, which LangChain has
deprecated. Behaviour is otherwise unchanged for existing deployments:
the agent still exposes the same entry point, the same tool factory,
and the same `config.yaml` surface. Two things are worth knowing when
writing or modifying tools:

- The agent is a compiled LangGraph at construction time and stateless
  across invocations; session state is tracked by the platform wrapper
  in `core/agent_core.py` and injected as message history on each call.
- Tool exceptions are caught by the graph rather than propagated to
  the invoker. `tools/human_input.py` uses a sentinel return value
  (not an exception) to signal that human input is required.

### Project Structure

```
agent/
├── agent.py                 # Entry point
├── config.example.yaml      # Configuration template
├── requirements.txt         # Dependencies (pip/local dev)
├── pyproject.toml           # Dependencies (required by the AgentCore CLI's
│                             # CodeZip build - kept in sync with requirements.txt)
├── deploy_agent.sh          # Deployment script (wraps `agentcore deploy`)
├── attach_agent_policies.sh # Attaches agent/policies/*.json to the execution role
├── .env.example             # Environment template
│
├── core/                    # Platform integration (rarely modified)
│   ├── agent_core.py       # create_agent wrapper + session state
│   ├── tool_factory.py     # Tool factory
│   └── execution_control.py # Execution control
│
└── tools/                   # Custom tools (frequently modified)
    ├── calculator.py        # Example tool
    ├── human_input.py       # Human-in-the-loop
    └── s3_reader.py         # S3 file reader

AmbientAgent/                # Sibling of agent/ - AgentCore CLI project only.
└── agentcore/
    ├── agentcore.json       # Runtime spec; codeLocation points at ../agent/
    ├── aws-targets.json     # Deployment target (account/region)
    └── cdk/                 # CDK app the CLI uses to provision the runtime
```


### Available Tools

**Calculator Tool**: Performs mathematical calculations

- Operations: `+`, `-`, `*`, `/`, `**`
- Functions: `sqrt`, `sin`, `cos`, `tan`, `log`, `abs`
- Constants: `pi`, `e`

**Human Input Tool**: Requests user input

- Pauses agent execution
- Presents question to user
- Resumes with response

**S3 Reader Tool**: Reads files from S3

- Processes text files
- Handles binary files
- Works with S3 signals

### Adding Custom Tools

#### 1. Create Tool File

Create `tools/my_custom_tool.py`:

```python
def create_my_tool_func():
    """Create the tool function"""
    def my_tool_wrapper(input_param: str) -> str:
        """
        Tool description for the agent.

        Args:
            input_param: Parameter description

        Returns:
            Result description
        """
        result = do_something(input_param)
        return f"Result: {result}"

    return my_tool_wrapper
```

#### 2. Update `tools/__init__.py`

```python
from .my_custom_tool import create_my_tool_func

__all__ = [
    'create_my_tool_func',
    # ... other tools
]
```

#### 3. Update `core/tool_factory.py`

Add tool creation method:

```python
def _create_my_custom_tool(self, tool_config: Dict[str, Any]) -> Tool:
    """Create my custom tool."""
    my_func = create_my_tool_func()
    return Tool(
        name=tool_config.get("name", "my_tool"),
        description=tool_config.get("description", "My custom tool"),
        func=my_func
    )
```

Add to `_create_tool` method:

```python
elif tool_type == "my_custom":
    return self._create_my_custom_tool(tool_config)
```

#### 4. Update `config.yaml`

```yaml
tools:
  my_custom_tool:
    enabled: true
    type: "my_custom"
    name: "my_tool"
    description: "What my tool does"
```

### Customizing Agent Behavior

Edit `config.yaml`:

```yaml
agent:
  verbose: true # Enable detailed logging
  max_iterations: 10 # Maximum reasoning steps
  handle_parsing_errors: true # Gracefully handle errors

prompts:
  system_template: |
    Your custom system prompt here...
```

## Architecture

### High-Level Overview

```
┌─────────────────┐
│   CloudFront    │ ← User accesses application
└────────┬────────┘
         │
         ├─→ S3 Bucket (Frontend)
         │
         └─→ API Gateway

┌─────────────────┐
│ Multi-Agent API │
└────────┬────────┘
         │
         ├─→ Agent Management Lambda
         ├─→ Task Management Lambda
         ├─→ Task Execution Lambda
         ├─→ Conversation Management Lambda
         ├─→ Signal Management Lambda
         └─→ Signal Processor Lambda

┌─────────────────┐
│   DynamoDB      │
└─────────────────┘
         │
         ├─→ Agent Registry
         ├─→ Task Registry
         ├─→ Conversation Store
         └─→ Ambient Signals
```

### Components

- **Frontend**: React app with Vite.js and CloudScape Design
- **Backend**: Python Lambda functions with API Gateway
- **Storage**: DynamoDB tables for agents, tasks, and conversations
- **Authentication**: AWS Cognito with automatic user provisioning
- **Infrastructure**: AWS CDK (Python)
- **Scheduling**: EventBridge for task scheduling

## Testing

### Test 1: Create and Execute a Job

1. Navigate to **Jobs** page
2. Click **"Create New Job"**
3. Fill in details:
   - Job Name: "Test Job"
   - Agent: Select your agent
   - Prompt: "What are the benefits of AWS Lambda?"
4. Click **"Execute"**
5. Monitor status: idle → busy → completed
6. View results and conversation history

### Test 2: Chat with an Agent

1. Navigate to **Chat** in the left sidebar.
2. Click **"New chat"**, pick your registered agent, and click **Start chat**.
3. Type a message and press Enter. The reply renders as Markdown in a
   left-aligned bubble; your message appears right-aligned.
4. You can also open any job on the **Jobs** page and use the **Chat**
   tab to continue that job's conversation. For jobs that are
   `awaiting_human`, the message you send is forwarded as the human
   response and the job resumes automatically.

### Test 3: Create an S3 Signal

Ambient signals can only watch the single, stack-owned signal-uploads
bucket — the `SignalUploadsBucketName` output from Part 1, Step 3 — not an
arbitrary bucket of your choosing. The Signals page's bucket field is
pre-filled and read-only for this reason (see `THREAT_MODEL.md`).

#### Configure Signal

1. Navigate to **Signals** page
2. Click **"Create New Signal"**
3. Fill in:
   - Signal Name: "Document Processor"
   - Agent: Select your agent
   - S3 Bucket: pre-filled with the platform's signal-uploads bucket
   - Prefix: `documents/` (optional)
   - Suffix: `.txt` (optional)
4. Enable signal

#### Test Signal

```bash
# Create test document
echo "AWS Lambda is a serverless compute service..." > test.txt

# Upload to the SAME bucket shown in the Signals page (get the exact name
# from the SignalUploadsBucketName stack output if unsure)
aws s3 cp test.txt s3://<signal-uploads-bucket-name>/documents/test.txt
```

Check the **Jobs** page - a new job should be automatically created!

## Troubleshooting

### Agent Deployment Fails

**Solution**:

1. Check AWS credentials: `aws sts get-caller-identity`
2. Confirm the AgentCore CLI is on PATH and not shadowed by the old Python
   toolkit: `agentcore --version` should print a version, not an error. If
   it errors, `pip uninstall bedrock-agentcore-starter-toolkit` and open a
   new shell.
3. Run `agentcore validate` from `AmbientAgent/` to catch config errors
   before deploying.
4. `CREATE_FAILED ... OpenTelemetry instrumentation executable not found`
   means `agent/pyproject.toml` is missing `aws-opentelemetry-distro` —
   the AgentCore Runtime needs it to run `opentelemetry-instrument`.
5. Verify IAM permissions on the pinned `executionRoleArn` and on your own
   credentials (CDK bootstrap role assumption) — see
   `AmbientAgent/agentcore/.cli/logs/import/` or `deploy/` for detailed logs.
6. Check deployment logs via `agentcore logs --runtime ambient`.

### Agent Says S3 Permissions Are Not Configured

If the agent responds with something like "the necessary S3 bucket
permissions haven't been configured," the cause is usually not IAM: the
agent's S3 tool enforces its own in-process bucket allowlist, read from
`AGENT_S3_BUCKET_NAME` / `ALLOWED_S3_BUCKETS` in the **deployed
runtime's** environment, and it refuses every request when neither is
set (fail-closed). `deploy_agent.sh` syncs these from `agent/.env` into
the runtime's `envVars` in `agentcore.json` on every deploy — if you
hit this, make sure `AGENT_S3_BUCKET_NAME` is set in `agent/.env`
(to the `SignalUploadsBucketName` stack output) and re-run
`./deploy_agent.sh`.

### Agent Fails to Start with `GuardrailNotConfiguredError`

The agent refuses to start if `aws.bedrock.guardrail_id`/`guardrail_version`
are unset in `config.yaml` — it never runs unguarded against untrusted,
model-facing input (uploaded files, signal metadata) without an explicit
decision to do so.

**Solution**:

1. Normally nothing to do — `deploy_agent.sh` resolves these automatically
   from the backend stack's `AgentGuardrailId`/`AgentGuardrailVersion`
   outputs on every deploy. This error means that sync didn't run or
   couldn't find the stack (e.g. wrong region, backend stack not deployed
   yet, or you passed `--skip-guardrail-sync`).
2. Re-run `./deploy_agent.sh` (without `--skip-guardrail-sync`) once the
   backend stack is deployed in the same account/region as `AWS_DEFAULT_REGION`
   in `agent/.env`.
3. To run unguarded for local development only, set
   `ALLOW_UNGUARDED_AGENT=true` in the environment before starting the
   agent process. Do not use this for any real deployment — nothing then
   filters prompt-injection attempts in uploaded file content or signal
   metadata before it reaches the model.

### Jobs Stay in "Busy" Status

**Solution**:

1. Check CloudWatch logs for the agent
2. Verify agent runtime ARN is correct
3. Ensure agent has necessary IAM permissions
4. Check if agent container is running

### Signal Not Triggering

**Solution**:

1. Confirm you uploaded to the exact `SignalUploadsBucketName` bucket, not a
   different bucket with a similar name — the stack also creates an
   access-logs bucket for it whose name looks similar at a glance; only
   the one shown in the Signals page / stack output actually has the
   notification wired to the signal processor.
2. Check signal is enabled (Status: Active)
3. Ensure file matches prefix/suffix filters
4. Check CloudWatch logs for signal processor
5. Verify the notification is actually installed: `aws s3api
   get-bucket-notification-configuration --bucket <bucket-name>` should
   list a `LambdaFunctionConfigurations` entry for your signal.

### CORS Errors in Browser

**Solution**:

1. Clear browser cache
2. Check API Gateway CORS settings
3. Redeploy frontend: `cd frontend && npm run build && cd ../backend && cdk deploy --all`

## Maintenance

### Update Frontend

```bash
cd frontend
npm run build
cd ../backend
cdk deploy bedrock-agent-core-agents
```

### Update Backend

```bash
cd backend
cdk deploy --all
```

### Update Agent

```bash
cd agent
./deploy_agent.sh
```

### View Logs

```bash
# Agent runtime logs (via the AgentCore CLI, from AmbientAgent/)
(cd AmbientAgent && agentcore logs --runtime ambient --follow)

# Platform API access logs
aws logs tail /aws/apigateway/bedrock-agent-core-agents-MultiAgent --follow

# Lambda function logs
aws logs tail /aws/lambda/bedrock-agent-core-agents-MultiAgent-JobExecution --follow
```

### Cleanup

To remove all AWS resources:

```bash
cd backend
cdk destroy --all
```

**Warning**: This deletes all data including DynamoDB tables!

## Additional Resources

- [AWS Bedrock Agent Core Documentation](https://docs.aws.amazon.com/bedrock/)
- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [LangChain Documentation](https://python.langchain.com/)

## Support

For issues or questions:

1. Check CloudWatch logs for detailed error messages
2. Review the troubleshooting section above
3. Verify all prerequisites are met
4. Check AWS service quotas and limits
5. Ensure all `.example` files have been copied and configured

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
