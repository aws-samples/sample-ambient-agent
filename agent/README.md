# Example Agent for Multi-Agent Platform

This is a fully-functional example agent that demonstrates how to build agents compatible with the Multi-Agent Platform. It showcases all platform features including:

- ✅ Human-in-the-loop interruptions
- ✅ Conversation continuity across sessions
- ✅ S3 signal processing
- ✅ Job execution and management
- ✅ Modular tool architecture

## 🎯 Purpose

This agent serves two main purposes:

1. **Testing & Validation**: Deploy this agent to test the platform's features and ensure everything works correctly
2. **Reference Implementation**: Use this as a template for building your own custom agents

## 📁 Project Structure

```
agent/
├── agent.py                 # Entry point (imports from core)
├── config.example.yaml      # Configuration template
├── requirements.txt         # Python dependencies
├── deploy_agent.sh          # Deployment script
├── .env.example             # Environment variables example
├── README.md                # This file
│
├── core/                    # Platform integration (rarely modified)
│   ├── __init__.py
│   ├── agent_core.py       # Main agent implementation
│   ├── tool_factory.py     # Tool factory
│   └── execution_control.py # Execution state management
│
└── tools/                   # Custom tools (frequently modified)
    ├── __init__.py
    ├── calculator.py        # Example: Calculator tool
    ├── human_input.py       # Platform: Human-in-the-loop tool
    └── s3_reader.py         # Platform: S3 file reader tool
```

## 🔧 Core Components

### Entry Point (`agent.py`)

- Minimal entry point that imports from core package
- Clean separation between entry point and implementation
- **Rarely needs modification**

### Core Package (`core/` directory)

Contains platform integration code that rarely needs changes:

#### `core/agent_core.py`

- Main agent implementation with BedrockAgentCore integration
- Handles platform features (sessions, interruptions, signals)
- Manages conversation history and execution state
- **Platform code - typically no changes needed**

#### `core/tool_factory.py`

- Factory for creating tools from configuration
- Dynamically loads and configures tools
- **Platform code - typically no changes needed**

#### `core/execution_control.py`

- Tracks execution metrics
- Prevents infinite loops with circuit breakers
- Manages execution state
- **Platform code - typically no changes needed**

### Tools Package (`tools/` directory)

Contains custom tools - **this is where you add your functionality**:

- **Modular design**: Each tool is a separate file
- **Easy to extend**: Add new tools by creating new files
- **Configuration-driven**: Enable/disable tools via config.yaml
- **Clear purpose**: Tools are the main customization point

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd agent
pip install -r requirements.txt
```

### 2. Configure the Agent

```bash
# Copy the template
cp config.example.yaml config.yaml

# Edit config.yaml to customize:
# - AWS Bedrock model settings
# - Tool configurations
# - Agent behavior
```

### 3. Set Up Environment

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your AWS credentials
```

### 4. Deploy the Agent

```bash
# Deploy to AWS Lambda
./deploy_agent.sh
```

### 5. Register with Platform

After deployment, register the agent in the platform UI:

- Agent Name: "Example Agent"
- Agent ARN: (from deployment output)
- Agent Type: Choose based on your use case
- Description: "Example agent with calculator, S3, and human input tools"

## 🛠️ Available Tools

### Calculator Tool

Performs mathematical calculations with support for:

- Basic operations: `+`, `-`, `*`, `/`, `**`
- Functions: `sqrt`, `sin`, `cos`, `tan`, `log`, `abs`, etc.
- Constants: `pi`, `e`

**Example**: "Calculate sqrt(16) + 2 \*\* 3"

### Human Input Tool

Requests clarification or input from users:

- Interrupts agent execution
- Presents question to user
- Resumes with user's response

**Example**: Agent asks "Which format do you prefer: PDF or CSV?"

### S3 Reader Tool

Reads files from S3 buckets:

- Processes text files
- Handles binary files
- Works with S3 signals

**Example**: Automatically processes files uploaded to monitored S3 buckets

## 📝 Adding Custom Tools

### Step 1: Create Tool File

Create `tools/my_custom_tool.py`:

```python
"""
My custom tool description
"""

def create_my_tool_func():
    """Create the tool function for LangChain"""
    def my_tool_wrapper(input_param: str) -> str:
        """
        Tool description that the agent will see.

        Args:
            input_param: Description of the parameter

        Returns:
            Result description
        """
        # Your tool logic here
        result = do_something(input_param)
        return f"Result: {result}"

    return my_tool_wrapper
```

### Step 2: Update `tools/__init__.py`

```python
from .my_custom_tool import create_my_tool_func

__all__ = [
    # ... existing tools ...
    'create_my_tool_func',
]
```

### Step 3: Update `tool_factory.py`

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

### Step 4: Add to Configuration

Update `config.yaml`:

```yaml
tools:
  my_custom_tool:
    enabled: true
    type: "my_custom"
    name: "my_tool"
    description: "Description of what my tool does"
```

## 🔄 Platform Features

### Conversation Continuity

The agent maintains conversation history within each session:

- In-memory conversation storage per session
- Context preserved during job execution
- Session-based state management

### Human-in-the-Loop

Use the `ask_human` tool to request user input:

- Agent execution pauses
- User receives notification
- Agent resumes with user's response

### S3 Signal Processing

When files are uploaded to monitored S3 buckets:

- Signal automatically triggers the agent
- File information passed to agent
- Agent can read and process the file

### Execution Metrics

The platform tracks:

- Execution time
- Tool usage
- Human interactions
- Errors and loops

## 🎨 Customization Guide

### Modify Agent Behavior

Edit `config.yaml`:

```yaml
agent:
  verbose: true # Enable detailed logging
  max_iterations: 10 # Maximum reasoning steps
  handle_parsing_errors: true # Gracefully handle errors
```

### Customize System Prompt

Edit the `prompts.system_template` in `config.yaml` to change how the agent behaves and responds.

### Enable/Disable Tools

```yaml
tools:
  calculator:
    enabled: false # Disable calculator

  my_custom_tool:
    enabled: true # Enable custom tool
```

## 📊 Testing

### Local Testing

```python
# test_agent.py
from agent import agent

result = agent.invoke({
    "prompt": "Calculate 2 + 2",
    "session_id": "test-session"
})

print(result)
```

### Platform Testing

1. Deploy the agent
2. Register in platform UI
3. Create a job with the agent
4. Execute and monitor results

## 🐛 Troubleshooting

### Agent Not Responding

- Check CloudWatch logs for errors
- Verify AWS credentials
- Ensure Bedrock model access

### Tools Not Working

- Verify tool is enabled in config.yaml
- Check tool implementation for errors
- Review tool descriptions for clarity

### Conversation Not Persisting

- Check database file permissions
- Verify conversation_store.py is working
- Review session_id consistency

## 📚 Additional Resources

- [Platform Documentation](../docs/README.md)
- [Bedrock AgentCore Docs](https://github.com/awslabs/bedrock-agentcore)
- [LangChain Documentation](https://python.langchain.com/)

## 🤝 Contributing

To improve this example agent:

1. Keep it simple and well-documented
2. Add useful example tools
3. Maintain platform compatibility
4. Update this README with changes

## 📄 License

This example agent is part of the Multi-Agent Platform project.
