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
- Node.js 18+ and npm installed
- Python 3.11+ installed
- AWS CDK CLI installed (`npm install -g aws-cdk`)
- Docker installed and running (required for agent deployment)
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
# Edit both files with your AWS configuration

./deploy_agent.sh
```

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

### Part 2: Agent Deployment

#### Step 4: Configure Agent

```bash
cd ../agent

# Copy configuration files
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env`:

```bash
AWS_ACCOUNT_ID=123456789012
AWS_DEFAULT_REGION=us-east-1
INVOKE_AGENT_ARN=  # Leave blank
```

Edit `config.yaml` to customize your agent settings (model, tools, prompts, etc.)

#### Step 5: Deploy Agent

```bash
./deploy_agent.sh
```

This will:

- Build the agent Docker container
- Push to Amazon ECR
- Deploy using Bedrock Agent Core SDK
- Create necessary IAM roles

**Copy the Agent Runtime ARN from the output!**

#### Step 6: Register Agent in Platform

1. Open the CloudFront URL in your browser
2. Log in with Cognito credentials (check email for temp password)
3. Navigate to **Agents** page
4. Click **"Register New Agent"**
5. Fill in:
   - Agent Name: "My Agent"
   - Agent Runtime ARN: (from step 6)
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

- `agent/.bedrock_agentcore.yaml` - Created by agent deployment
- `agent/.bedrock_agentcore/` - Agent deployment artifacts

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
├── requirements.txt         # Dependencies
├── deploy_agent.sh          # Deployment script
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


#### Create S3 Bucket

```bash
aws s3 mb s3://my-agent-test-bucket-$(date +%s)
```

#### Configure Signal

1. Navigate to **Signals** page
2. Click **"Create New Signal"**
3. Fill in:
   - Signal Name: "Document Processor"
   - Agent: Select your agent
   - S3 Bucket: Your bucket name
   - Prefix: `documents/` (optional)
   - Suffix: `.txt` (optional)
4. Enable signal

#### Test Signal

```bash
# Create test document
echo "AWS Lambda is a serverless compute service..." > test.txt

# Upload to S3
aws s3 cp test.txt s3://your-bucket-name/documents/test.txt
```

Check the **Jobs** page - a new job should be automatically created!

## Troubleshooting

### Agent Deployment Fails

**Solution**:

1. Ensure Docker is running: `docker ps`
2. Check AWS credentials: `aws sts get-caller-identity`
3. Verify ECR and IAM permissions
4. Check deployment logs

### Jobs Stay in "Busy" Status

**Solution**:

1. Check CloudWatch logs for the agent
2. Verify agent runtime ARN is correct
3. Ensure agent has necessary IAM permissions
4. Check if agent container is running

### Signal Not Triggering

**Solution**:

1. Verify S3 bucket name is correct
2. Check signal is enabled (Status: Active)
3. Ensure file matches prefix/suffix filters
4. Check CloudWatch logs for signal processor

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
# Agent logs
aws logs tail /aws/lambda/bedrock-agentcore-agent --follow

# Platform logs
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
