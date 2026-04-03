#!/bin/bash

# Agent Core Deployment Script
# Extracted from master deployment script
# Deploys only the Bedrock Agent Core component

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] ✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] ⚠${NC} $1"
}

log_error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ✗${NC} $1"
}

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check prerequisites for agent deployment
check_prerequisites() {
    log "Checking prerequisites for agent deployment..."

    # Check for required commands
    local required_commands=("aws" "agentcore")
    for cmd in "${required_commands[@]}"; do
        if ! command_exists "$cmd"; then
            log_error "Required command '$cmd' not found. Please install it first."
            exit 1
        fi
    done

    # Check AWS credentials
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        log_error "AWS credentials not configured or invalid. Please run 'aws configure' first."
        exit 1
    fi

    # Check for .env file
    if [ ! -f ".env" ]; then
        log_error ".env file not found in project root. Please create it from env.example."
        exit 1
    fi

    log_success "Prerequisites check passed"
}

# Function to source environment variables
source_env() {
    log "Loading environment variables from .env..."
    if [ -f ".env" ]; then
        set -a
        source .env
        set +a
        log_success "Environment variables loaded"
        log "Using AWS Account ID: $AWS_ACCOUNT_ID"
        log "Using AWS Region: $AWS_DEFAULT_REGION"
    else
        log_error ".env file not found"
        exit 1
    fi
}

# Function to attach IAM policies to agent role
attach_agent_policies() {
    log "========================================="
    log "ATTACHING IAM POLICIES TO AGENT ROLE"
    log "========================================="

    # Save current directory
    local ORIGINAL_DIR="$(pwd)"

    # Navigate to agent directory if needed
    if [ ! -f "attach_agent_policies.sh" ]; then
        if [ -f "agent/attach_agent_policies.sh" ]; then
            cd agent
        else
            log_warning "Policy attachment script not found. Skipping policy attachment."
            log "To attach policies later, run: cd agent && ./attach_agent_policies.sh"
            return 0
        fi
    fi

    # Make script executable if not already
    chmod +x attach_agent_policies.sh 2>/dev/null || true

    # Run the policy attachment script
    log "Running policy attachment script..."
    if ./attach_agent_policies.sh; then
        log_success "Policies attached successfully"
    else
        log_warning "Policy attachment failed or was skipped"
        log "You can manually attach policies later by running: cd agent && ./attach_agent_policies.sh"
    fi

    # Return to original directory
    cd "$ORIGINAL_DIR"
}

# Function to deploy agent core
deploy_agent() {
    log "========================================="
    log "DEPLOYING AGENT CORE"
    log "========================================="

    # Get the current directory
    CURRENT_DIR="$(pwd)"
    log "Current directory: $CURRENT_DIR"

    # Check if we're already in an agent directory (contains agent.py)
    if [ -f "agent.py" ]; then
        log "Found agent.py in current directory - proceeding with deployment"
    else
        # Look for agent directory
        if [ -d "agent" ]; then
            log "Found agent directory, changing to it"
            cd agent
        else
            log_error "Could not find agent directory or agent.py file"
            log "Please run this script from the project root or from within the agent directory"
            exit 1
        fi
    fi

    log "Agent directory: $(pwd)"

    # Verify agent.py exists
    if [ ! -f "agent.py" ]; then
        log_error "agent.py not found in $(pwd)"
        exit 1
    fi

    # Check if agent is already configured
    if [ -f ".bedrock_agentcore.yaml" ]; then
        log_warning "Agent appears to be already configured. Checking current status..."

        # Extract current agent ARN from config for agent specifically
        CURRENT_AGENT_ARN=$(awk '/agents:/,/agent:/ {next} /agent:/,/ambient_agent:/ { if (/agent_arn:/) print $2; exit }' .bedrock_agentcore.yaml)
        if [ -n "$CURRENT_AGENT_ARN" ]; then
            log "Current agent ARN: $CURRENT_AGENT_ARN"
        fi
    fi

    log "Configuring agent..."
    agentcore configure -e agent.py

    log "Launching agent..."
    agentcore launch --auto-update-on-conflict

    # Extract the new agent ARN from the configuration
    if [ -f ".bedrock_agentcore.yaml" ]; then
        NEW_AGENT_ARN=$(grep "agent_arn:" .bedrock_agentcore.yaml | sed 's/.*agent_arn: *//' | tr -d ' ')
        if [ -n "$NEW_AGENT_ARN" ]; then
            log_success "Agent deployed successfully with ARN: $NEW_AGENT_ARN"

            # Export the ARN for use in verification
            export INVOKE_AGENT_ARN="$NEW_AGENT_ARN"
        else
            log_error "Failed to extract agent ARN from configuration"
            exit 1
        fi
    else
        log_error "Agent configuration file not found after deployment"
        exit 1
    fi

    # Stay in agent directory for policy attachment
    log_success "Agent Core deployment completed"
}

# Function to verify agent deployment
verify_agent_deployment() {
    log "========================================="
    log "VERIFYING AGENT DEPLOYMENT"
    log "========================================="

    # Check agent status
    log "Checking agent status..."
    if [ -n "$INVOKE_AGENT_ARN" ]; then
        log_success "Agent ARN: $INVOKE_AGENT_ARN"

        # Test if agent is accessible
        log "Testing agent accessibility..."
        if aws bedrock-agent get-agent --agent-id $(echo $INVOKE_AGENT_ARN | cut -d'/' -f2) >/dev/null 2>&1; then
            log_success "Agent is accessible and ready"
        else
            log_warning "Agent may not be fully ready yet (this is normal for new deployments)"
        fi
    else
        log_error "Agent ARN not found in environment"
        exit 1
    fi
}

# Function to display agent deployment summary
display_summary() {
    log "========================================="
    log "AGENT DEPLOYMENT SUMMARY"
    log "========================================="

    echo ""
    log_success "Agent Core deployed successfully!"
    echo ""

    if [ -n "$INVOKE_AGENT_ARN" ]; then
        echo -e "${GREEN}Agent ARN:${NC} $INVOKE_AGENT_ARN"
    fi

    echo ""
    log "Next steps:"
    echo "1. Copy the Agent Runtime ARN above to register in the platform UI"
    echo "2. Navigate to the Agents page in the web application"
    echo "3. Click 'Register New Agent' and paste the ARN"
    echo ""
    log "To test the agent directly:"
    echo "aws bedrock-agent-runtime invoke-agent --agent-id \$(echo $INVOKE_AGENT_ARN | cut -d'/' -f2) --agent-alias-id TSTALIASID --session-id test-session --input-text 'Hello, how are you?'"
    echo ""
}

# Function to handle cleanup on error
cleanup_on_error() {
    log_error "Agent deployment failed. Check the logs above for details."
    log "You may need to clean up partially deployed resources manually."
    exit 1
}

# Main deployment function
main() {
    log "========================================="
    log "AGENT CORE DEPLOYMENT SCRIPT"
    log "========================================="
    log "Starting agent deployment at $(date)"

    # Set up error handling
    trap cleanup_on_error ERR

    # Run deployment steps
    check_prerequisites
    source_env
    deploy_agent
    attach_agent_policies
    verify_agent_deployment
    display_summary

    log_success "Agent deployment completed successfully at $(date)"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Agent Core Deployment Script"
            echo "Deploys only the Bedrock Agent Core component"
            echo ""
            echo "Options:"
            echo "  --help, -h        Show this help message"
            echo ""
            echo "Prerequisites:"
            echo "  - AWS CLI configured with appropriate credentials"
            echo "  - agentcore CLI tool installed"
            echo "  - .env file present in project root"
            echo ""
            echo "This script will:"
            echo "  1. Check prerequisites"
            echo "  2. Configure the agent using agentcore"
            echo "  3. Launch the agent to AWS Bedrock"
            echo "  4. Update .env file with the agent ARN"
            echo ""
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run the main function
main

# Ensure clean exit
exit 0
